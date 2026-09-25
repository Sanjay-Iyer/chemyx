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
import re
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Callable

from arduino.python.protocol import bool_field
from arduino.python.config import load_arduino_config, test3_missing
from arduino.python.needle_state import TrackedNeedle
from arduino.python.controller import NeedleController
from arduino.python.discovery import resolve_arduino_port
from arduino.python.errors import LiveExecutionBlocked, PositionUncertainError
from arduino.python.results import matching_live_result, unresolved_live_motion_failure
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
from ..runtime_state import write_json_atomic
from . import si6_automated_nmr as base

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
STAGE_DECISIONS = {"CONTINUE": "continue", "ADVANCE": "advance", "ABORT": "abort"}


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
    root = Path(raw["output"]["run_root_dir"])
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

    @property
    def mock(self) -> bool:
        return self.identity.mock

    @property
    def dataset_name(self) -> str:
        return self.paths.run_dir.name

    def record(self, name: str, **fields: Any) -> None:
        self.recorder.record(name, **fields)

    def assert_pump_idle(self) -> None:
        if self.state.motion_active or self.state.uncertain:
            raise VerificationError("Pump motion or physical-state uncertainty blocks needle movement")
        # The Chemyx protocol's STOP acknowledgement is the available idle evidence.
        if self.state.last_stop_status is not base.StopStatus.SUCCEEDED:
            raise VerificationError("Chemyx STOP has not been positively confirmed")

    def move_needle(self, label: str) -> dict[str, str]:
        self.assert_pump_idle()
        up, down, speed = positions(self.arduino_cfg, mock=self.mock)
        target = up if label == "UP" else down
        self.record("needle_transition", workflow_phase="needle", target=label, target_steps=target, result_classification="started")
        self.needle.move_absolute(target, speed)
        status = verify_needle(self.needle, target)
        self.record("needle_transition", workflow_phase="needle", target=label, target_steps=target, verified_status=status, result_classification="completed")
        return status

    def pump_move(self, direction: str, volume: float, needle_label: str, *, stage: str, cycle: int) -> None:
        up, down, _ = positions(self.arduino_cfg, mock=self.mock)
        status = verify_needle(self.needle, up if needle_label == "UP" else down)
        self.record("pump_needle_context", workflow_phase=stage, cycle_number=cycle, needle_state=needle_label, needle_status=status, operation_type=direction, requested_volume_ml=volume)
        base.run_safe_metered_move(
            self.pump, self.pump_cfg, direction, volume, self.state,
            extra_seconds=float(self.raw["workflow"].get("pump_extra_seconds", 2.0)),
            sleep_fn=self.sleep, recorder=self.recorder,
            workflow_phase=stage, cycle_number=cycle,
        )
        self.state.retained_volume_ml += volume if direction == "withdraw" else -volume
        self.assert_pump_idle()

    def verify_cleanup_preconditions(self, down_steps: int, expected_retained_ml: float) -> dict[str, str]:
        """Prove pump and needle state before returning the sample automatically."""
        if self.state.persistence_errors:
            raise PhysicalStateUncertain("Journal persistence failed, so an automatic recovery could not be recorded")
        try:
            self.assert_pump_idle()
        except VerificationError as exc:
            raise PhysicalStateUncertain(f"Pump is not proven idle: {exc}") from exc
        if abs(self.state.retained_volume_ml - expected_retained_ml) > 1e-6:
            raise PhysicalStateUncertain(
                f"Estimated retained volume {self.state.retained_volume_ml:g} mL is not the expected {expected_retained_ml:g} mL"
            )
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
        self.last_acquisition_started_at = datetime.now()
        path = self.acquire(self.nmr_cfg, self.paths.raw_dir, label=f"{stage}_{cycle:04d}")
        self.last_acquisition_completed_at = datetime.now()
        if progress:
            progress("NMR acquisition")
        self.measurement_step = "NMR data retrieval"
        if not path.is_file() or path.stat().st_size == 0:
            raise VerificationError("NMR acquisition returned no nonempty retrieved data file")
        self.record("nmr_retrieved", workflow_phase=stage, cycle_number=cycle, result_path=str(path.relative_to(self.paths.run_dir)), scans=self.nmr_cfg.scans)
        if progress:
            progress("NMR data retrieval")
        self.measurement_step = "NMR processing"
        processed = self.process(path, self.paths, self.dataset_name)
        if not processed.is_dir():
            raise VerificationError("NMR process_fid output directory is missing")
        self.record("analysis_result", workflow_phase=stage, cycle_number=cycle, analysis_type=getattr(self.process, "last_kind", "process_fid_full_spectrum"), result_classification="completed", result_path=str(processed.relative_to(self.paths.run_dir)))
        if progress:
            progress("NMR processing")
        self.measurement_step = "LONG DATE metadata"
        long_date = str(read_jcamp_fid(path).metadata.get("LONG DATE", "")).strip()
        try:
            acquired_at = datetime.strptime(long_date, "%Y/%m/%d %H:%M:%S%z")
        except ValueError as exc:
            raise VerificationError("Authoritative JCAMP LONG DATE acquisition time is unavailable") from exc
        timestamp_source = "LONG DATE header"
        if self.first_acquisition_at is None:
            self.first_acquisition_at = acquired_at
        metadata = {"iteration": cycle, "stage": stage, "stage_iteration": len([r for r in rows if r.get("stage") == stage]) + 1,
                    "elapsed_hours": (acquired_at - self.first_acquisition_at).total_seconds() / 3600,
                    "acquired_at": acquired_at.isoformat(timespec="seconds"), "timestamp_source": timestamp_source,
                    "target_ppm": self.nmr_cfg.target_ppm,
                    "dataset_display_name": self.dataset_name}
        self.record("nmr_acquisition_time", workflow_phase=stage, cycle_number=cycle, acquired_at=metadata["acquired_at"], timestamp_source=timestamp_source)
        self.measurement_step = "NMR analysis"
        row, spectrum = self.analyze(path, processed, self.paths, self.raw["analysis"], metadata)
        if row.get("plot_file") and row.get("plot_title"):
            if self.plot_manifest is None:
                self.plot_manifest = []
            self.plot_manifest.append({"file": row["plot_file"], "dataset_display_name": self.dataset_name, "visible_title": row["plot_title"], "stage": stage, "cycle_number": cycle, "measurement_valid": bool(row.get("peak_clear"))})
            self.write_manifest()
        summary = {"peak_ppm": row.get("peak_ppm"), "peak_area": row.get("peak_area"), "snr": row.get("snr"), "metric_source": row.get("metric_source")}
        if not row.get("peak_clear"):
            self.record("analysis_result", workflow_phase=stage, cycle_number=cycle, result_classification="invalid", analysis_result=summary, error_message=row.get("qc_failure_reasons"))
            raise base.AnalysisInconclusiveError(f"Tracked resonance failed the configured checks: {row.get('qc_failure_reasons') or 'unknown reason'}")
        self.measurement_step = "plateau evaluation"
        stage_rows = [r for r in rows if r.get("stage") == stage]
        previous = float(stage_rows[-1]["peak_area"]) if stage_rows else None
        row["growth_percent"] = base.growth_percent(previous, float(row["peak_area"]), epsilon=float(self.raw["analysis"]["area_epsilon"]))
        row["plateau"] = base.plateau_reached([*stage_rows, row], self.raw["analysis"])
        rows.append(row)
        base.write_csv(self.paths.time_series_csv, rows, base.TIME_SERIES_COLUMNS)
        base.append_spectra(self.paths.spectra_csv, spectrum)
        self.record("analysis_result", workflow_phase=stage, cycle_number=cycle, result_classification="valid", analysis_result=dict(summary, growth_percent=row["growth_percent"]), plateau_progress={"plateau_reached": row["plateau"], "required_intervals": self.raw["analysis"]["plateau_consecutive_intervals"]})
        self.last_analysis_completed_at = datetime.now()
        self.measurement_step = None
        if progress:
            progress("NMR analysis")
        return row, path, processed


