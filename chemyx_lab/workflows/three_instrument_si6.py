"""Shared, fail-closed three-instrument Si6 orchestration.

The needle position is *commanded*, not independently measured. A successful
STATUS proves controller state and limit inputs, not physical needle height.

Both entry points (``scripts/01_three_instrument_system_test.py`` and
``scripts/02_si6_experiment.py``) use this module, so the diagnostic and the
experiment exercise the same production interfaces.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Callable

from arduino.python.protocol import bool_field
from arduino.python.config import load_arduino_config
from arduino.python.needle_state import TrackedNeedle
from arduino.python.controller import NeedleController
from arduino.python.discovery import resolve_arduino_port
from arduino.python.errors import LiveExecutionBlocked, PositionUncertainError
from arduino.python.run_lock import PortProcessLock
from arduino.python.transport import SerialTransport
from arduino.mock.fake_arduino import FakeArduinoTransport

from .. import config
from ..analysis.nmr import build_magnitude_spectrum, read_jcamp_fid
from ..analysis.plot_titles import dataset_plot_title
from ..instruments.chemyx import Pump
from ..instruments.nmr import NmrRpcClient, NmrRpcConfig
from ..recovery import RecoveryClassification, inspect_run
from ..runtime_journal import JournalError
from ..runtime_state import replay_journal, write_json_atomic
from . import si6_automated_nmr as base
from . import pump_channels
from .dose_guard import DoseGuard
from ..analysis.stage_completion import completion_evidence
from ..analysis.stage_measurement import tracked_peak_measurement, tracked_peak_area_observation
from ..analysis.si6_stage_reports import write_stage_reports
from ..analysis.si6_final_qc import write_final_qc

# A real spectrum acquired through this workflow's iFlow acquisition path on
# 2026-08-10. Production process_fid finds the tracked resonance in it at
# 5.792 ppm, so every mock exercises the configured target window.
MOCK_NMR_FIXTURE = (
    config.REPO_ROOT / "chemyx_lab" / "testing" / "fixtures"
    / "tracked_resonance_phsi4_20260810.dx"
)
# The previous live run blocks the next one when it ended in one of these.
REVIEW_BLOCKING = {
    RecoveryClassification.MANUAL_INSPECTION_REQUIRED,
    RecoveryClassification.PHYSICAL_STATE_UNCERTAIN,
    RecoveryClassification.JOURNAL_CORRUPT,
}
STAGE_DECISIONS = {"CONTINUE": "continue", "ADVANCE": "advance", "ABORT": "abort", "C": "continue", "A": "advance", "Q": "abort"}


class VerificationError(RuntimeError):
    pass


class PhysicalStateUncertain(VerificationError):
    """Automatic recovery is refused because pump or needle state is unproven."""


class MeasurementFailedAfterCleanup(RuntimeError):
    """An NMR measurement failed; the physical cycle then finished normally."""

    def __init__(self, stage: str, cycle: int, failed_step: str, cause: BaseException) -> None:
        super().__init__(
            f"{failed_step} failed in {stage} cycle {cycle} "
            f"({type(cause).__name__}: {cause}); the sample was returned and "
            "the cleanup completed"
        )
        self.stage = stage
        self.cycle = cycle
        self.failed_step = failed_step
        self.cause = cause


@dataclass(frozen=True)
class RunIdentity:
    """What a run folder holds: ``si6``, ``diagnostic``, or ``processing_only``."""

    kind: str
    mock: bool
    selection: str | None = None

    @property
    def mode(self) -> str:
        return "mock" if self.mock else "live"

    @property
    def label(self) -> str:
        parts = [self.kind]
        if self.selection:
            parts.append(self.selection)
        # Processing an existing file contacts no instrument, so only the
        # simulated-fixture case carries a mode.
        if self.kind != "processing_only" or self.mock:
            parts.append(self.mode)
        return "_".join(parts)


def run_root(raw: dict[str, Any], identity: RunIdentity) -> Path:
    """Live results use output.run_root_dir; mocks use a sibling ``_mock`` folder."""
    root = config.resolve_repo_path(raw["output"]["run_root_dir"])
    return root.with_name(f"{root.name}_mock") if identity.mock else root


def create_identified_run(raw: dict[str, Any], identity: RunIdentity) -> base.RunPaths:
    return base.create_run_paths(run_root(raw, identity), label=identity.label)


def manifest_payload(paths: base.RunPaths, identity: RunIdentity, raw: dict[str, Any], figures: list[dict[str, Any]]) -> dict[str, Any]:
    """The run folder name is the dataset identity shown in every figure title."""
    return {
        "run_id": paths.run_dir.name,
        "run_kind": identity.kind,
        "mode": identity.mode,
        "diagnostic_selection": identity.selection,
        "dataset_display_name": paths.run_dir.name,
        "workflow_name": str(raw["workflow"]["name"]),
        "figures": figures,
    }


def cycle_values(raw: dict[str, Any]) -> dict[str, float]:
    """Read the five pump volumes and pause from the validated legacy YAML."""
    events = raw["workflow"]["cycle"]
    actions = [str(item["action"]).lower() for item in events]
    expected = ["withdraw", "operator", "withdraw", "pause", "nmr", "infuse", "operator", "withdraw", "infuse"]
    if actions != expected:
        raise ValueError("Three-instrument cycle requires withdraw, DOWN, withdraw, pause, NMR, infuse, UP, withdraw, infuse in that order")
    for index, position in ((1, "DOWN"), (6, "UP")):
        if events[index].get("position", position) != position:
            raise ValueError(f"Sampling cycle needle event {index + 1} must command {position}")
    if raw["workflow"].get("initial_needle_position", "UP") != "UP":
        raise ValueError("Sampling requires initial needle UP")
    return {
        "initial_withdraw_ml": float(events[0]["volume_ml"]),
        "sample_withdraw_ml": float(events[2]["volume_ml"]),
        "settle_seconds": float(events[3]["seconds"]),
        "return_infuse_ml": float(events[5]["volume_ml"]),
        "cleanup_withdraw_ml": float(events[7]["volume_ml"]),
        "cleanup_infuse_ml": float(events[8]["volume_ml"]),
    }


def positions(arduino_cfg: dict[str, Any], *, mock: bool) -> tuple[int, int, int]:
    if "needle" in arduino_cfg:
        needle, motion = arduino_cfg["needle"], arduino_cfg["motion"]
        speed = motion.get("maximum_speed_steps_s")
        if mock and speed is None:
            speed = 100
        if not isinstance(speed, int) or speed <= 0:
            raise ValueError("Configured needle maximum_speed_steps_s is required")
        return needle["up_position"], needle["down_position"], speed
    motion = arduino_cfg["motion"]
    up = motion.get("safe_up_position_steps")
    down = motion.get("sample_down_position_steps")
    if mock and down is None:
        down = motion.get("test_down_position_steps")
    speed = motion.get("maximum_speed_steps_s")
    if mock and (up is None or down is None or speed is None):
        return 100, 500, 300
    if not all(isinstance(value, int) and value > 0 for value in (up, down, speed)):
        raise ValueError("Commissioned Arduino UP/DOWN positions and motion speed are required")
    maximum = motion.get("maximum_travel_steps")
    if not isinstance(maximum, int) or not 0 < up < down < maximum:
        raise ValueError("Needle geometry must satisfy 0 < UP < DOWN < maximum travel")
    return up, down, speed


def verify_needle(needle: Any, expected_steps: int, *, require_homed: bool = True) -> dict[str, str]:
    status = needle.status()
    if bool_field(status, "moving") or status["fault"] != "NONE":
        raise VerificationError(f"Needle moving or faulted: {status}")
    if "logical_position" in status:
        if not bool_field(status, "position_valid"):
            raise VerificationError("Needle software position is uncertain; operator confirmation required")
        if int(status["logical_position"]) != expected_steps:
            raise VerificationError(f"Needle logical position {status['logical_position']} != {expected_steps}")
        return status
    if require_homed and (not bool_field(status, "homed") or not bool_field(status, "position_known")):
        raise VerificationError("Needle is not homed with a known commanded position")
    if bool_field(status, "limit_up") and bool_field(status, "limit_down"):
        raise VerificationError("Both needle limits report active")
    if int(status["commanded_position_steps"]) != expected_steps:
        raise VerificationError(f"Needle commanded position {status['commanded_position_steps']} != {expected_steps}")
    return status


def _process_fid_table(processed_dir: Path, suffix: str) -> Path:
    # process_fid shortens its filename prefixes to stay under the Windows path
    # limit, so its tables are found by their fixed suffix.
    matches = sorted(processed_dir.glob(f"*{suffix}"))
    if len(matches) != 1:
        raise base.AnalysisInconclusiveError(
            f"Expected one process_fid *{suffix} in {processed_dir.name}, found {len(matches)}"
        )
    return matches[0]


def _rows_for_file(table: Path, file_name: str) -> list[dict[str, str]]:
    with table.open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row.get("file") == file_name]


def _region_plot(processed_dir: Path, dx_path: Path) -> Path | None:
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", dx_path.stem).strip("_")
    matches = sorted((processed_dir / "plots" / "region").glob(f"*{safe}.png"))
    return matches[0] if len(matches) == 1 else None


def analyze_tracked_resonance(dx_path: Path, processed_dir: Path, paths: base.RunPaths, analysis: dict[str, Any], metadata: dict[str, Any]) -> tuple[dict, list[dict]]:
    """Take the measurement from production process_fid's tracked-window result.

    process_fid phases the FID, removes the solvent background with ALS, finds
    peaks on the real trace, and applies the peak-QC gates calibrated against
    operator-confirmed spectra (configs/nmr/analysis.yaml). Restricted to the
    configured window, its simple table holds the strongest QC-passing peak or
    a zero-filled row. The magnitude-spectrum detector is not used for this
    decision: it misses this resonance in most confirmed spectra.
    """
    simple = _rows_for_file(_process_fid_table(processed_dir, "peaks_simple.csv"), dx_path.name)
    window = _rows_for_file(_process_fid_table(processed_dir, "peak_qc_log_window.csv"), dx_path.name)
    if len(simple) != 1:
        raise base.AnalysisInconclusiveError(f"process_fid reported {len(simple)} tracked rows for {dx_path.name}")
    try:
        values = {key: float(simple[0][key]) for key in ("peak_ppm", "integrated_area", "intensity", "snr", "prominence_snr", "width_hz")}
    except (KeyError, TypeError, ValueError) as exc:
        raise base.AnalysisInconclusiveError(f"process_fid tracked row for {dx_path.name} is unreadable: {exc}") from exc
    if not all(math.isfinite(value) for value in values.values()):
        raise base.AnalysisInconclusiveError("Nonfinite production peak metrics cannot pass QC")
    target = float(metadata["target_ppm"])
    half_width = float(analysis["detection_window_ppm"])
    reasons = []
    if not any(row.get("qc_pass") == "True" for row in window) or values["snr"] <= 0:
        rejected = "; ".join(row.get("qc_failure_reasons") or "" for row in window if row.get("qc_pass") != "True")
        reasons.append("no QC-passing peak in the tracked window" + (f" (rejected: {rejected})" if rejected else ""))
    else:
        if abs(values["peak_ppm"] - target) > half_width:
            reasons.append(f"peak {values['peak_ppm']:.3f} ppm outside {target:g} +/- {half_width:g} ppm")
        if values["snr"] < float(analysis["min_peak_snr"]):
            reasons.append(f"snr {values['snr']:g} < {float(analysis['min_peak_snr']):g}")
        if values["prominence_snr"] < float(analysis["min_prominence_snr"]):
            reasons.append(f"prominence_snr {values['prominence_snr']:g} < {float(analysis['min_prominence_snr']):g}")
        minimum_area = max(float(analysis.get("min_peak_area", 0.0)), float(analysis.get("area_epsilon", 1e-12)))
        if values["integrated_area"] < minimum_area:
            reasons.append(f"area {values['integrated_area']:g} < {minimum_area:g}")
    try:
        width_ppm: float | str = values["width_hz"] / float(read_jcamp_fid(dx_path).metadata["$SF"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        width_ppm = ""
    row = dict(metadata)
    row.update({
        "file": dx_path.name, "peak_ppm": values["peak_ppm"],
        "peak_height": values["intensity"], "peak_area": values["integrated_area"],
        "snr": values["snr"], "prominence": "", "prominence_snr": values["prominence_snr"],
        "width_ppm": width_ppm, "baseline": "", "noise": "",
        "peak_clear": not reasons, "qc_failure_reasons": "; ".join(reasons),
        "rejected_candidate_count": sum(row.get("qc_pass") != "True" for row in window),
        "metric_source": "process_fid tracked window", "plot_file": "", "error": "",
    })
    region_plot = _region_plot(processed_dir, dx_path)
    if region_plot is not None:
        row["plot_file"] = str(region_plot.relative_to(paths.run_dir))
        row["plot_title"] = dataset_plot_title(dx_path.name, configured_name=str(metadata["dataset_display_name"]))
    spectrum = build_magnitude_spectrum(dx_path, line_broadening_hz=float(analysis.get("line_broadening_hz", 0.3)))
    spectrum_rows = [
        {"iteration": metadata["iteration"], "stage": metadata["stage"],
         "elapsed_hours": metadata["elapsed_hours"], "ppm": float(ppm), "magnitude": float(magnitude)}
        for ppm, magnitude in zip(spectrum.ppm_axis, spectrum.magnitude)
    ]
    return row, spectrum_rows


@dataclass
class Services:
    needle: Any
    pump: Any
    pump_cfg: config.PumpConfig
    nmr_cfg: config.NmrSettings
    raw: dict[str, Any]
    arduino_cfg: dict[str, Any]
    paths: base.RunPaths
    recorder: Any
    state: base.PumpSafetyState
    acquire: Callable[..., Path]
    process: Callable[..., Path]
    analyze: Callable[..., tuple[dict, list[dict]]]
    sleep: Callable[[str, float], None]
    identity: RunIdentity
    first_acquisition_at: datetime | None = None
    last_acquisition_started_at: datetime | None = None
    last_acquisition_completed_at: datetime | None = None
    last_analysis_completed_at: datetime | None = None
    plot_manifest: list[dict[str, Any]] | None = None
    # The NMR sub-step in progress, so a failure names exactly where it failed.
    measurement_step: str | None = None
    channel_configs: dict[int, config.PumpConfig] = field(default_factory=dict)
    channel_states: dict[int, base.PumpSafetyState] = field(default_factory=dict)
    simulation: Any = None
    dose_guard: Any = None
    transition_evidence: dict | None = None
    last_acquisition_completed_monotonic: float | None = None

    @property
    def states(self) -> dict[int, base.PumpSafetyState]:
        return self.channel_states or {self.pump_cfg.channel: self.state}

    def stop_all(self, *, stage: str | None = None, cycle: int | None = None) -> None:
        # Stop a possibly moving channel first. Failure on one never skips another.
        for ch, state in sorted(self.states.items(), key=lambda item: not item[1].motion_active):
            state.channel = ch
            base.attempt_emergency_stop(self.pump, state, self.recorder, workflow_phase=stage, cycle_number=cycle)

    @property
    def mock(self) -> bool:
        return self.identity.mock

    @property
    def dataset_name(self) -> str:
        return self.paths.run_dir.name

    def record(self, name: str, **fields: Any) -> dict:
        return self.recorder.record(name, **fields)

    def assert_pump_idle(self) -> None:
        for ch, state in self.states.items():
            if state.motion_active or state.uncertain or state.persistence_errors:
                raise VerificationError(f"Chemyx channel {ch} motion or physical-state uncertainty blocks movement")
            # The Chemyx protocol's STOP acknowledgement is the available idle evidence.
            if state.last_stop_status is not base.StopStatus.SUCCEEDED:
                raise VerificationError(f"Chemyx channel {ch} STOP has not been positively confirmed")

    def move_needle(self, label: str) -> dict[str, str]:
        self.assert_pump_idle()
        up, down, speed = positions(self.arduino_cfg, mock=self.mock)
        target = up if label == "UP" else down
        self.record("needle_transition", workflow_phase="needle", target=label, target_steps=target, result_classification="started")
        self.needle.move_absolute(target, speed)
        status = verify_needle(self.needle, target)
        self.record("needle_transition", workflow_phase="needle", target=label, target_steps=target, verified_status=status, result_classification="completed")
        return status

    def pump_move(self, direction: str, volume: float, needle_label: str, *, stage: str, cycle: int, channel: int | None = None, rate_ml_min: float | None = None) -> None:
        if direction not in {"withdraw", "infuse"}:
            raise ValueError("Pump direction must be withdraw or infuse")
        volume = pump_channels.positive(volume, "pump operation.volume_ml")
        if needle_label not in {"UP", "DOWN"}:
            raise ValueError("Pump operation needle position must be UP or DOWN")
        event = {"action": direction, "volume_ml": volume}
        if channel is not None:
            event["channel"] = channel
        if rate_ml_min is not None:
            event["rate_ml_min"] = rate_ml_min
        pump_channels.validate_operation(self.raw, event, "pump operation")
        ch = pump_channels.operation_channel(self.raw, event)
        cfg = (self.channel_configs or {self.pump_cfg.channel: self.pump_cfg})[ch]
        if rate_ml_min is not None:
            cfg = replace(cfg, rate=float(rate_ml_min))
        pump_channels.validate_rate(cfg.rate, cfg.units, f"channel {ch} operation")
        state = self.states[ch]
        self.assert_pump_idle()
        retained_after = state.retained_volume_ml + (volume if direction == "withdraw" else -volume)
        pump_channels.check_volume(ch, retained_after, pump_channels.channel_definitions(self.raw)[ch])
        up, down, _ = positions(self.arduino_cfg, mock=self.mock)
        status = verify_needle(self.needle, up if needle_label == "UP" else down)
        self.record("pump_needle_context", workflow_phase=stage, cycle_number=cycle, channel=ch, needle_state=needle_label, needle_status=status, operation_type=direction, requested_volume_ml=volume)
        # Explicitly select and configure every transfer. The driver caches units
        # globally, so configuring here also restores the correct validation units.
        self.pump.select_channel(ch)
        state.channel = ch
        try:
            base.configure_pump(self.pump, cfg)
            self.record("pump_configuration", workflow_phase=stage, cycle_number=cycle, channel=ch, units=config.UNITS[cfg.units], syringe_diameter_mm=cfg.diameter, rate=cfg.rate)
            base.run_safe_metered_move(
                self.pump, cfg, direction, volume, state,
                extra_seconds=float(self.raw["workflow"].get("pump_extra_seconds", 2.0)),
                sleep_fn=self.sleep, recorder=self.recorder,
                workflow_phase=stage, cycle_number=cycle,
            )
            state.retained_volume_ml = retained_after
            if direction == "withdraw":
                state.cumulative_withdrawn_ml += volume
            else:
                state.cumulative_infused_ml += volume
        finally:
            self.pump.select_channel(self.pump_cfg.channel)
        self.assert_pump_idle()

    def verify_cleanup_preconditions(self, down_steps: int, expected_retained_ml: float | dict[int, float]) -> dict[str, str]:
        """Prove pump and needle state before returning the sample automatically."""
        if any(state.persistence_errors for state in self.states.values()):
            raise PhysicalStateUncertain("Journal persistence failed, so an automatic recovery could not be recorded")
        try:
            self.assert_pump_idle()
        except VerificationError as exc:
            raise PhysicalStateUncertain(f"Pump is not proven idle: {exc}") from exc
        expected = expected_retained_ml if isinstance(expected_retained_ml, dict) else {self.pump_cfg.channel: expected_retained_ml}
        for ch, volume in expected.items():
            if abs(self.states[ch].retained_volume_ml - volume) > 1e-6:
                raise PhysicalStateUncertain(f"Channel {ch} estimated retained volume {self.states[ch].retained_volume_ml:g} mL is not the expected {volume:g} mL")
        try:
            return verify_needle(self.needle, down_steps)
        except Exception as exc:
            raise PhysicalStateUncertain(f"Needle is not proven at DOWN: {exc}") from exc

    def write_manifest(self) -> None:
        write_json_atomic(self.paths.manifest_json, manifest_payload(self.paths, self.identity, self.raw, list(self.plot_manifest or [])))

    def nmr_measurement(self, *, stage: str, cycle: int, rows: list[dict], started: datetime, progress: Callable[[str], None] | None = None) -> tuple[dict, Path, Path]:
        """Acquire, retrieve, process, and analyze one spectrum.

        ``rows`` gains the new row only after every step has succeeded, so a
        failed measurement can never contribute to plateau detection.
        """
        self.measurement_step = "NMR acquisition"
        self.last_acquisition_started_at = self.simulation.now() if self.simulation else datetime.now()
        path = self.acquire(self.nmr_cfg, self.paths.raw_dir, label=f"{stage}_{cycle:04d}")
        self.last_acquisition_completed_at = self.simulation.now() if self.simulation else datetime.now()
        self.last_acquisition_completed_monotonic = self.simulation.monotonic() if self.simulation else time.monotonic()
        if progress:
            progress("NMR acquisition")
        self.measurement_step = "NMR data retrieval"
        if not path.is_file() or path.stat().st_size == 0:
            raise VerificationError("NMR acquisition returned no nonempty retrieved data file")
        self.record("nmr_retrieved", workflow_phase=stage, cycle_number=cycle, result_path=str(path.relative_to(self.paths.run_dir)), scans=self.nmr_cfg.scans)
        if progress:
            progress("NMR data retrieval")
        area_only = "peak_finding" in self.raw["analysis"]
        self.measurement_step = "NMR processing"
        processed = self.process(path, self.paths, self.dataset_name)
        if not processed.is_dir():
            raise VerificationError("NMR process_fid output directory is missing")
        self.record("analysis_result", workflow_phase=stage, cycle_number=cycle, analysis_type=getattr(self.process, "last_kind", "process_fid_full_spectrum"), result_classification="completed", result_path=str(processed.relative_to(self.paths.run_dir)))
        if progress:
            progress("NMR processing")
        self.measurement_step = "LONG DATE metadata"
        long_date = str(read_jcamp_fid(path).metadata.get("LONG DATE", "")).strip()
        acquired_at = None
        timing_note = ""
        try:
            acquired_at = datetime.strptime(long_date, "%Y/%m/%d %H:%M:%S%z")
        except ValueError as exc:
            if not area_only:
                raise VerificationError("Authoritative JCAMP LONG DATE acquisition time is unavailable") from exc
            timing_note = "Authoritative JCAMP LONG DATE unavailable; time plots disabled"
        timestamp_source = "LONG DATE header" if acquired_at else "unavailable"
        if not area_only and any(st.completion for st in base.build_stages(self.raw["workflow"])) and rows:
            if acquired_at <= datetime.fromisoformat(rows[-1]["acquired_at"]):
                raise VerificationError("Acquisition LONG DATE must increase strictly; repeated/backwards metadata rejected")
        if self.first_acquisition_at is None and acquired_at is not None:
            self.first_acquisition_at = acquired_at
        metadata = {"iteration": cycle, "stage": stage, "stage_iteration": len([r for r in rows if r.get("stage") == stage]) + 1,
                    "elapsed_hours": (acquired_at - self.first_acquisition_at).total_seconds() / 3600 if acquired_at else None,
                    "acquired_at": acquired_at.isoformat(timespec="seconds") if acquired_at else None, "timestamp_source": timestamp_source,
                    "target_ppm": self.nmr_cfg.target_ppm,
                    "dataset_display_name": self.dataset_name, "nmr_scans": self.nmr_cfg.scans,
                    "receiver_gain": self.nmr_cfg.receiver_gain, "auto_gain": self.nmr_cfg.auto_gain}
        self.record("nmr_acquisition_time", workflow_phase=stage, cycle_number=cycle, acquired_at=metadata["acquired_at"], timestamp_source=timestamp_source)
        self.measurement_step = "NMR analysis"
        if ("peak_tracking" in self.raw["analysis"] or area_only) and not self.simulation:
            row, spectrum = dict(metadata), []
        else:
            row, spectrum = self.analyze(path, processed, self.paths, self.raw["analysis"], metadata)
        stage_spec = next((item for item in base.build_stages(self.raw["workflow"]) if item.name == stage), None)
        stage_rows = [r for r in rows if r.get("stage") == stage]
        if area_only and not self.simulation:
            row = tracked_peak_area_observation(path, processed, row, self.raw["analysis"], rows)
        elif stage_spec and stage_spec.completion and not self.simulation:
            try:
                row = tracked_peak_measurement(path, processed, row, self.raw["analysis"], rows, reference=rows[0] if rows else None)
            except ValueError as exc:
                self.record("analysis_result", workflow_phase=stage, cycle_number=cycle, result_classification=getattr(exc, "status", "invalid"), error_message=str(exc), tracking_evidence=getattr(exc, "evidence", None))
                raise base.AnalysisInconclusiveError(str(exc)) from exc
        if area_only:
            row["observation_recorded"] = True
            row["diagnostic_notes"] = "; ".join(v for v in (row.get("diagnostic_notes"), timing_note) if v)
        row.update(raw_path=str(path.relative_to(self.paths.run_dir)), processed_path=str(processed.relative_to(self.paths.run_dir)))
        if row.get("phase_evidence_path"):
            row["phase_evidence_path"] = str(Path(row["phase_evidence_path"]).relative_to(self.paths.run_dir))
        if row.get("plot_file") and row.get("plot_title"):
            if self.plot_manifest is None:
                self.plot_manifest = []
            self.plot_manifest.append({"file": row["plot_file"], "dataset_display_name": self.dataset_name, "visible_title": row["plot_title"], "stage": stage, "cycle_number": cycle, "measurement_valid": bool(row.get("measurement_valid", row.get("peak_clear")))})
            self.write_manifest()
        summary = {"peak_ppm": row.get("peak_ppm"), "peak_area": row.get("peak_area"), "snr": row.get("snr"), "metric_source": row.get("metric_source")}
        if not area_only and not row.get("measurement_valid", row.get("peak_clear")):
            self.record("analysis_result", workflow_phase=stage, cycle_number=cycle, result_classification="invalid", analysis_result=summary, error_message=row.get("qc_failure_reasons"))
            raise base.AnalysisInconclusiveError(f"Tracked resonance failed the configured checks: {row.get('qc_failure_reasons') or 'unknown reason'}")
        self.measurement_step = "plateau evaluation"
        previous = float(stage_rows[-1]["peak_area"]) if stage_rows else None
        row["growth_percent"] = base.growth_percent(previous, float(row["peak_area"]), epsilon=float(self.raw["analysis"].get("area_epsilon", 1.e-12)))
        if stage_spec and stage_spec.completion:
            evidence = completion_evidence([*stage_rows, row], stage_spec.completion)
            row["plateau"] = evidence["complete"]
            row["trend"] = stage_spec.completion["trend"]
            row["normalized_area"] = row["peak_area"] / evidence["normalization_area"] if evidence.get("normalization_area") else ""
            evidence_path = self.paths.run_dir / "stages" / stage / "evidence" / f"{cycle:04d}.json"
            write_json_atomic(evidence_path, evidence)
            row["completion_evidence_path"] = str(evidence_path.relative_to(self.paths.run_dir))
            if row.get("tracking_evidence_path"):
                tracking_path = Path(row["tracking_evidence_path"])
                tracking_audit = json.loads(tracking_path.read_text(encoding="utf-8"))
                tracking_audit["endpoint_evidence"] = evidence
                write_json_atomic(tracking_path, tracking_audit)
            self.record("stage_completion_evidence", workflow_phase=stage, cycle_number=cycle, evidence=evidence, result_path=row["completion_evidence_path"])
        else:
            row["plateau"] = False if area_only else base.plateau_reached([*stage_rows, row], self.raw["analysis"])
        if row.get("tracking_evidence_path"):
            row["tracking_evidence_path"] = str(Path(row["tracking_evidence_path"]).relative_to(self.paths.run_dir))
        rows.append(row)
        base.write_csv(self.paths.time_series_csv, rows, base.TIME_SERIES_COLUMNS)
        if spectrum:
            base.append_spectra(self.paths.spectra_csv, spectrum)
        self.record("analysis_result", workflow_phase=stage, cycle_number=cycle, result_classification="valid", analysis_result=dict(summary, growth_percent=row["growth_percent"]), plateau_progress={"plateau_reached": row["plateau"], "required_intervals": stage_spec.completion.get("consecutive_iterations", stage_spec.completion.get("consecutive_confirmations")) if stage_spec and stage_spec.completion else self.raw["analysis"].get("plateau_consecutive_intervals", 0)})
        self.last_analysis_completed_at = self.simulation.now() if self.simulation else datetime.now()
        self.measurement_step = None
        if progress:
            progress("NMR analysis")
        return row, path, processed


def _record_failure(s: Services, *, stage: str, cycle: int, step: str, exc: BaseException, recovery_attempted: bool) -> None:
    """Journal a stopped cycle without letting a journal fault mask the stop."""
    needle_uncertain = not getattr(s.needle, "position_certain", True)
    states = getattr(s, "channel_states", None) or {0: s.state}
    uncertain = any(state.uncertain for state in states.values()) or needle_uncertain or isinstance(exc, (PositionUncertainError, PhysicalStateUncertain))
    error = {"error_type": type(exc).__name__, "error_message": str(exc)}
    events: list[tuple[str, dict[str, Any]]] = []
    if recovery_attempted:
        events.append(("recovery_cleanup", {"result_classification": "not_attempted" if step == "recovery precheck" else "failed", "failed_step": step, **error}))
    events.append(("cycle_status", {"status": "FAILED", "failed_step": step, **error}))
    events.append(("manual_inspection_required", {"reason": f"Cycle stopped at {step}", "physical_state_certainty": "uncertain" if uncertain else "requires_inspection", "result_classification": "manual_inspection_required", **error}))
    for name, fields in events:
        try:
            s.record(name, workflow_phase=stage, cycle_number=cycle, **fields)
        except BaseException as journal_exc:
            s.state.record_persistence_error(journal_exc)
            return


def sample_cycle(s: Services, *, stage: str, cycle: int, rows: list[dict], started: datetime) -> dict:
    """Run one physical sampling cycle in the fixed SOP order.

    A plateau result never skips cleanup. If the NMR step fails (acquisition,
    retrieval, processing, LONG DATE, analysis, or plateau evaluation), the
    measurement is discarded. When the pump and needle states are then proven,
    the same return and cleanup sequence runs and the experiment stops for
    review. A physical uncertainty, operator abort, or journal failure stops
    what can be stopped and leaves the rig for manual inspection.
    """
    values = cycle_values(s.raw)
    events = s.raw["workflow"]["cycle"]
    expected_retained = None
    if getattr(s, "channel_states", None):
        expected_retained = {ch: state.retained_volume_ml for ch, state in s.channel_states.items()}
        for index in (0, 2):
            event = events[index]
            ch = pump_channels.operation_channel(s.raw, event)
            expected_retained[ch] += float(event["volume_ml"])
    def transfer(index: int, direction: str, volume: float, needle: str) -> None:
        overrides = {key: events[index][key] for key in ("channel", "rate_ml_min") if key in events[index]}
        s.pump_move(direction, volume, needle, stage=stage, cycle=cycle, **overrides)
    s.record("cycle_status", workflow_phase=stage, cycle_number=cycle, status="STARTED", started_at=datetime.now().isoformat(timespec="seconds"))
    step = "verify UP"
    failure: Exception | None = None
    failed_step = ""
    try:
        up, down, _ = positions(s.arduino_cfg, mock=s.mock)
        verify_needle(s.needle, up)
        step = "initial withdraw"
        transfer(0, "withdraw", values["initial_withdraw_ml"], "UP")
        step = "needle DOWN"
        s.move_needle("DOWN")
        step = "sample withdraw"
        transfer(2, "withdraw", values["sample_withdraw_ml"], "DOWN")
        step = "settle"
        s.sleep("NMR settle", values["settle_seconds"])
        step = "NMR measurement"
        row: dict = {}
        try:
            row, path, processed = s.nmr_measurement(stage=stage, cycle=cycle, rows=rows, started=started)
        except JournalError:
            raise
        except Exception as exc:
            # Ctrl+C (KeyboardInterrupt) is not an Exception: an operator abort
            # never triggers automatic motion.
            failure = exc
            failed_step = s.measurement_step or "NMR measurement"
            step = "recovery precheck"
            s.record("measurement_failed", workflow_phase=stage, cycle_number=cycle, failed_step=failed_step, error_type=type(exc).__name__, error_message=str(exc), counted_toward_plateau=False, result_classification="measurement_failed")
            s.record("cycle_status", workflow_phase=stage, cycle_number=cycle, status="MEASUREMENT_FAILED", failed_step=failed_step)
            needle_status = s.verify_cleanup_preconditions(down, expected_retained if expected_retained is not None else values["initial_withdraw_ml"] + values["sample_withdraw_ml"])
            s.record("recovery_cleanup", workflow_phase=stage, cycle_number=cycle, result_classification="started", failed_step=failed_step, reason="measurement failed; pump idle and needle at DOWN verified", needle_status=needle_status)
        else:
            s.record("cycle_status", workflow_phase=stage, cycle_number=cycle, status="NMR_COMPLETE", raw_path=str(path.relative_to(s.paths.run_dir)), processed_path=str(processed.relative_to(s.paths.run_dir)))
        step = "return infusion while DOWN"
        transfer(5, "infuse", values["return_infuse_ml"], "DOWN")
        step = "needle UP"
        s.move_needle("UP")
        step = "cleanup withdraw"
        transfer(7, "withdraw", values["cleanup_withdraw_ml"], "UP")
        step = "cleanup infusion"
        transfer(8, "infuse", values["cleanup_infuse_ml"], "UP")
        step = "final idle verification"
        s.assert_pump_idle()
        s.record("cycle_status", workflow_phase=stage, cycle_number=cycle, status="CLEANUP_COMPLETE", after_measurement_failure=failure is not None)
        if failure is not None:
            s.record("recovery_cleanup", workflow_phase=stage, cycle_number=cycle, result_classification="completed", failed_step=failed_step, estimated_retained_volume_ml=s.state.retained_volume_ml)
            raise MeasurementFailedAfterCleanup(stage, cycle, failed_step, failure)
        s.record("cycle_completed", workflow_phase=stage, cycle_number=cycle, status="COMPLETE", completed_at=datetime.now().isoformat(timespec="seconds"), result_classification="completed_after_cleanup", plateau=bool(row["plateau"]))
        return row
    except MeasurementFailedAfterCleanup:
        raise
    except BaseException as exc:
        # Stop first; journaling must never delay or prevent the stop.
        if hasattr(s, "stop_all"):
            s.stop_all(stage=stage, cycle=cycle)
        else:
            base.attempt_emergency_stop(s.pump, s.state, s.recorder, workflow_phase=stage, cycle_number=cycle)
        s.needle.stop_best_effort()
        _record_failure(s, stage=stage, cycle=cycle, step=step, exc=exc, recovery_attempted=failure is not None)
        raise


def mock_acquire(nmr_cfg: config.NmrSettings, save_dir: Path, *, label: str) -> Path:
    path = save_dir / f"{label}.dx"
    shutil.copyfile(MOCK_NMR_FIXTURE, path)
    return path


# Production process_fid results for byte-identical mock spectra, per process.
_MOCK_PROCESSED: dict[str, tuple[Path, str]] = {}


class MockProcessFid:
    """Fast Level 2 stand-in for process_fid on byte-identical fixture copies.

    The first copy of a spectrum really goes through production process_fid.
    Later copies of the same bytes reuse its tracked-window tables, renamed to
    the new file, instead of repeating the subprocess; no figures are copied.
    Level 1 mock runs process_fid for every measurement.
    """

    def __init__(self, tracked_window: tuple[float, float], peak_tracking: dict | None = None) -> None:
        self.tracked_window = tracked_window
        self.peak_tracking = peak_tracking
        self.last_kind = "process_fid_full_spectrum"

    def __call__(self, dx_path: Path, paths: base.RunPaths, dataset_display_name: str) -> Path:
        key = f"{hashlib.sha256(dx_path.read_bytes()).hexdigest()}:{self.tracked_window}:{json.dumps(self.peak_tracking, sort_keys=True)}"
        cached = _MOCK_PROCESSED.get(key)
        if cached is None or not cached[0].is_dir():
            processed = base.run_process_fid_postprocessing(dx_path, paths, dataset_display_name, tracked_window=self.tracked_window, peak_tracking=self.peak_tracking)
            _MOCK_PROCESSED[key] = (processed, dx_path.name)
            self.last_kind = "process_fid_full_spectrum"
            return processed
        source_dir, source_name = cached
        output = paths.run_dir / "processed_nmr" / f"{dx_path.stem}_mock_reused"
        output.mkdir(parents=True)
        for suffix in ("peaks_simple.csv", "peak_qc_log_window.csv"):
            table = _process_fid_table(source_dir, suffix)
            with table.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                fields = list(reader.fieldnames or [])
                table_rows = [dict(row, file=dx_path.name) if row.get("file") == source_name else row for row in reader]
            with (output / table.name).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(table_rows)
        (output / "MOCK_REUSED_PROCESSING.txt").write_text(
            f"Mock fixture byte-identical to {source_name}; its production process_fid tables were reused.\n",
            encoding="utf-8",
        )
        if self.peak_tracking is not None:
            # The tracker needs lossless evidence, not rounded display tables.
            shutil.copytree(source_dir / "phase_audit", output / "phase_audit")
        self.last_kind = "mock_process_fid_reused"
        return output


def tracked_window(raw: dict[str, Any], nmr_cfg: config.NmrSettings) -> tuple[float, float]:
    return float(nmr_cfg.target_ppm), float(raw["analysis"].get("peak_finding", raw["analysis"].get("peak_tracking", {})).get("search", {}).get("half_width_ppm", raw["analysis"].get("detection_window_ppm", .2)))


def validate_stage_policy(stages: list[base.Stage]) -> None:
    """Refuse an explicit measurement cap that would end a stage before its duration."""
    for stage in stages:
        if stage.completion and stage.completion.get("method") == "area_only":
            continue
        slots = base.duration_measurement_slots(stage.interval_minutes, stage.max_hours, stage.measure_immediately)
        if stage.max_measurements_explicit and stage.max_measurements < slots:
            last = stage.interval_minutes * (stage.max_measurements - (1 if stage.measure_immediately else 0))
            raise ValueError(
                f"Stage {stage.name}: max_measurements {stage.max_measurements} ends monitoring after the "
                f"measurement at {last:g} min, before max_hours {stage.max_hours:g} h. Remove max_measurements "
                f"so the duration governs, or set it to at least {slots}."
            )


def _journal_mode(run_dir: Path) -> str | None:
    try:
        with (run_dir / "operation_journal.jsonl").open(encoding="utf-8") as handle:
            first = handle.readline()
    except OSError:
        return None
    for mode in ("mock", "live"):
        if f'"mode":"{mode}"' in first or f'"result_classification":"{mode}"' in first:
            return mode
    return None


def unresolved_previous_live_run(raw: dict[str, Any]) -> Any | None:
    """Return the latest live run's inspection when it still needs review."""
    root = config.resolve_repo_path(raw["output"]["run_root_dir"])
    if not root.is_dir():
        return None
    candidates = sorted((p for p in root.iterdir() if (p / "operation_journal.jsonl").is_file()), key=lambda p: p.name, reverse=True)
    for run_dir in candidates:
        if _journal_mode(run_dir) == "mock":
            continue
        result = inspect_run(run_dir)
        state = result.replay.state if result.replay is not None else None
        if result.classification in REVIEW_BLOCKING or (state is not None and state.operator_review_required):
            return result
        return None
    return None