def _record_failure(s: Services, *, stage: str, cycle: int, step: str, exc: BaseException, recovery_attempted: bool) -> None:
    """Journal a stopped cycle without letting a journal fault mask the stop."""
    needle_uncertain = not getattr(s.needle, "position_certain", True)
    uncertain = s.state.uncertain or needle_uncertain or isinstance(exc, (PositionUncertainError, PhysicalStateUncertain))
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
    s.record("cycle_status", workflow_phase=stage, cycle_number=cycle, status="STARTED", started_at=datetime.now().isoformat(timespec="seconds"))
    step = "verify UP"
    failure: Exception | None = None
    failed_step = ""
    try:
        up, down, _ = positions(s.arduino_cfg, mock=s.mock)
        verify_needle(s.needle, up)
        step = "initial withdraw"
        s.pump_move("withdraw", values["initial_withdraw_ml"], "UP", stage=stage, cycle=cycle)
        step = "needle DOWN"
        s.move_needle("DOWN")
        step = "sample withdraw"
        s.pump_move("withdraw", values["sample_withdraw_ml"], "DOWN", stage=stage, cycle=cycle)
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
            needle_status = s.verify_cleanup_preconditions(down, values["initial_withdraw_ml"] + values["sample_withdraw_ml"])
            s.record("recovery_cleanup", workflow_phase=stage, cycle_number=cycle, result_classification="started", failed_step=failed_step, reason="measurement failed; pump idle and needle at DOWN verified", needle_status=needle_status)
        else:
            s.record("cycle_status", workflow_phase=stage, cycle_number=cycle, status="NMR_COMPLETE", raw_path=str(path.relative_to(s.paths.run_dir)), processed_path=str(processed.relative_to(s.paths.run_dir)))
        step = "return infusion while DOWN"
        s.pump_move("infuse", values["return_infuse_ml"], "DOWN", stage=stage, cycle=cycle)
        step = "needle UP"
        s.move_needle("UP")
        step = "cleanup withdraw"
        s.pump_move("withdraw", values["cleanup_withdraw_ml"], "UP", stage=stage, cycle=cycle)
        step = "cleanup infusion"
        s.pump_move("infuse", values["cleanup_infuse_ml"], "UP", stage=stage, cycle=cycle)
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

    def __init__(self, tracked_window: tuple[float, float]) -> None:
        self.tracked_window = tracked_window
        self.last_kind = "process_fid_full_spectrum"

    def __call__(self, dx_path: Path, paths: base.RunPaths, dataset_display_name: str) -> Path:
        key = f"{hashlib.sha256(dx_path.read_bytes()).hexdigest()}:{self.tracked_window}"
        cached = _MOCK_PROCESSED.get(key)
        if cached is None or not cached[0].is_dir():
            processed = base.run_process_fid_postprocessing(dx_path, paths, dataset_display_name, tracked_window=self.tracked_window)
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
        self.last_kind = "mock_process_fid_reused"
        return output