def prepare(workflow_path: Path, machine_path: Path, arduino_path: Path, *, mock: bool, require_needle_live: bool = True, acknowledged_review: str | None = None):
    raw = base.load_si6_config(workflow_path)
    if "three_instrument" not in raw:
        raise ValueError("Three-instrument config section is required")
    raw["workflow"]["initial_stage"]["plateau_stopping_enabled"] = raw["three_instrument"]["initial_plateau_stopping_enabled"]
    resolved_stages = base.build_stages(raw["workflow"])
    if any(stage.completion for stage in resolved_stages):
        from .si6_profile import validate_profile
        validate_profile(raw, resolved_stages)
    cycle_values(raw)
    validate_stage_policy(base.build_stages(raw["workflow"]))
    arduino_cfg = load_arduino_config(arduino_path)
    if mock or require_needle_live:
        positions(arduino_cfg, mock=mock)
    pump_cfg, nmr_cfg = base.build_instrument_settings(raw, machine_path)
    if any(stage.completion for stage in base.build_stages(raw["workflow"])) and nmr_cfg.auto_gain:
        raise ValueError("Resolved NMR auto_gain must be false for quantitative completion monitoring")
    pump_channels.instrument_configs(raw, pump_cfg)
    if not mock and require_needle_live:
        # Demo mode: staged-commissioning records and previous-run review are
        # advisory; only the settings needed to actually move are required.
        if not pump_cfg.port or not nmr_cfg.host:
            raise ValueError("Live Chemyx serial port and NMR host must be configured")
        two_stage = any(s.completion for s in base.build_stages(raw["workflow"]))
        if two_stage:
            if "simulation" in raw:
                raise ValueError("A simulation template cannot be run live; select nominal/development hardware YAML")
            if raw["workflow"]["experiment_id"].startswith("SET_"):
                raise ValueError("Set a unique workflow.experiment_id for this physical reaction before live use")
            check_previous_run_review(raw, acknowledged_review)
            ledger = config.REPO_ROOT / "runtime" / "si6_doses" / f"{raw['workflow']['experiment_id']}.json"
            if ledger.exists():
                raise VerificationError(f"Experiment already reserved: {ledger}. Automatic restart refused; inspect and reconcile.")
        review = unresolved_previous_live_run(raw)
        if review is not None and not two_stage:
            print(f"NOTE: previous live run {review.run_dir.name} ended {review.classification.value}; continuing (demo mode).")
    return raw, arduino_cfg, pump_cfg, nmr_cfg


def check_previous_run_review(raw: dict[str, Any], acknowledged_review: str | None) -> Any | None:
    """Block a live run until the operator acknowledges an unresolved previous one."""
    review = unresolved_previous_live_run(raw)
    if review is not None and review.run_dir.name != acknowledged_review:
        raise LiveExecutionBlocked("Previous live run review", [
            f"{review.run_dir.name} ended {review.classification.value}"
            f"{' (operator review required)' if review.replay and review.replay.state.operator_review_required else ''}; "
            "reconcile the rig per docs/LIVE_COMMISSIONING_CHECKLIST.md, then rerun with "
            f"--acknowledge-review {review.run_dir.name}"
        ])
    return review


@contextmanager
def open_services(raw, arduino_cfg, pump_cfg, nmr_cfg, *, identity: RunIdentity, fast_mock_processing: bool = False, acknowledged_review: str | None = None):
    """One set of production interfaces for both entry points."""
    mock = identity.mock
    # Recheck before opening transports, including callers supplying dictionaries.
    cycle_values(raw)
    definitions = pump_channels.channel_definitions(raw)
    channel_configs = pump_channels.instrument_configs(raw, pump_cfg)
    stages = base.build_stages(raw["workflow"])
    two_stage = any(stage.completion for stage in stages)
    if two_stage:
        from .si6_profile import validate_profile
        validate_profile(raw, stages)
        if nmr_cfg.auto_gain:
            raise ValueError("Resolved NMR auto_gain must be false for completion monitoring")
        if any(cfg.units != 0 for cfg in channel_configs.values()):
            raise ValueError("Resolved two-stage pump units must be mL/min")
        if not mock:
            if not all(st.completion.get("method") == "area_only" for st in stages):
                raise ValueError("Historical QC/statistical profiles are offline only; migrate to area_only completion and peak_finding")
            if "simulation" in raw:
                raise ValueError("Simulation settings cannot be selected live")
            check_previous_run_review(raw, acknowledged_review)
        elif "simulation" not in raw:
            raise ValueError("For a complete two-stage mock use the fast simulation template")
    for event in raw["workflow"]["cycle"]:
        pump_channels.validate_operation(raw, event, "workflow.cycle")
    for stage in stages:
        pump_channels.validate_stage_actions(raw, list(stage.before_monitoring), f"{stage.name}.before_monitoring")
        pump_channels.validate_stage_actions(raw, list(stage.after_monitoring), f"{stage.name}.after_monitoring")
    pump_channels.validate_channel_capacity(raw, stages)
    channel_states = {ch: base.PumpSafetyState(float(settings["initial_retained_volume_ml"]), channel=ch) for ch, settings in definitions.items()}
    paths = create_identified_run(raw, identity)
    recorder = base.create_run_recorder(paths)
    write_json_atomic(paths.run_dir / "config_snapshot.json", raw)
    nmr_snapshot = {key: str(value) if isinstance(value, Path) else value for key, value in asdict(nmr_cfg).items()}
    write_json_atomic(paths.run_dir / "instrument_settings_snapshot.json", {"pump_channels": {str(ch): asdict(cfg) for ch, cfg in channel_configs.items()}, "nmr": nmr_snapshot})
    write_json_atomic(paths.run_dir / "arduino_config_snapshot.json", {k: v for k, v in arduino_cfg.items() if not k.startswith("_")})
    write_json_atomic(paths.manifest_json, manifest_payload(paths, identity, raw, []))
    initial_channels = {
        str(ch): {"retained_volume_ml": state.retained_volume_ml,
                  "cumulative_withdrawn_ml": 0.0, "cumulative_infused_ml": 0.0,
                  "syringe_capacity_ml": float(definitions[ch]["syringe_capacity_ml"]),
                  "syringe_safety_margin_ml": float(definitions[ch]["syringe_safety_margin_ml"]),
                  "syringe_diameter_mm": channel_configs[ch].diameter,
                  "rate": channel_configs[ch].rate, "units": config.UNITS[channel_configs[ch].units],
                  "motion_active": False, "uncertain": False}
        for ch, state in channel_states.items()
    }
    recorder.record("phase_transition", previous_state=None, new_state="initializing", workflow_phase="initializing", result_classification=identity.mode, mode=identity.mode, run_kind=identity.kind, diagnostic_selection=identity.selection, acknowledged_previous_run=acknowledged_review, default_pump_channel=pump_cfg.channel, pump_channels=initial_channels)
    guard = None
    if two_stage and identity.kind == "si6":
        guard = DoseGuard(paths.run_dir / "mock_dose_ledger" if mock else config.REPO_ROOT / "runtime" / "si6_doses", raw["workflow"]["experiment_id"], raw, paths.run_dir.name)
        guard.reserve()  # Exclusive, durable admission BEFORE either transport.
        recorder.record("experiment_reserved", **guard.data, ledger_path=str(guard.path))
    state = channel_states[pump_cfg.channel]
    lock = None
    settings = arduino_cfg["arduino"]
    if mock:
        transport = FakeArduinoTransport(runtime_configurable=True, version=settings.get("expected_version") or "1.2.1")
    else:
        selected = resolve_arduino_port(settings.get("port"), settings.get("fingerprint"))
        lock = PortProcessLock(selected.device).acquire()
        transport = SerialTransport(selected.device, settings["baud_rate"], read_timeout_s=settings["read_timeout_s"], write_timeout_s=settings["write_timeout_s"])
    # The staged-test 120 s cap does not apply to a continuously monitored
    # experiment. Keep a finite ceiling exceeding all configured stage limits.
    total_hours = sum(stage.max_hours for stage in base.build_stages(raw["workflow"]))
    controller = NeedleController(transport, expected_device=settings["expected_device"], expected_board=settings["expected_board"], expected_version=settings.get("expected_version"), ready_timeout_s=settings["ready_timeout_s"], command_timeout_s=settings["command_timeout_s"], overall_timeout_s=max(3600, (total_hours + 2) * 3600), allow_motion=True, motion_guard=lambda: all(not s.motion_active and not s.uncertain and not s.persistence_errors for s in channel_states.values()))
    # The serial response delay only matters for a real pump.
    pump = Pump(port=pump_cfg.port, baud_rate=pump_cfg.baud_rate, channel=pump_cfg.channel, units=pump_cfg.units, timeout=pump_cfg.timeout, response_delay=0 if mock else pump_cfg.response_delay, mock=mock)
    try:
        with controller:
            if arduino_cfg["firmware"].get("runtime_configurable"):
                if mock:
                    synthetic = dict(arduino_cfg)
                    synthetic["firmware"] = dict(arduino_cfg["firmware"], motion_enabled=True, limits_enabled=False)
                    synthetic["motion"] = dict(arduino_cfg["motion"], maximum_travel_steps=0, maximum_speed_steps_s=100, maximum_acceleration_steps_s2=300, home_speed_steps_s=0)
                    controller.configure_runtime(synthetic)
                else:
                    controller.configure_runtime(arduino_cfg)
            tracked_cfg = arduino_cfg
            if mock:
                tracked_cfg = dict(arduino_cfg)
                tracked_cfg["needle"] = dict(arduino_cfg["needle"], steps_per_unit=1 if "simulation" in raw else 20, up_step_sign=1)
                tracked_cfg["motion"] = dict(
                    arduino_cfg["motion"], maximum_speed_steps_s=100,
                    maximum_acceleration_steps_s2=300,
                )
            needle = TrackedNeedle(controller, tracked_cfg, state_path=paths.run_dir / "mock_needle_state.json" if mock else None, allow_home_assumption=mock or not two_stage)
            if mock:
                needle.confirm_home(operator_confirmed=True)
            with pump:
                # Attempt all STOPs before configuration, even if one fails.
                for ch, channel_state in channel_states.items():
                    base.attempt_emergency_stop(pump, channel_state, recorder)
                if any(s.last_stop_status is not base.StopStatus.SUCCEEDED or s.persistence_errors for s in channel_states.values()):
                    raise VerificationError("Initial Chemyx STOP was not confirmed on every configured channel")
                for ch, cfg in channel_configs.items():
                    pump.select_channel(ch)
                    base.configure_pump(pump, cfg)
                    recorder.record("pump_configuration", workflow_phase="initializing", channel=ch, units=config.UNITS[cfg.units], syringe_diameter_mm=cfg.diameter, rate=cfg.rate)
                pump.select_channel(pump_cfg.channel)
                window = tracked_window(raw, nmr_cfg)
                processor = MockProcessFid(window, raw["analysis"].get("peak_finding", raw["analysis"].get("peak_tracking"))) if mock and fast_mock_processing else partial(base.run_process_fid_postprocessing, tracked_window=window, peak_tracking=raw["analysis"].get("peak_finding", raw["analysis"].get("peak_tracking")))
                services = Services(
                    needle=needle, pump=pump, pump_cfg=pump_cfg, nmr_cfg=nmr_cfg, raw=raw,
                    arduino_cfg=arduino_cfg, paths=paths, recorder=recorder, state=state,
                    acquire=mock_acquire if mock else base.run_nmr_acquisition, process=processor,
                    analyze=analyze_tracked_resonance,
                    sleep=(lambda _label, _seconds: None) if mock else base.sleep_with_progress,
                    identity=identity,
                    channel_configs=channel_configs, channel_states=channel_states,
                    dose_guard=guard,
                )
                if mock and "simulation" in raw:
                    from .si6_simulation import TrendSimulation
                    simulator = TrendSimulation(raw["simulation"], MOCK_NMR_FIXTURE)
                    services.simulation = simulator
                    services.acquire, services.process, services.analyze, services.sleep = simulator.acquire, simulator.process, simulator.analyze, simulator.sleep
                try:
                    yield services
                except BaseException:
                    # Stop while the connection is still open, before __exit__.
                    services.stop_all()
                    needle.stop_best_effort()
                    raise
    except BaseException as exc:
        if pump.is_connected:
            for ch, channel_state in sorted(channel_states.items(), key=lambda item: not item[1].motion_active):
                base.attempt_emergency_stop(pump, channel_state, recorder)
        if controller.is_open:
            if "needle" in locals():
                needle.stop_best_effort()
            else:
                controller.stop_best_effort()
        try:
            recorder.record("terminal", workflow_phase="run", terminal_status="failed", result_classification=type(exc).__name__, error_message=str(exc), physical_state_certainty="uncertain" if any(s.uncertain or s.last_stop_status is not base.StopStatus.SUCCEEDED for s in channel_states.values()) else "requires_inspection")
        except BaseException:
            pass
        raise
    finally:
        if lock is not None:
            lock.release()