def tracked_window(raw: dict[str, Any], nmr_cfg: config.NmrSettings) -> tuple[float, float]:
    return float(nmr_cfg.target_ppm), float(raw["analysis"]["detection_window_ppm"])


def validate_stage_policy(stages: list[base.Stage]) -> None:
    """Refuse an explicit measurement cap that would end a stage before its duration."""
    for stage in stages:
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
    root = Path(raw["output"]["run_root_dir"])
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
    cycle_values(raw)
    validate_stage_policy(base.build_stages(raw["workflow"]))
    arduino_cfg = load_arduino_config(arduino_path)
    if mock or require_needle_live:
        positions(arduino_cfg, mock=mock)
    pump_cfg, nmr_cfg = base.build_instrument_settings(raw, machine_path)
    if not mock and require_needle_live:
        root = arduino_cfg["results"]["run_root_dir"]
        records = {name: matching_live_result(root, test, arduino_cfg) is not None for name, test in (
            ("test_02", "test_02_unloaded_motor"), ("test_03", "test_03_needle_axis"))}
        missing = test3_missing(arduino_cfg, test2_record_valid=records["test_02"])
        if unresolved_live_motion_failure(root, arduino_cfg):
            missing.append("Unresolved live motion failure requires inspection clearance")
        if missing:
            raise LiveExecutionBlocked("Three-instrument needle preflight", missing)
        if not pump_cfg.port or not nmr_cfg.host:
            raise ValueError("Live Chemyx serial port and NMR host must be configured")
        check_previous_run_review(raw, acknowledged_review)
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
    paths = create_identified_run(raw, identity)
    recorder = base.create_run_recorder(paths)
    write_json_atomic(paths.run_dir / "config_snapshot.json", raw)
    write_json_atomic(paths.run_dir / "arduino_config_snapshot.json", {k: v for k, v in arduino_cfg.items() if not k.startswith("_")})
    write_json_atomic(paths.manifest_json, manifest_payload(paths, identity, raw, []))
    recorder.record("phase_transition", previous_state=None, new_state="initializing", workflow_phase="initializing", result_classification=identity.mode, mode=identity.mode, run_kind=identity.kind, diagnostic_selection=identity.selection, acknowledged_previous_run=acknowledged_review)
    state = base.PumpSafetyState(float(raw["pump"].get("initial_retained_volume_ml", 0)))
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
    controller = NeedleController(transport, expected_device=settings["expected_device"], expected_board=settings["expected_board"], expected_version=settings.get("expected_version"), ready_timeout_s=settings["ready_timeout_s"], command_timeout_s=settings["command_timeout_s"], overall_timeout_s=max(3600, (total_hours + 2) * 3600), allow_motion=True, motion_guard=lambda: not state.motion_active and not state.uncertain)
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
                tracked_cfg["needle"] = dict(arduino_cfg["needle"], steps_per_unit=20, up_step_sign=1)
                tracked_cfg["motion"] = dict(
                    arduino_cfg["motion"], maximum_speed_steps_s=100,
                    maximum_acceleration_steps_s2=300,
                )
            needle = TrackedNeedle(controller, tracked_cfg, state_path=paths.run_dir / "mock_needle_state.json" if mock else None)
            if mock:
                needle.confirm_home(operator_confirmed=True)
            with pump:
                base.configure_pump(pump, pump_cfg)
                if base.attempt_emergency_stop(pump, state, recorder) is not base.StopStatus.SUCCEEDED:
                    raise VerificationError("Initial Chemyx STOP was not confirmed")
                window = tracked_window(raw, nmr_cfg)
                processor = MockProcessFid(window) if mock and fast_mock_processing else partial(base.run_process_fid_postprocessing, tracked_window=window)
                yield Services(
                    needle=needle, pump=pump, pump_cfg=pump_cfg, nmr_cfg=nmr_cfg, raw=raw,
                    arduino_cfg=arduino_cfg, paths=paths, recorder=recorder, state=state,
                    acquire=mock_acquire if mock else base.run_nmr_acquisition, process=processor,
                    analyze=analyze_tracked_resonance,
                    sleep=(lambda _label, _seconds: None) if mock else base.sleep_with_progress,
                    identity=identity,
                )
    except BaseException as exc:
        if pump.is_connected:
            base.attempt_emergency_stop(pump, state, recorder)
        if controller.is_open:
            if "needle" in locals():
                needle.stop_best_effort()
            else:
                controller.stop_best_effort()
        try:
            recorder.record("terminal", workflow_phase="run", terminal_status="failed", result_classification=type(exc).__name__, error_message=str(exc), physical_state_certainty="uncertain" if state.uncertain else "requires_inspection")
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
        step("Chemyx withdraw", lambda: s.pump_move("withdraw", float(d["test_withdraw_ml"]), "UP", stage="diagnostic", cycle=1))
        step("Chemyx infuse", lambda: s.pump_move("infuse", float(d["test_infuse_ml"]), "UP", stage="diagnostic", cycle=1))
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
            answer = input_fn("Type CONTINUE, ADVANCE, or ABORT: ").strip().upper()
            if answer in STAGE_DECISIONS:
                return STAGE_DECISIONS[answer]
            print("Unrecognized answer.")
    except (EOFError, KeyboardInterrupt):
        pass
    return "abort"