def preflight(s: Services, *, check_nmr: bool = True) -> None:
    s.needle.ping()
    status = s.needle.status()
    if bool_field(status, "moving") or status["fault"] != "NONE":
        raise VerificationError("Arduino not idle and fault-free")
    verify_pump_help(s.pump.help())
    if check_nmr and not s.mock:
        rpc = NmrRpcClient(NmrRpcConfig(host=s.nmr_cfg.host, port=s.nmr_cfg.port, scheme=s.nmr_cfg.scheme, timeout=s.nmr_cfg.timeout))
        verify_nmr_ping(rpc.ping())
    if not base.PROCESS_FID_SCRIPT.is_file():
        raise VerificationError("NMR process_fid script missing")
    s.record("phase_transition", previous_state=None, new_state="preflight_passed", workflow_phase="preflight")


def verify_nmr_ping(reply: Any) -> None:
    if reply is None or reply is False or reply == "" or (isinstance(reply, (dict, list, tuple)) and not reply):
        raise VerificationError("NMR PING returned no positive response")
    if isinstance(reply, dict) and any(
        reply.get(key) is False
        for key in ("connected", "Connected", "ready", "Ready", "ok", "Ok")
    ):
        raise VerificationError(f"NMR PING explicitly reported unavailable: {reply!r}")


def verify_pump_help(reply: Any) -> None:
    if reply is None or reply is False or reply == "" or (isinstance(reply, (dict, list, tuple)) and not reply):
        raise VerificationError("Chemyx HELP returned no positive response")


def home_and_raise(s: Services) -> None:
    s.assert_pump_idle()
    s.needle.enable()
    s.needle.home()
    status = s.needle.status()
    if "position_valid" in status:
        if not bool_field(status, "position_valid"):
            raise VerificationError("Needle software HOME reference unknown")
    elif not bool_field(status, "homed") or not bool_field(status, "position_known"):
        raise VerificationError("Needle homing not confirmed")
    s.move_needle("UP")


def run_diagnostic(s: Services, selection: str = "all") -> list[str]:
    passed: list[str] = []
    def step(label: str, fn):
        try:
            fn()
        except BaseException as exc:
            print(f"[FAIL] {label}\nReason: {exc}")
            s.record("diagnostic_failure", workflow_phase="diagnostic", step=label, error_type=type(exc).__name__, error_message=str(exc))
            raise
        print(f"[PASS] {label}")
        passed.append(label)
    if selection in ("all", "needle"):
        step("Arduino connection", s.needle.ping)
        step("Needle homing", lambda: home_and_raise(s))
        step("Needle UP", lambda: verify_needle(s.needle, positions(s.arduino_cfg, mock=s.mock)[0]))
        step("Needle DOWN", lambda: s.move_needle("DOWN"))
        step("Needle UP again", lambda: s.move_needle("UP"))
    if selection in ("all", "pump"):
        step("Chemyx connection", lambda: verify_pump_help(s.pump.help()))
        if selection == "pump":
            step("Needle homing", lambda: home_and_raise(s))
        d = s.raw["three_instrument"]
        channel = {"channel": d["diagnostic_channel"]} if "diagnostic_channel" in d else {}
        step("Chemyx withdraw", lambda: s.pump_move("withdraw", float(d["test_withdraw_ml"]), "UP", stage="diagnostic", cycle=1, **channel))
        step("Chemyx infuse", lambda: s.pump_move("infuse", float(d["test_infuse_ml"]), "UP", stage="diagnostic", cycle=1, **channel))
    if selection in ("all", "nmr", "process"):
        if selection != "process":
            def check_nmr():
                if s.mock:
                    return
                reply = NmrRpcClient(NmrRpcConfig(
                    host=s.nmr_cfg.host, port=s.nmr_cfg.port,
                    scheme=s.nmr_cfg.scheme, timeout=s.nmr_cfg.timeout,
                    poll_seconds=s.nmr_cfg.poll_seconds,
                    max_wait_seconds=s.nmr_cfg.max_wait_seconds,
                )).ping()
                verify_nmr_ping(reply)
            step("NMR connection", check_nmr)
        rows: list[dict] = []
        completed: list[str] = []
        ordered = ["NMR acquisition", "NMR data retrieval", "NMR processing", "NMR analysis"]
        def progress(label: str) -> None:
            completed.append(label)
            print(f"[PASS] {label}")
            passed.append(label)
        try:
            s.nmr_measurement(stage="diagnostic", cycle=1, rows=rows, started=datetime.now(), progress=progress)
        except BaseException as exc:
            failed = next((label for label in ordered if label not in completed), "NMR analysis")
            print(f"[FAIL] {failed}\nReason: {exc}")
            s.record("diagnostic_failure", workflow_phase="diagnostic", step=failed, error_type=type(exc).__name__, error_message=str(exc))
            raise
    return passed