def run_experiment(s: Services, *, mock_cycles_per_stage: int = 4, stage_decision: Callable[[base.Stage, base.RunOutcome], str] | None = None) -> base.RunOutcome:
    preflight(s)
    home_and_raise(s)
    rows: list[dict] = []
    started = datetime.now()
    cycle = 0
    advanced: list[str] = []
    stages = base.build_stages(s.raw["workflow"])
    if s.mock:
        stages = [replace(stage, interval_minutes=0.0001, max_hours=1, max_measurements=mock_cycles_per_stage, max_measurements_explicit=True) for stage in stages]
    def operator(prompt: str) -> None:
        if s.mock:
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
        def measurement(schedule: base.MeasurementSchedule) -> base.MeasurementObservation:
            nonlocal cycle
            cycle += 1
            s.record("measurement_scheduled", workflow_phase=stage.name, cycle_number=cycle, scheduled_time=schedule.scheduled_time, actual_start=schedule.actual_cycle_start, scheduling_delay_seconds=schedule.scheduling_delay_seconds)
            row = sample_cycle(s, stage=stage.name, cycle=cycle, rows=rows, started=started)
            return base.MeasurementObservation(
                True, bool(row["plateau"]),
                s.last_acquisition_started_at.isoformat(timespec="seconds"),
                s.last_acquisition_completed_at.isoformat(timespec="seconds"),
                s.last_analysis_completed_at.isoformat(timespec="seconds"),
            )
        extension = 0
        while True:
            outcome = base.run_monitoring_stage(stage, measurement, recorder=s.recorder, sleep_fn=s.sleep).outcome
            if outcome.stage_outcome in (base.StageOutcome.PLATEAU_REACHED, base.StageOutcome.SCHEDULED_MONITORING_COMPLETED):
                return outcome
            # A limit without plateau is never taken as chemistry complete.
            s.record("stage_limit_reached", workflow_phase=stage.name, extension=extension, stage_outcome=outcome.stage_outcome.value if outcome.stage_outcome else None, plateau_reached=False, result_classification="plateau_not_reached")
            choice = decide(stage, outcome, extension)
            if choice == "continue":
                extension += 1
                continue
            if choice == "advance":
                advanced.append(stage.name)
                return base.RunOutcome(base.TerminalStatus.COMPLETED, f"Operator advanced past stage {stage.name} without a plateau.", base.StageOutcome.OPERATOR_ADVANCED_WITHOUT_PLATEAU)
            return base.RunOutcome(base.TerminalStatus.OPERATOR_ABORTED, f"Stage {stage.name} reached its limit without a plateau; the experiment was ended at a stage decision.", outcome.stage_outcome)
    try:
        outcome = base.run_stage_sequence(stages, operator, stage_runner, recorder=s.recorder)
    except MeasurementFailedAfterCleanup as exc:
        outcome = base.RunOutcome(base.TerminalStatus.ANALYSIS_INCONCLUSIVE, f"{exc}. The experiment stopped; review the failed measurement before any new run or stage.")
        s.record("terminal", workflow_phase="run", terminal_status=outcome.status.value, result_classification="measurement_failed_cleanup_completed", physical_state_certainty="certain", operator_review_required=True, failed_stage=exc.stage, failed_cycle=exc.cycle, failed_step=exc.failed_step, error_message=outcome.message)
        return outcome
    except base.OperatorAbortError as exc:
        # Reagent checkpoints only occur between stages, with the rig at rest.
        outcome = base.RunOutcome(base.TerminalStatus.OPERATOR_ABORTED, f"Operator declined a reagent checkpoint: {exc}")
        s.record("terminal", workflow_phase="run", terminal_status=outcome.status.value, result_classification="checkpoint_declined", physical_state_certainty="certain", error_message=outcome.message)
        return outcome
    if outcome.status is base.TerminalStatus.COMPLETED and advanced:
        outcome = base.RunOutcome(base.TerminalStatus.COMPLETED, "All configured stages finished; the operator advanced without a plateau after: " + ", ".join(advanced) + ".", outcome.stage_outcome)
    s.record("terminal", workflow_phase="run", terminal_status=outcome.status.value, result_classification=outcome.stage_outcome.value if outcome.stage_outcome else outcome.status.value, physical_state_certainty="certain", stages_advanced_without_plateau=advanced or None)
    return outcome