def run_nmr_only(raw: dict[str, Any], pump_cfg: config.PumpConfig, nmr_cfg: config.NmrSettings, *, mock: bool) -> base.RunPaths:
    """Level 1 NMR diagnostic; opens neither serial port."""
    identity = RunIdentity("diagnostic", mock, "nmr")
    paths = create_identified_run(raw, identity)
    recorder = base.create_run_recorder(paths)
    write_json_atomic(paths.run_dir / "config_snapshot.json", raw)
    write_json_atomic(paths.manifest_json, manifest_payload(paths, identity, raw, []))
    recorder.record("phase_transition", previous_state=None, new_state="initializing", workflow_phase="initializing", result_classification=identity.mode, mode=identity.mode, run_kind=identity.kind, diagnostic_selection=identity.selection)
    services = Services(
        needle=None, pump=None, pump_cfg=pump_cfg, nmr_cfg=nmr_cfg, raw=raw, arduino_cfg={},
        paths=paths, recorder=recorder, state=base.PumpSafetyState(),
        acquire=mock_acquire if mock else base.run_nmr_acquisition,
        process=partial(base.run_process_fid_postprocessing, tracked_window=tracked_window(raw, nmr_cfg)),
        analyze=analyze_tracked_resonance, sleep=lambda _label, _seconds: None, identity=identity,
    )
    run_diagnostic(services, "nmr")
    return paths


def run_processing_only(raw: dict[str, Any], nmr_cfg: config.NmrSettings, source: Path, *, mock: bool) -> tuple[base.RunPaths, Path, dict]:
    """Process one existing spectrum with production process_fid; no instrument."""
    identity = RunIdentity("processing_only", mock)
    paths = create_identified_run(raw, identity)
    target = paths.raw_dir / source.name
    shutil.copyfile(source, target)
    write_json_atomic(paths.manifest_json, manifest_payload(paths, identity, raw, []))
    processed = base.run_process_fid_postprocessing(target, paths, paths.run_dir.name, tracked_window=tracked_window(raw, nmr_cfg))
    metadata = {"iteration": 1, "stage": "processing_only", "elapsed_hours": 0.0, "target_ppm": nmr_cfg.target_ppm, "dataset_display_name": paths.run_dir.name}
    row, _ = analyze_tracked_resonance(target, processed, paths, raw["analysis"], metadata)
    figures = [{"file": row["plot_file"], "dataset_display_name": paths.run_dir.name, "visible_title": row["plot_title"], "measurement_valid": bool(row["peak_clear"])}] if row.get("plot_title") else []
    write_json_atomic(paths.manifest_json, manifest_payload(paths, identity, raw, figures))
    if not row["peak_clear"]:
        raise base.AnalysisInconclusiveError(f"Tracked resonance failed the configured checks: {row['qc_failure_reasons']}")
    return paths, processed, row


def operator_stage_decision(stage: base.Stage, outcome: base.RunOutcome, *, input_fn: Callable[[str], str] = input, interactive: bool | None = None) -> str:
    """Ask whether to continue, advance, or abort after a stage limit without plateau."""
    if not (sys.stdin.isatty() if interactive is None else interactive):
        return "abort"
    print(
        f"\nSTAGE LIMIT: {stage.name} ended without a plateau ({outcome.message}).\n"
        "  CONTINUE  keep sampling this stage for another full stage duration\n"
        "  ADVANCE   go to the next reagent checkpoint without a plateau\n"
        "  ABORT     end the experiment now (the rig is at rest)"
    )
    try:
        for _ in range(3):
            answer = input_fn("c = continue, a = advance, q = abort: ").strip().upper()
            if answer in STAGE_DECISIONS:
                return STAGE_DECISIONS[answer]
            print("Unrecognized answer.")
    except (EOFError, KeyboardInterrupt):
        pass
    return "abort"


def run_stage_actions(s: Services, events: tuple[dict[str, Any], ...], *, stage: str, phase: str, cycle: int, rows: list[dict], started: datetime) -> int:
    """Execute a configured boundary sequence once, outside repeated sampling.

    Boundary NMR uses the existing acquisition/processing path and a separate
    series label, so it does not contribute to the monitoring plateau window.
    A failure requires review; there is no guessed reverse-dose recovery.
    """
    if not events:
        return cycle
    label = f"{stage}_{phase}"
    pump_channels.validate_stage_actions(s.raw, list(events), label)
    s.assert_pump_idle()
    up, down, _ = positions(s.arduino_cfg, mock=s.mock)
    verify_needle(s.needle, up)
    step = "boundary start"
    try:
        s.record("stage_actions_status", workflow_phase=label, status="STARTED")
        for index, event in enumerate(events, 1):
            action = str(event["action"]).lower()
            step = f"{phase}[{index}] {action}"
            s.record("stage_action", workflow_phase=label, action_index=index, action=action, result_classification="started")
            if action in {"withdraw", "infuse"}:
                overrides = {key: event[key] for key in ("channel", "rate_ml_min") if key in event}
                s.pump_move(action, float(event["volume_ml"]), event["needle_position"], stage=label, cycle=cycle, **overrides)
            elif action == "needle":
                s.move_needle(event["position"])
            elif action == "pause":
                s.assert_pump_idle()
                s.sleep("stage pause", float(event["seconds"]))
            elif action == "nmr":
                s.assert_pump_idle()
                verify_needle(s.needle, up if event["needle_position"] == "UP" else down)
                cycle += 1
                s.nmr_measurement(stage=label, cycle=cycle, rows=rows, started=started)
            s.record("stage_action", workflow_phase=label, action_index=index, action=action, result_classification="completed")
        s.assert_pump_idle()
        verify_needle(s.needle, up)
        s.record("stage_actions_status", workflow_phase=label, status="COMPLETE")
        return cycle
    except BaseException as exc:
        s.stop_all(stage=label, cycle=cycle)
        s.needle.stop_best_effort()
        _record_failure(s, stage=label, cycle=cycle, step=step, exc=exc, recovery_attempted=False)
        raise


def run_experiment(s: Services, *, mock_cycles_per_stage: int = 4, stage_decision: Callable[[base.Stage, base.RunOutcome], str] | None = None) -> base.RunOutcome:
    configured_stages = base.build_stages(s.raw["workflow"])
    if not s.mock and any(st.completion for st in configured_stages) and not all(st.completion and st.completion.get("method") == "area_only" for st in configured_stages):
        raise ValueError("Live two-stage experiments require the current area_only profile")
    preflight(s)
    home_and_raise(s)
    rows: list[dict] = []
    started = datetime.now()
    cycle = 0
    advanced: list[str] = []
    stages = base.build_stages(s.raw["workflow"])
    area_only = all(st.completion and st.completion.get("method") == "area_only" for st in stages)
    if s.mock and not s.simulation:
        stages = [replace(stage, interval_minutes=0.0001, max_hours=1, max_measurements=mock_cycles_per_stage, max_measurements_explicit=True) for stage in stages]
    def operator(prompt: str) -> None:
        if area_only:
            s.record("automatic_stage_entry", workflow_phase="chemistry", checkpoint=prompt, result_classification="automatic_after_start_confirmation")
        elif s.mock:
            s.record("operator_checkpoint", workflow_phase="chemistry", checkpoint=prompt, result_classification="requested")
            s.record("operator_checkpoint", workflow_phase="chemistry", checkpoint=prompt, result_classification="confirmed", decided_by="mock")
        else:
            base.operator_checkpoint(prompt, s.recorder, workflow_phase="chemistry")
    def decide(stage: base.Stage, outcome: base.RunOutcome, extension: int) -> str:
        s.record("stage_limit_decision", workflow_phase=stage.name, extension=extension, result_classification="requested", options=sorted(STAGE_DECISIONS.values()))
        if stage_decision is not None:
            choice, source = stage_decision(stage, outcome), "callback"
        elif s.mock:
            choice, source = "abort", "mock_default"
        else:
            choice, source = operator_stage_decision(stage, outcome), "operator"
        if choice not in STAGE_DECISIONS.values():
            choice = "abort"
        s.record("stage_limit_decision", workflow_phase=stage.name, extension=extension, result_classification=choice, decided_by=source)
        return choice
    def stage_runner(stage: base.Stage) -> base.RunOutcome:
        nonlocal cycle
        cycle = run_stage_actions(s, stage.before_monitoring, stage=stage.name, phase="before_monitoring", cycle=cycle, rows=rows, started=started)
        def finish(outcome: base.RunOutcome) -> base.RunOutcome:
            nonlocal cycle
            if stage.completion and stage.after_monitoring:
                if outcome.stage_outcome is not base.StageOutcome.PLATEAU_REACHED or not completion_evidence([r for r in rows if r["stage"] == stage.name], stage.completion)["complete"]:
                    raise VerificationError("Reagent transition requires genuine sustained stage completion")
                if s.dose_guard is None:
                    raise VerificationError("Durable experiment reservation is required before dosing")
                # Intent is durable before the first boundary physical action.
                dose = next(e for e in stage.after_monitoring if e["action"] == "infuse")
                s.dose_guard.mark("DISPATCH_INTENT", source_stage=stage.name, channel=2, volume_ml=dose["volume_ml"], rate_ml_min=dose["rate_ml_min"])
                s.record("dose_dispatch_intent", **s.dose_guard.data)
            cycle = run_stage_actions(s, stage.after_monitoring, stage=stage.name, phase="after_monitoring", cycle=cycle, rows=rows, started=started)
            if stage.completion and stage.after_monitoring:
                dose = next(e for e in stage.after_monitoring if e["action"] == "infuse")
                replay = replay_journal(s.paths.journal_jsonl)
                intent = next(e for e in reversed(replay.records) if e["event_type"] == "dose_dispatch_intent")
                completed = [e for e in replay.records if e["sequence"] > intent["sequence"] and e.get("operation_type") == "infuse" and e.get("channel") == 2 and e.get("lifecycle_state") == "completed"]
                if not replay.valid or len(completed) != 1:
                    raise VerificationError("Exactly one matched durable reagent move is required")
                move = completed[0]
                confirmed = s.record("dose_confirmed", dose_id=s.dose_guard.data["dose_id"], experiment_id=s.dose_guard.data["experiment_id"], configuration_sha256=s.dose_guard.data["configuration_sha256"], pump_operation_id=move["operation_id"], pump_completion_sequence=move["sequence"], channel=2, volume_ml=dose["volume_ml"], rate_ml_min=dose["rate_ml_min"], result_classification="timed_move_stop_and_journal_confirmed")
                s.dose_guard.mark("CONFIRMED", completion_journal_sequence=confirmed["sequence"], channel=2, volume_ml=dose["volume_ml"], rate_ml_min=dose["rate_ml_min"])
                boundary = s.simulation.now() if s.simulation else s.last_acquisition_completed_at
                # Actual dose alignment uses last metadata time plus monotonic
                # duration from that acquisition, rather than filename timing.
                elapsed = ((boundary - s.first_acquisition_at).total_seconds() / 3600 if s.simulation else float(rows[-1]["elapsed_hours"]) + (time.monotonic() - s.last_acquisition_completed_monotonic) / 3600) if s.first_acquisition_at is not None and rows[-1].get("elapsed_hours") is not None else None
                s.transition_evidence = dict(s.dose_guard.data, elapsed_hours=elapsed, timing_source="virtual journal clock" if s.simulation else "journal boundary aligned to last JCAMP LONG DATE", confirmation_utc=confirmed["timestamp_utc"])
                write_json_atomic(s.paths.run_dir / "transition" / "channel2_addition.json", s.transition_evidence)
            if stage.completion and not area_only:
                write_stage_reports(s.paths, rows, stages, dataset=s.dataset_name, transition=s.transition_evidence)
            return outcome
        def measurement(schedule: base.MeasurementSchedule) -> base.MeasurementObservation:
            nonlocal cycle
            cycle += 1
            s.record("measurement_scheduled", workflow_phase=stage.name, cycle_number=cycle, scheduled_time=schedule.scheduled_time, actual_start=schedule.actual_cycle_start, scheduling_delay_seconds=schedule.scheduling_delay_seconds)
            row = sample_cycle(s, stage=stage.name, cycle=cycle, rows=rows, started=started)
            row.update(scheduled_measurement_number=schedule.scheduled_measurement_number,
                       stage_started_at=schedule.stage_started_at, scheduled_measurement_time=schedule.scheduled_time,
                       actual_cycle_start=schedule.actual_cycle_start, scheduling_delay_seconds=schedule.scheduling_delay_seconds,
                       nmr_acquisition_started_at=s.last_acquisition_started_at.isoformat(timespec="seconds"),
                       nmr_acquisition_completed_at=s.last_acquisition_completed_at.isoformat(timespec="seconds"),
                       analysis_completed_at=s.last_analysis_completed_at.isoformat(timespec="seconds"))
            base.write_csv(s.paths.time_series_csv, rows, base.TIME_SERIES_COLUMNS)
            if stage.completion and not area_only:
                write_stage_reports(s.paths, rows, stages, dataset=s.dataset_name, transition=s.transition_evidence)
            return base.MeasurementObservation(
                True, bool(row["plateau"]),
                s.last_acquisition_started_at.isoformat(timespec="seconds"),
                s.last_acquisition_completed_at.isoformat(timespec="seconds"),
                s.last_analysis_completed_at.isoformat(timespec="seconds"),
            )
        extension = 0
        while True:
            clock = {"monotonic_fn": s.simulation.monotonic, "wall_now_fn": s.simulation.now} if s.simulation else {}
            outcome = base.run_monitoring_stage(stage, measurement, recorder=s.recorder, sleep_fn=s.sleep, **clock).outcome
            if outcome.stage_outcome in (base.StageOutcome.PLATEAU_REACHED, base.StageOutcome.SCHEDULED_MONITORING_COMPLETED):
                return finish(outcome)
            # A limit without plateau is never taken as chemistry complete.
            s.record("stage_limit_reached", workflow_phase=stage.name, extension=extension, stage_outcome=outcome.stage_outcome.value if outcome.stage_outcome else None, plateau_reached=False, result_classification="plateau_not_reached")
            if area_only:
                status = (base.TerminalStatus.STAGE_1_MAX_ITERATIONS_REACHED if stage is stages[0] else base.TerminalStatus.STAGE_2_MAX_ITERATIONS_REACHED) if outcome.stage_outcome is base.StageOutcome.PLATEAU_NOT_REACHED_WITHIN_LIMIT else outcome.status
                return base.RunOutcome(status, f"Stage {stage.name} ended at its configured operational limit without the area endpoint; no next stage or dose.", outcome.stage_outcome)
            if stage.completion:
                return base.RunOutcome(base.TerminalStatus.ANALYSIS_INCONCLUSIVE, f"Stage {stage.name} reached its configured limit without chemical completion; transition refused.", outcome.stage_outcome)
            choice = decide(stage, outcome, extension)
            if choice == "continue":
                extension += 1
                continue
            if choice == "advance":
                advanced.append(stage.name)
                return finish(base.RunOutcome(base.TerminalStatus.COMPLETED, f"Operator advanced past stage {stage.name} without a plateau.", base.StageOutcome.OPERATOR_ADVANCED_WITHOUT_PLATEAU))
            return base.RunOutcome(base.TerminalStatus.OPERATOR_ABORTED, f"Stage {stage.name} reached its limit without a plateau; the experiment was ended at a stage decision.", outcome.stage_outcome)
    try:
        outcome = base.run_stage_sequence(stages, operator, stage_runner, recorder=s.recorder)
    except MeasurementFailedAfterCleanup as exc:
        outcome = base.RunOutcome(base.TerminalStatus.INSTRUMENT_FAILURE if area_only else base.TerminalStatus.ANALYSIS_INCONCLUSIVE, f"{exc}. The experiment stopped; review the failed measurement before any new run or stage.")
        s.record("terminal", workflow_phase="run", terminal_status=outcome.status.value, result_classification="measurement_failed_cleanup_completed", physical_state_certainty="certain", operator_review_required=True, failed_stage=exc.stage, failed_cycle=exc.cycle, failed_step=exc.failed_step, error_message=outcome.message)
        if area_only:
            final_reports(s, rows, stages, outcome)
        elif any(stage.completion for stage in stages):
            write_stage_reports(s.paths, rows, stages, dataset=s.dataset_name, outcome=outcome, transition=s.transition_evidence)
        return outcome
    except base.OperatorAbortError as exc:
        # Reagent checkpoints only occur between stages, with the rig at rest.
        outcome = base.RunOutcome(base.TerminalStatus.OPERATOR_ABORTED, f"Operator declined a reagent checkpoint: {exc}")
        s.record("terminal", workflow_phase="run", terminal_status=outcome.status.value, result_classification="checkpoint_declined", physical_state_certainty="certain", error_message=outcome.message)
        if area_only:
            final_reports(s, rows, stages, outcome)
        return outcome
    except BaseException as exc:
        # Stop physical motion before attempting any retrospective output.
        if area_only:
            s.stop_all(stage="run", cycle=cycle)
            s.needle.stop_best_effort()
            failure_outcome = base.outcome_from_exception(exc, s.state)
            if failure_outcome.status is base.TerminalStatus.ANALYSIS_INCONCLUSIVE:
                failure_outcome = base.RunOutcome(base.TerminalStatus.INSTRUMENT_FAILURE, str(exc))
            final_reports(s, rows, stages, failure_outcome)
        raise
    if outcome.status is base.TerminalStatus.COMPLETED and advanced:
        outcome = base.RunOutcome(base.TerminalStatus.COMPLETED, "All configured stages finished; the operator advanced without a plateau after: " + ", ".join(advanced) + ".", outcome.stage_outcome)
    s.record("terminal", workflow_phase="run", terminal_status=outcome.status.value, result_classification=outcome.stage_outcome.value if outcome.stage_outcome else outcome.status.value, physical_state_certainty="certain", stages_advanced_without_plateau=advanced or None)
    if area_only:
        final_reports(s, rows, stages, outcome)
    elif any(stage.completion for stage in stages):
        figures = write_stage_reports(s.paths, rows, stages, dataset=s.dataset_name, outcome=outcome, transition=s.transition_evidence)
        s.plot_manifest = [*(s.plot_manifest or []), *figures]
        s.write_manifest()
    return outcome


def final_reports(s, rows, stages, outcome):
    """Retrospective work cannot alter an already established terminal decision."""
    try:
        figures = write_final_qc(s.paths, rows, stages, s.raw["qc_reporting"], dataset=s.dataset_name, outcome=outcome, transition=s.transition_evidence)
        if rows and all(r.get("acquired_at") and r.get("elapsed_hours") is not None for r in rows):
            figures += write_stage_reports(s.paths, rows, stages, dataset=s.dataset_name, outcome=outcome, transition=s.transition_evidence)
        s.plot_manifest = [*(s.plot_manifest or []), *figures]
        s.write_manifest()
    except Exception as exc:
        print(f"Retrospective report unavailable ({type(exc).__name__}: {exc}); experiment outcome remains {outcome.status.value}.")
