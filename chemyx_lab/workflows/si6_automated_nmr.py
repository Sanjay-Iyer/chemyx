"""Automated Si6 pump/NMR kinetics workflow with timestamped outputs.

Pump and NMR work is automated. Needle movement and reagent additions remain
explicit operator checkpoints because this repository has no needle-position
or reagent-addition actuator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..analysis.nmr import (
    NmrProcessingError,
    analyze_dx_peak,
    build_magnitude_spectrum,
    plot_peak_region,
)
from ..analysis.plot_titles import dataset_plot_title
from ..instruments.chemyx import EchoMismatchError, Pump, PumpConnectionError
from ..instruments.nmr import NmrRpcError
from ..recovery import format_inspection, inspect_run
from ..runtime_journal import (
    EventRecorder,
    JournalError,
    OperationJournal,
    RunRecorder,
    SnapshotWriteError,
    discover_git_commit,
)
from ..runtime_state import write_json_atomic
from .instrument_operations import (
    configure_pump,
    move_seconds,
    run_nmr_acquisition,
    sleep_with_progress,
)


DEFAULT_CONFIG = config.REPO_ROOT / "configs" / "experiments" / "02_si6_automated_nmr.yaml"
TIME_SERIES_COLUMNS = [
    "iteration", "stage", "stage_iteration", "scheduled_measurement_number",
    "acquisition_attempt_number", "valid_analysis_index", "stage_started_at",
    "scheduled_measurement_time", "actual_cycle_start",
    "scheduling_delay_seconds", "nmr_acquisition_started_at",
    "nmr_acquisition_completed_at", "analysis_completed_at", "acquired_at",
    "elapsed_hours",
    "file", "target_ppm", "peak_ppm", "peak_height", "peak_area", "snr",
    "prominence", "prominence_snr", "width_ppm", "baseline", "noise",
    "growth_percent", "peak_clear", "plateau", "plot_file", "error",
]


class TerminalStatus(str, Enum):
    COMPLETED = "completed"
    VALIDATION_FAILURE = "validation_failure"
    OPERATOR_ABORTED = "operator_aborted"
    MAXIMUM_DURATION_REACHED = "maximum_duration_reached"
    INSTRUMENT_FAILURE = "instrument_failure"
    ANALYSIS_INCONCLUSIVE = "analysis_inconclusive"
    PLATEAU_NOT_REACHED_WITHIN_LIMIT = "plateau_not_reached_within_limit"
    SAFETY_STOP = "safety_stop"
    UNEXPECTED_FAILURE = "unexpected_failure"


EXIT_CODES = {
    TerminalStatus.COMPLETED: 0,
    TerminalStatus.VALIDATION_FAILURE: 2,
    TerminalStatus.OPERATOR_ABORTED: 3,
    TerminalStatus.MAXIMUM_DURATION_REACHED: 4,
    TerminalStatus.INSTRUMENT_FAILURE: 5,
    TerminalStatus.SAFETY_STOP: 6,
    TerminalStatus.ANALYSIS_INCONCLUSIVE: 7,
    TerminalStatus.UNEXPECTED_FAILURE: 8,
    TerminalStatus.PLATEAU_NOT_REACHED_WITHIN_LIMIT: 9,
}


class StageOutcome(str, Enum):
    SCHEDULED_MONITORING_COMPLETED = "scheduled_monitoring_completed"
    PLATEAU_REACHED = "plateau_reached"
    PLATEAU_NOT_REACHED_WITHIN_LIMIT = "plateau_not_reached_within_limit"
    RUNTIME_LIMIT_REACHED = "runtime_limit_reached"


class StopStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class RunOutcome:
    status: TerminalStatus
    message: str
    stage_outcome: StageOutcome | None = None

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]


@dataclass
class PumpSafetyState:
    retained_volume_ml: float = 0.0
    motion_active: bool = False
    current_operation: str | None = None
    uncertain: bool = False
    uncertain_operation: str | None = None
    uncertain_reason: str | None = None
    stop_attempts: int = 0
    last_stop_status: StopStatus | None = None
    last_stop_error: str | None = None
    persistence_errors: list[str] | None = None

    def mark_uncertain(self, reason: str) -> None:
        self.uncertain = True
        self.uncertain_operation = self.current_operation
        self.uncertain_reason = reason

    def record_persistence_error(self, exc: BaseException) -> None:
        if self.persistence_errors is None:
            self.persistence_errors = []
        self.persistence_errors.append(f"{type(exc).__name__}: {exc}")


class OperatorAbortError(RuntimeError):
    """Raised when the operator declines or cannot complete a checkpoint."""


class PumpStateUncertainError(RuntimeError):
    """Raised when software cannot prove the outcome of a physical transfer."""


class AnalysisInconclusiveError(RuntimeError):
    """Raised when acquired data cannot support a completion decision."""


@dataclass(frozen=True)
class Stage:
    name: str
    operator_prompt: str
    interval_minutes: float
    max_hours: float
    measure_immediately: bool = True
    plateau_stopping_enabled: bool = True
    max_measurements: int = 1


@dataclass(frozen=True)
class MeasurementSchedule:
    stage_name: str
    stage_started_at: str
    scheduled_measurement_number: int
    acquisition_attempt_number: int
    valid_analysis_count_before: int
    scheduled_time: str
    scheduled_monotonic: float
    actual_cycle_start: str
    actual_cycle_start_monotonic: float
    scheduling_delay_seconds: float


@dataclass(frozen=True)
class MeasurementObservation:
    valid_analysis: bool
    plateau_detected: bool
    nmr_acquisition_started_at: str
    nmr_acquisition_completed_at: str
    analysis_completed_at: str


@dataclass(frozen=True)
class CycleAcquisitionResult:
    path: Path
    acquisition_started_at: datetime
    acquisition_completed_at: datetime


@dataclass(frozen=True)
class MonitoringResult:
    outcome: RunOutcome
    scheduled_measurements: int
    acquisition_attempts: int
    valid_analysis_count: int
    plateau_measurement_number: int | None = None


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    raw_dir: Path
    plots_dir: Path
    time_series_csv: Path
    spectra_csv: Path
    operations_csv: Path
    manifest_json: Path
    journal_jsonl: Path | None = None
    state_json: Path | None = None


@dataclass(frozen=True)
class CapacityRequirement:
    syringe_capacity_ml: float
    initial_retained_volume_ml: float
    safety_margin_ml: float
    maximum_retained_volume_ml: float
    end_retained_volume_ml: float


def load_si6_config(path: Path) -> dict[str, Any]:
    raw = config.read_mapping_config(path, "Si6 experiment config")
    required = {"workflow", "pump", "nmr", "analysis", "output"}
    missing = sorted(required - set(raw))
    unknown = sorted(set(raw) - required)
    if missing:
        raise ValueError(f"Missing Si6 config section(s): {', '.join(missing)}")
    if unknown:
        raise ValueError(f"Unknown Si6 config section(s): {', '.join(unknown)}")

    workflow = _mapping(raw["workflow"], "workflow")
    _reject_unknown(
        workflow,
        {
            "name", "description", "cycle", "pump_extra_seconds",
            "initial_stage", "first_addition_stage",
            "repeat_addition_rounds", "repeating_stages",
        },
        "workflow",
    )
    cycle = workflow.get("cycle")
    if not isinstance(cycle, list) or not cycle:
        raise ValueError("workflow.cycle must be a non-empty list")
    for index, event in enumerate(cycle, start=1):
        event = _mapping(event, f"workflow.cycle[{index}]")
        _reject_unknown(
            event,
            {"action", "volume_ml", "seconds", "prompt", "note"},
            f"workflow.cycle[{index}]",
        )
        action = str(event.get("action", "")).lower()
        if action not in {"withdraw", "infuse", "pause", "nmr", "operator"}:
            raise ValueError(f"Unknown cycle action {action!r} at item {index}")
        if action in {"withdraw", "infuse"} and _positive(event.get("volume_ml"), f"cycle[{index}].volume_ml") <= 0:
            raise ValueError("Pump volumes must be positive")
        if action == "pause" and _nonnegative(event.get("seconds"), f"cycle[{index}].seconds") < 0:
            raise ValueError("Pause seconds cannot be negative")
        if action == "operator" and not str(event.get("prompt", "")).strip():
            raise ValueError(f"cycle[{index}] operator action needs a prompt")

    stages = build_stages(workflow)
    if not stages:
        raise ValueError("At least one stage is required")
    analysis = _mapping(raw["analysis"], "analysis")
    _reject_unknown(
        _mapping(raw["pump"], "pump"),
        {
            "channel", "syringe_diameter_mm", "syringe_capacity_ml",
            "initial_retained_volume_ml", "syringe_safety_margin_ml",
            "units", "rate_ml_min", "default_volume_ml",
        },
        "pump",
    )
    _reject_unknown(
        _mapping(raw["nmr"], "nmr"),
        {
            "route", "result_type", "scans", "receiver_gain", "auto_gain",
            "spectral_center", "sweep_width", "target_ppm",
        },
        "nmr",
    )
    _reject_unknown(
        analysis,
        {
            "detection_window_ppm", "integration_window_ppm",
            "plot_window_ppm", "line_broadening_hz", "min_peak_snr",
            "min_prominence_snr", "min_peak_area", "area_epsilon",
            "plateau_max_growth_percent", "plateau_max_decline_percent",
            "plateau_consecutive_intervals",
        },
        "analysis",
    )
    _reject_unknown(
        _mapping(raw["output"], "output"),
        {"run_root_dir"},
        "output",
    )
    for key in (
        "detection_window_ppm", "integration_window_ppm", "plot_window_ppm",
        "min_peak_snr", "min_prominence_snr", "min_peak_area",
        "area_epsilon", "plateau_max_growth_percent",
        "plateau_max_decline_percent",
    ):
        _nonnegative(analysis.get(key), f"analysis.{key}")
    intervals = int(analysis.get("plateau_consecutive_intervals", 0))
    if intervals < 1:
        raise ValueError("analysis.plateau_consecutive_intervals must be at least 1")
    if bool(_mapping(raw["nmr"], "nmr").get("auto_gain", False)):
        raise ValueError("nmr.auto_gain must be false for comparable peak areas over time")
    result_type = str(raw["nmr"].get("result_type", "fid")).lower()
    if result_type != "fid":
        raise ValueError("nmr.result_type must be 'fid' for automated processing")
    validate_syringe_capacity(raw)
    return raw


def validate_syringe_capacity(
    raw: dict[str, Any],
    *,
    repetitions: int = 2,
) -> CapacityRequirement:
    """Fail closed unless the complete repeated cycle fits the syringe.

    Two repetitions are checked by default so a non-zero end-of-cycle balance
    cannot be hidden by validating just one cycle.
    """
    pump = _mapping(raw.get("pump"), "pump")
    if "syringe_capacity_ml" not in pump:
        raise ValueError(
            "pump.syringe_capacity_ml is required; capacity cannot be assumed safe"
        )
    capacity = _positive(pump.get("syringe_capacity_ml"), "pump.syringe_capacity_ml")
    initial = _nonnegative(
        pump.get("initial_retained_volume_ml", 0.0),
        "pump.initial_retained_volume_ml",
    )
    margin = _nonnegative(
        pump.get("syringe_safety_margin_ml", 0.0),
        "pump.syringe_safety_margin_ml",
    )
    if repetitions < 1:
        raise ValueError("capacity validation repetitions must be at least 1")
    if initial + margin > capacity:
        raise ValueError(
            "Initial retained volume plus syringe safety margin exceeds capacity"
        )

    retained = initial
    maximum = retained
    cycle = _mapping(raw.get("workflow"), "workflow").get("cycle", [])
    for repetition in range(1, repetitions + 1):
        for index, event_value in enumerate(cycle, start=1):
            event = _mapping(event_value, f"workflow.cycle[{index}]")
            action = str(event.get("action", "")).lower()
            if action not in {"withdraw", "infuse"}:
                continue
            volume = _positive(
                event.get("volume_ml"),
                f"workflow.cycle[{index}].volume_ml",
            )
            retained += volume if action == "withdraw" else -volume
            if retained < -1e-9:
                raise ValueError(
                    f"Cycle repetition {repetition} infuses more volume than the "
                    "syringe can contain"
                )
            maximum = max(maximum, retained)

    one_cycle_net = (retained - initial) / repetitions
    if not math.isclose(one_cycle_net, 0.0, abs_tol=1e-9):
        raise ValueError(
            "The repeated Si6 cycle must return to its initial retained volume; "
            f"net change is {one_cycle_net:g} mL per cycle"
        )
    required_with_margin = maximum + margin
    if required_with_margin > capacity + 1e-9:
        raise ValueError(
            "Unsafe syringe capacity: repeated cycle requires maximum retained "
            f"volume {maximum:g} mL plus {margin:g} mL safety margin "
            f"({required_with_margin:g} mL total), but configured capacity is "
            f"{capacity:g} mL"
        )
    return CapacityRequirement(
        syringe_capacity_ml=capacity,
        initial_retained_volume_ml=initial,
        safety_margin_ml=margin,
        maximum_retained_volume_ml=maximum,
        end_retained_volume_ml=retained,
    )


def build_stages(workflow: dict[str, Any]) -> list[Stage]:
    stages = [_parse_stage(workflow.get("initial_stage"), "initial_stage")]
    first = workflow.get("first_addition_stage")
    if first:
        stages.append(_parse_stage(first, "first_addition_stage"))
    rounds = int(workflow.get("repeat_addition_rounds", 0))
    if rounds < 0:
        raise ValueError("workflow.repeat_addition_rounds cannot be negative")
    repeating = workflow.get("repeating_stages", [])
    if not isinstance(repeating, list):
        raise ValueError("workflow.repeating_stages must be a list")
    for round_number in range(1, rounds + 1):
        for index, value in enumerate(repeating, start=1):
            stage = _parse_stage(value, f"repeating_stages[{index}]")
            stages.append(
                Stage(
                    f"round_{round_number}_{stage.name}",
                    stage.operator_prompt,
                    stage.interval_minutes,
                    stage.max_hours,
                    stage.measure_immediately,
                    stage.plateau_stopping_enabled,
                    stage.max_measurements,
                )
            )
    names = [stage.name for stage in stages]
    if len(names) != len(set(names)):
        raise ValueError("Every active workflow stage must have a unique name")
    return stages


def _parse_stage(value: Any, label: str) -> Stage:
    section = _mapping(value, f"workflow.{label}")
    _reject_unknown(
        section,
        {
            "name", "operator_prompt", "interval_minutes", "max_hours",
            "measure_immediately", "plateau_stopping_enabled",
            "max_measurements",
        },
        f"workflow.{label}",
    )
    name = str(section.get("name", "")).strip()
    prompt = str(section.get("operator_prompt", "")).strip()
    if not name or not prompt:
        raise ValueError(f"workflow.{label} needs name and operator_prompt")
    interval_minutes = _positive(
        section.get("interval_minutes"), f"{label}.interval_minutes"
    )
    max_hours = _positive(section.get("max_hours"), f"{label}.max_hours")
    measure_immediately = _required_bool(
        section.get("measure_immediately"), f"{label}.measure_immediately"
    )
    plateau_enabled = _required_bool(
        section.get("plateau_stopping_enabled"),
        f"{label}.plateau_stopping_enabled",
    )
    max_measurements = _positive_integer(
        section.get("max_measurements"), f"{label}.max_measurements"
    )
    last_scheduled_minutes = interval_minutes * (
        max_measurements - 1 if measure_immediately else max_measurements
    )
    if max_hours * 60 <= last_scheduled_minutes:
        raise ValueError(
            f"{label}.max_hours must extend beyond the last scheduled "
            f"measurement at {last_scheduled_minutes:g} minutes"
        )
    return Stage(
        name=name,
        operator_prompt=prompt,
        interval_minutes=interval_minutes,
        max_hours=max_hours,
        measure_immediately=measure_immediately,
        plateau_stopping_enabled=plateau_enabled,
        max_measurements=max_measurements,
    )


def _reject_unknown(
    mapping: dict[str, Any], allowed: set[str], label: str
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"Unknown {label} field(s): {', '.join(unknown)}")


def _required_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be Boolean")
    return value


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return dict(value)


def _positive(value: Any, label: str) -> float:
    number = _nonnegative(value, label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def _nonnegative(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if number < 0:
        raise ValueError(f"{label} cannot be negative")
    return number


def create_run_paths(root: Path, now: datetime | None = None) -> RunPaths:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    run_dir = Path(root) / f"{stamp}_si6"
    suffix = 1
    while run_dir.exists():
        run_dir = Path(root) / f"{stamp}_si6_{suffix:02d}"
        suffix += 1
    raw_dir = run_dir / "raw_nmr"
    plots_dir = run_dir / "plots"
    raw_dir.mkdir(parents=True)
    plots_dir.mkdir(parents=True)
    return RunPaths(
        run_dir, raw_dir, plots_dir, run_dir / "time_series.csv",
        run_dir / "spectra_long.csv", run_dir / "operations.csv",
        run_dir / "manifest.json", run_dir / "operation_journal.jsonl",
        run_dir / "run_state.json",
    )


def build_instrument_settings(raw: dict[str, Any], machine_path: Path) -> tuple[config.PumpConfig, config.NmrSettings]:
    machine = config.load_machine_config(machine_path)
    pump = raw["pump"]
    nmr = raw["nmr"]
    pump_values = dict(
        port=machine.chemyx.serial_port,
        baud_rate=machine.chemyx.baud_rate,
        timeout=machine.chemyx.timeout_seconds,
        response_delay=machine.chemyx.response_delay_seconds,
        channel=pump["channel"], diameter=pump["syringe_diameter_mm"],
        units=pump["units"], rate=pump["rate_ml_min"],
        volume=pump["default_volume_ml"],
    )
    pump_values.update(config._explicit_env_pump_overrides())
    pump_cfg = config.load_pump_config(load_local=False, **pump_values)
    nmr_values = dict(
        host=machine.nmr.host, port=machine.nmr.port, scheme=machine.nmr.scheme,
        timeout=machine.nmr.timeout_seconds, poll_seconds=machine.nmr.poll_seconds,
        max_wait_seconds=machine.nmr.max_wait_seconds,
        route=nmr["route"], scans=nmr["scans"], receiver_gain=nmr["receiver_gain"],
        auto_gain=nmr["auto_gain"], spectral_center=nmr["spectral_center"],
        sweep_width=nmr["sweep_width"], result_type=nmr["result_type"],
        target_ppm=nmr["target_ppm"],
    )
    nmr_values.update(config._explicit_env_nmr_overrides())
    nmr_cfg = config.load_nmr_settings(load_local=False, **nmr_values)
    return pump_cfg, nmr_cfg


def growth_percent(
    previous: float | None,
    current: float,
    *,
    epsilon: float = 1e-12,
) -> float | None:
    if (
        previous is None
        or not math.isfinite(previous)
        or not math.isfinite(current)
        or previous <= float(epsilon)
        or current < 0
    ):
        return None
    return (current - previous) / abs(previous) * 100.0


def _valid_plateau_measurement(row: dict, analysis: dict[str, Any]) -> bool:
    if row.get("error") or not row.get("peak_clear"):
        return False
    required = (
        "target_ppm", "peak_ppm", "peak_area", "snr", "prominence_snr"
    )
    try:
        values = {key: float(row[key]) for key in required}
    except (KeyError, TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in values.values()):
        return False
    if abs(values["peak_ppm"] - values["target_ppm"]) > float(
        analysis["detection_window_ppm"]
    ):
        return False
    if values["snr"] < float(analysis["min_peak_snr"]):
        return False
    if values["prominence_snr"] < float(analysis["min_prominence_snr"]):
        return False
    minimum_area = max(
        float(analysis.get("min_peak_area", 0.0)),
        float(analysis.get("area_epsilon", 1e-12)),
    )
    return values["peak_area"] >= minimum_area


def plateau_reached(rows: list[dict], analysis: dict[str, Any]) -> bool:
    intervals = int(analysis["plateau_consecutive_intervals"])
    if len(rows) < intervals + 1:
        return False
    window = rows[-(intervals + 1):]
    if not all(_valid_plateau_measurement(row, analysis) for row in window):
        return False
    lower = -float(analysis["plateau_max_decline_percent"])
    upper = float(analysis["plateau_max_growth_percent"])
    for row in window[1:]:
        try:
            change = float(row["growth_percent"])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(change) or not lower <= change <= upper:
            return False
    return True


def analyze_timepoint(dx_path: Path, paths: RunPaths, analysis: dict[str, Any], metadata: dict[str, Any]) -> tuple[dict, list[dict]]:
    result = analyze_dx_peak(
        dx_path, target_ppm=float(metadata["target_ppm"]),
        window_ppm=float(analysis["detection_window_ppm"]),
        line_broadening_hz=float(analysis.get("line_broadening_hz", 0.3)),
        min_prominence_snr=float(analysis["min_prominence_snr"]),
        integration_window_ppm=float(analysis["integration_window_ppm"]),
    )
    plot = plot_peak_region(
        dx_path, result, paths.plots_dir, target_ppm=float(metadata["target_ppm"]),
        detection_window_ppm=float(analysis["detection_window_ppm"]),
        plot_window_ppm=float(analysis["plot_window_ppm"]),
        line_broadening_hz=float(analysis.get("line_broadening_hz", 0.3)),
    )
    clear = (
        result.snr >= float(analysis["min_peak_snr"])
        and result.prominence_snr >= float(analysis["min_prominence_snr"])
        and result.peak_area >= float(analysis.get("min_peak_area", 0.0))
    )
    row = dict(metadata)
    row.update({
        "file": dx_path.name, "peak_ppm": result.peak_ppm,
        "peak_height": result.peak_height, "peak_area": result.peak_area,
        "snr": result.snr, "prominence": result.prominence,
        "prominence_snr": result.prominence_snr, "width_ppm": result.width_ppm,
        "baseline": result.baseline, "noise": result.noise, "peak_clear": clear,
        "plot_file": str(plot.relative_to(paths.run_dir)), "error": "",
    })
    spectrum = build_magnitude_spectrum(dx_path, line_broadening_hz=float(analysis.get("line_broadening_hz", 0.3)))
    spectrum_rows = [
        {"iteration": metadata["iteration"], "stage": metadata["stage"],
         "elapsed_hours": metadata["elapsed_hours"], "ppm": float(ppm),
         "magnitude": float(magnitude)}
        for ppm, magnitude in zip(spectrum.ppm_axis, spectrum.magnitude)
    ]
    return row, spectrum_rows


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def append_spectra(path: Path, rows: list[dict]) -> None:
    columns = ["iteration", "stage", "elapsed_hours", "ppm", "magnitude"]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def update_summary_plots(rows: list[dict], paths: RunPaths, spectra_path: Path) -> None:
    successful = [row for row in rows if not row.get("error")]
    if not successful:
        return
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    x = [float(row["elapsed_hours"]) for row in successful]
    plots = [
        ("peak_area_vs_time.png", "Peak area", "Peak Area vs Time", [float(row["peak_area"]) for row in successful]),
        ("snr_vs_time.png", "Signal-to-noise ratio", "Signal-to-Noise Ratio vs Time", [float(row["snr"]) for row in successful]),
        ("growth_percent_vs_time.png", "Peak-area growth (%)", "Peak-Area Growth vs Time", [float(row["growth_percent"]) if row["growth_percent"] not in (None, "") else float("nan") for row in successful]),
    ]
    for filename, ylabel, descriptive_title, y in plots:
        fig, ax = plt.subplots(figsize=(8.5, 5), dpi=140)
        ax.plot(x, y, marker="o", linewidth=1.4)
        ax.set_title(
            dataset_plot_title(
                descriptive_title,
                output_path=paths.run_dir,
            )
        )
        ax.set_xlabel("Elapsed time (hours)")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        fig.savefig(paths.plots_dir / filename)
        plt.close(fig)

    # Overlay the target region from the point-wise raw spectrum CSV.
    grouped: dict[str, tuple[list[float], list[float]]] = {}
    with spectra_path.open(newline="", encoding="utf-8") as handle:
        for item in csv.DictReader(handle):
            ppm = float(item["ppm"])
            target = float(successful[-1]["target_ppm"])
            if abs(ppm - target) <= 0.5:
                xs, ys = grouped.setdefault(item["iteration"], ([], []))
                xs.append(ppm)
                ys.append(float(item["magnitude"]))
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=140)
    for iteration, (xs, ys) in grouped.items():
        scale = max(ys) or 1.0
        ax.plot(xs, [value / scale for value in ys], alpha=0.7, label=f"iteration {iteration}")
    ax.set_xlabel("ppm")
    ax.set_ylabel("Normalized magnitude")
    ax.set_title(
        dataset_plot_title(
            "Target Region Overlay",
            output_path=paths.run_dir,
        )
    )
    ax.invert_xaxis()
    ax.grid(True, alpha=0.2)
    if len(grouped) <= 12:
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(paths.plots_dir / "target_region_overlay.png")
    plt.close(fig)


def attempt_emergency_stop(
    pump: Pump,
    state: PumpSafetyState,
    recorder: EventRecorder | None = None,
    *,
    workflow_phase: str | None = None,
    cycle_number: int | None = None,
    parent_operation_id: str | None = None,
) -> StopStatus:
    """Best-effort stop that records its result and never raises.

    Emergency stop remains safety-prioritized: a journal failure is recorded in
    memory but never prevents the stop command from being attempted.
    """
    state.stop_attempts += 1
    operation_id = str(uuid.uuid4())
    journal_stage = 0
    fields = {
        "operation_id": operation_id,
        "parent_operation_id": parent_operation_id,
        "operation_type": "pump_stop",
        "physical_state_effect": True,
        "workflow_phase": workflow_phase,
        "cycle_number": cycle_number,
        "requested_parameters": {"command": "stop"},
    }
    if recorder is not None:
        try:
            recorder.record(
                "operation_lifecycle", lifecycle_state="planned", **fields
            )
            journal_stage = 1
            recorder.record(
                "operation_lifecycle",
                lifecycle_state="dispatch_started",
                **fields,
            )
            journal_stage = 2
        except BaseException as exc:
            state.record_persistence_error(exc)
    try:
        response = pump.stop()
    except BaseException as exc:
        state.last_stop_status = StopStatus.FAILED
        state.last_stop_error = f"{type(exc).__name__}: {exc}"
        if recorder is not None and journal_stage == 2:
            try:
                recorder.record(
                    "operation_lifecycle",
                    lifecycle_state="uncertain",
                    result_classification=StopStatus.FAILED.value,
                    physical_state_certainty="uncertain",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    **fields,
                )
            except BaseException as journal_exc:
                state.record_persistence_error(journal_exc)
        return state.last_stop_status
    if response in (None, "", b""):
        state.last_stop_status = StopStatus.UNCONFIRMED
        state.last_stop_error = "Pump returned no stop confirmation"
    else:
        state.last_stop_status = StopStatus.SUCCEEDED
        state.last_stop_error = None
        state.motion_active = False
    if recorder is not None and journal_stage == 2:
        lifecycle = (
            "completed"
            if state.last_stop_status is StopStatus.SUCCEEDED
            else "uncertain"
        )
        try:
            recorder.record(
                "operation_lifecycle",
                lifecycle_state=lifecycle,
                result_classification=state.last_stop_status.value,
                physical_state_certainty=(
                    "certain" if lifecycle == "completed" else "uncertain"
                ),
                error_message=state.last_stop_error,
                **fields,
            )
        except BaseException as exc:
            state.record_persistence_error(exc)
    return state.last_stop_status


def execute_with_emergency_stop(
    pump: Pump,
    state: PumpSafetyState,
    action: Callable[[], RunOutcome],
    recorder: EventRecorder | None = None,
) -> RunOutcome:
    """Run an action and attempt a stop for every BaseException and exit."""
    try:
        outcome = action()
    except BaseException:
        if state.motion_active:
            state.mark_uncertain(
                "Execution exited while pump motion may still have been active"
            )
        attempt_emergency_stop(pump, state, recorder)
        raise

    stop_status = attempt_emergency_stop(pump, state, recorder)
    if stop_status is not StopStatus.SUCCEEDED:
        state.mark_uncertain(
            "Final emergency stop could not be positively confirmed"
        )
        raise PumpStateUncertainError(state.uncertain_reason)
    if state.persistence_errors:
        raise JournalError(
            "Emergency cleanup completed, but required persistence failed: "
            + "; ".join(state.persistence_errors)
        )
    return outcome


def run_safe_metered_move(
    pump: Pump,
    pump_cfg: config.PumpConfig,
    direction: str,
    volume_ml: float,
    state: PumpSafetyState,
    *,
    extra_seconds: float = 2.0,
    sleep_fn: Callable[[str, float], None] = sleep_with_progress,
    recorder: EventRecorder | None = None,
    workflow_phase: str | None = None,
    cycle_number: int | None = None,
) -> None:
    """Run one transfer; any interruption after dispatch becomes uncertain."""
    operation = f"{direction} {abs(float(volume_ml)):g} mL"
    state.current_operation = operation
    wait_seconds = move_seconds(volume_ml, pump_cfg.rate, pump_cfg.units) + max(
        0.0, extra_seconds
    )
    signed_volume = abs(volume_ml) if direction == "infuse" else -abs(volume_ml)
    retained_before = state.retained_volume_ml
    retained_after = retained_before + (
        abs(volume_ml) if direction == "withdraw" else -abs(volume_ml)
    )
    operation_id = str(uuid.uuid4())
    journal_fields = {
        "operation_id": operation_id,
        "operation_type": direction,
        "physical_state_effect": True,
        "workflow_phase": workflow_phase,
        "cycle_number": cycle_number,
        "requested_parameters": {
            "volume": abs(float(volume_ml)),
            "rate": float(pump_cfg.rate),
        },
        "units": {
            "volume": "mL",
            "rate": config.UNITS[pump_cfg.units],
        },
        "expected_retained_volume_before_ml": retained_before,
        "expected_retained_volume_after_ml": retained_after,
    }
    if recorder is not None:
        recorder.record(
            "operation_lifecycle", lifecycle_state="planned", **journal_fields
        )
        recorder.record(
            "operation_lifecycle",
            lifecycle_state="dispatch_started",
            **journal_fields,
        )
    print(
        f"     {direction} {abs(volume_ml):.4g} mL at "
        f"{pump_cfg.rate:.4g} {config.UNITS[pump_cfg.units]}"
    )
    start_attempted = False
    completion_durable = False
    try:
        print("     volume ->", repr(pump.set_volume(signed_volume)))
        state.motion_active = True
        start_attempted = True
        print("     start  ->", repr(pump.start(delay=0)))
        sleep_fn(direction, wait_seconds)
        stop_status = attempt_emergency_stop(
            pump,
            state,
            recorder,
            workflow_phase=workflow_phase,
            cycle_number=cycle_number,
            parent_operation_id=operation_id,
        )
        print("     stop   ->", stop_status.value)
        if stop_status is not StopStatus.SUCCEEDED:
            state.mark_uncertain(
                f"Stop after {operation} could not be positively confirmed"
            )
            raise PumpStateUncertainError(state.uncertain_reason)
        if state.persistence_errors:
            state.mark_uncertain(
                f"Persistence failed after {operation}; completion evidence "
                "cannot be trusted"
            )
            raise JournalError(state.uncertain_reason)
        if recorder is not None:
            try:
                recorder.record(
                    "operation_lifecycle",
                    lifecycle_state="completed",
                    result_classification="positively_confirmed",
                    physical_state_certainty="certain",
                    **journal_fields,
                )
            except BaseException as exc:
                if isinstance(exc, SnapshotWriteError):
                    completion_durable = True
                    state.record_persistence_error(exc)
                    raise
                state.mark_uncertain(
                    f"{operation} physically completed, but its durable "
                    f"completion record failed: {type(exc).__name__}: {exc}"
                )
                state.record_persistence_error(exc)
                raise
        completion_durable = True
    except BaseException as exc:
        if completion_durable:
            raise
        if not start_attempted:
            if recorder is not None:
                try:
                    recorder.record(
                        "operation_lifecycle",
                        lifecycle_state="failed",
                        result_classification="motion_not_started",
                        physical_state_certainty="certain",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        **journal_fields,
                    )
                except BaseException as journal_exc:
                    state.record_persistence_error(journal_exc)
                    state.mark_uncertain(
                        f"{operation} did not start, but that safe failure could "
                        "not be made durable; recovery must treat the dispatched "
                        "operation as uncertain"
                    )
            raise
        if not state.uncertain:
            state.mark_uncertain(
                f"{operation} did not reach positively confirmed completion: "
                f"{type(exc).__name__}: {exc}"
            )
        if recorder is not None:
            try:
                recorder.record(
                    "operation_lifecycle",
                    lifecycle_state="uncertain",
                    result_classification="completion_not_durable_or_confirmed",
                    physical_state_certainty="uncertain",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    **journal_fields,
                )
            except BaseException as journal_exc:
                state.record_persistence_error(journal_exc)
        attempt_emergency_stop(
            pump,
            state,
            recorder,
            workflow_phase=workflow_phase,
            cycle_number=cycle_number,
            parent_operation_id=operation_id,
        )
        raise
    else:
        state.motion_active = False
        state.current_operation = None


def operator_checkpoint(
    prompt: str,
    recorder: EventRecorder | None = None,
    *,
    workflow_phase: str | None = None,
    cycle_number: int | None = None,
) -> None:
    operation_id = str(uuid.uuid4())
    fields = {
        "operation_id": operation_id,
        "workflow_phase": workflow_phase,
        "cycle_number": cycle_number,
        "checkpoint": prompt,
    }
    if recorder is not None:
        recorder.record(
            "operator_checkpoint",
            result_classification="requested",
            **fields,
        )
    try:
        if not sys.stdin.isatty():
            raise OperatorAbortError(
                f"Operator checkpoint requires an interactive terminal: {prompt}"
            )
        if (
            input(
                f"\nOPERATOR ACTION: {prompt}\nType yes when complete: "
            ).strip().lower()
            != "yes"
        ):
            raise OperatorAbortError(
                "Operator did not confirm the required action"
            )
    except BaseException as exc:
        if recorder is not None:
            try:
                recorder.record(
                    "operator_checkpoint",
                    result_classification="aborted",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    **fields,
                )
            except BaseException as journal_exc:
                if hasattr(exc, "add_note"):
                    exc.add_note(
                        "Operator-abort journal write also failed: "
                        f"{type(journal_exc).__name__}: {journal_exc}"
                    )
        raise
    if recorder is not None:
        recorder.record(
            "operator_checkpoint",
            result_classification="confirmed",
            **fields,
        )


def run_cycle(
    pump: Pump,
    pump_cfg: config.PumpConfig,
    nmr_cfg: config.NmrSettings,
    raw: dict[str, Any],
    paths: RunPaths,
    label: str,
    state: PumpSafetyState,
    *,
    sleep_fn: Callable[[str, float], None] = sleep_with_progress,
    operator_fn: Callable[[str], None] = operator_checkpoint,
    nmr_runner: Callable[..., Path] = run_nmr_acquisition,
    recorder: EventRecorder | None = None,
    cycle_number: int | None = None,
) -> CycleAcquisitionResult:
    dx_path: Path | None = None
    acquisition_started_at: datetime | None = None
    acquisition_completed_at: datetime | None = None
    for event in raw["workflow"]["cycle"]:
        if state.uncertain:
            raise PumpStateUncertainError(
                "Physical pump state is uncertain; no further automatic action is allowed"
            )
        action = event["action"].lower()
        if action in {"withdraw", "infuse"}:
            volume = float(event["volume_ml"])
            run_safe_metered_move(
                pump,
                pump_cfg,
                action,
                volume,
                state,
                extra_seconds=float(raw["workflow"].get("pump_extra_seconds", 2.0)),
                sleep_fn=sleep_fn,
                recorder=recorder,
                workflow_phase=label,
                cycle_number=cycle_number,
            )
            state.retained_volume_ml += volume if action == "withdraw" else -volume
        elif action == "pause":
            sleep_fn(event.get("note", "pause"), float(event["seconds"]))
        elif action == "operator":
            if operator_fn is operator_checkpoint:
                operator_checkpoint(
                    event["prompt"],
                    recorder,
                    workflow_phase=label,
                    cycle_number=cycle_number,
                )
            else:
                operator_fn(event["prompt"])
        elif action == "nmr":
            operation_id = str(uuid.uuid4())
            fields = {
                "operation_id": operation_id,
                "operation_type": "nmr_acquisition",
                "physical_state_effect": False,
                "workflow_phase": label,
                "cycle_number": cycle_number,
                "requested_parameters": {
                    "route": nmr_cfg.route,
                    "scans": nmr_cfg.scans,
                    "result_type": nmr_cfg.result_type,
                },
            }
            if recorder is not None:
                recorder.record(
                    "operation_lifecycle",
                    lifecycle_state="planned",
                    **fields,
                )
                recorder.record(
                    "operation_lifecycle",
                    lifecycle_state="dispatch_started",
                    **fields,
                )
            try:
                acquisition_started_at = datetime.now()
                dx_path = nmr_runner(nmr_cfg, paths.raw_dir, label=label)
                acquisition_completed_at = datetime.now()
            except BaseException as exc:
                if recorder is not None:
                    try:
                        recorder.record(
                            "operation_lifecycle",
                            lifecycle_state="failed",
                            result_classification="acquisition_failed",
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                            **fields,
                        )
                    except BaseException as journal_exc:
                        if hasattr(exc, "add_note"):
                            exc.add_note(
                                "NMR failure journal write also failed: "
                                f"{type(journal_exc).__name__}: {journal_exc}"
                            )
                raise
            if recorder is not None:
                recorder.record(
                    "operation_lifecycle",
                    lifecycle_state="completed",
                    result_classification="result_saved",
                    result_path=str(dx_path.relative_to(paths.run_dir)),
                    **fields,
                )
    if dx_path is None:
        raise RuntimeError("Si6 cycle did not contain an NMR action")
    if acquisition_started_at is None or acquisition_completed_at is None:
        raise RuntimeError("Si6 cycle did not record NMR acquisition timing")
    return CycleAcquisitionResult(
        dx_path, acquisition_started_at, acquisition_completed_at
    )


def print_plan(raw: dict[str, Any], paths_root: Path) -> None:
    capacity = validate_syringe_capacity(raw)
    print("Si6 automated Chemyx/NMR plan")
    print(f"Run root: {paths_root} (a new timestamped directory is created per run)")
    print("Cycle:")
    for event in raw["workflow"]["cycle"]:
        detail = event.get("volume_ml", event.get("seconds", event.get("prompt", "")))
        print(f"  - {event['action']}: {detail}")
    print("Stages:")
    for stage in build_stages(raw["workflow"]):
        mode = (
            "plateau-or-limit"
            if stage.plateau_stopping_enabled
            else "fixed scheduled count"
        )
        first = "immediately" if stage.measure_immediately else "after one interval"
        print(
            f"  - {stage.name}: {stage.max_measurements} measurements every "
            f"{stage.interval_minutes:g} min, first {first}, {mode}, hard "
            f"ceiling {stage.max_hours:g} h"
        )
    a = raw["analysis"]
    print(
        "Syringe safety: maximum retained "
        f"{capacity.maximum_retained_volume_ml:g} mL + "
        f"{capacity.safety_margin_ml:g} mL margin <= "
        f"{capacity.syringe_capacity_ml:g} mL capacity"
    )
    print(
        "Stop: every plateau spectrum must pass peak QC and each area change "
        f"must be between -{a['plateau_max_decline_percent']:g}% and "
        f"+{a['plateau_max_growth_percent']:g}% for "
        f"{a['plateau_consecutive_intervals']} consecutive intervals"
    )


def scheduled_measurement_offset_seconds(
    stage: Stage, measurement_number: int
) -> float:
    if measurement_number < 1 or measurement_number > stage.max_measurements:
        raise ValueError("measurement number is outside the configured stage")
    interval = stage.interval_minutes * 60.0
    return interval * (
        measurement_number - 1
        if stage.measure_immediately
        else measurement_number
    )


def run_monitoring_stage(
    stage: Stage,
    measurement_fn: Callable[[MeasurementSchedule], MeasurementObservation],
    *,
    recorder: EventRecorder | None = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
    wall_now_fn: Callable[[], datetime] = datetime.now,
    sleep_fn: Callable[[str, float], None] = sleep_with_progress,
) -> MonitoringResult:
    """Run one stage on fixed start-to-start monotonic deadlines."""
    stage_started_monotonic = monotonic_fn()
    stage_started_wall = wall_now_fn()
    hard_deadline = stage_started_monotonic + stage.max_hours * 3600.0
    attempts = 0
    valid_count = 0
    mode = (
        "plateau_or_limit"
        if stage.plateau_stopping_enabled
        else "fixed_scheduled_count"
    )
    monitoring_fields = {
        "workflow_phase": stage.name,
        "monitoring_mode": mode,
        "plateau_stopping_enabled": stage.plateau_stopping_enabled,
        "measure_immediately": stage.measure_immediately,
        "max_measurements": stage.max_measurements,
        "hard_runtime_ceiling_hours": stage.max_hours,
        "interval_minutes": stage.interval_minutes,
        "stage_started_at": stage_started_wall.isoformat(timespec="seconds"),
    }
    if recorder is not None:
        recorder.record(
            "monitoring_stage_started",
            result_classification="started",
            **monitoring_fields,
        )

    for measurement_number in range(1, stage.max_measurements + 1):
        offset = scheduled_measurement_offset_seconds(stage, measurement_number)
        scheduled_monotonic = stage_started_monotonic + offset
        scheduled_wall = stage_started_wall + timedelta(seconds=offset)
        if recorder is not None:
            recorder.record(
                "measurement_scheduled",
                scheduled_measurement_number=measurement_number,
                scheduled_measurement_time=scheduled_wall.isoformat(
                    timespec="seconds"
                ),
                **monitoring_fields,
            )
        remaining = scheduled_monotonic - monotonic_fn()
        if remaining > 0:
            sleep_fn(
                f"until scheduled measurement {measurement_number}", remaining
            )
        actual_start_monotonic = monotonic_fn()
        actual_start_wall = wall_now_fn()
        if actual_start_monotonic >= hard_deadline:
            outcome = maximum_duration_outcome(stage)
            if recorder is not None:
                recorder.record(
                    "stage_transition_decision",
                    result_classification=StageOutcome.RUNTIME_LIMIT_REACHED.value,
                    scheduled_measurement_number=measurement_number,
                    **monitoring_fields,
                )
            return MonitoringResult(outcome, measurement_number - 1, attempts, valid_count)

        attempts += 1
        schedule = MeasurementSchedule(
            stage_name=stage.name,
            stage_started_at=stage_started_wall.isoformat(timespec="seconds"),
            scheduled_measurement_number=measurement_number,
            acquisition_attempt_number=attempts,
            valid_analysis_count_before=valid_count,
            scheduled_time=scheduled_wall.isoformat(timespec="seconds"),
            scheduled_monotonic=scheduled_monotonic,
            actual_cycle_start=actual_start_wall.isoformat(timespec="seconds"),
            actual_cycle_start_monotonic=actual_start_monotonic,
            scheduling_delay_seconds=max(
                0.0, actual_start_monotonic - scheduled_monotonic
            ),
        )
        if recorder is not None:
            recorder.record(
                "measurement_started",
                scheduled_measurement_number=measurement_number,
                acquisition_attempt_number=attempts,
                valid_analysis_count=valid_count,
                scheduled_measurement_time=schedule.scheduled_time,
                actual_cycle_start=schedule.actual_cycle_start,
                scheduling_delay_seconds=schedule.scheduling_delay_seconds,
                **monitoring_fields,
            )
        observation = measurement_fn(schedule)
        if observation.valid_analysis:
            valid_count += 1
        analysis_finished_monotonic = monotonic_fn()
        if recorder is not None:
            recorder.record(
                "measurement_completed",
                scheduled_measurement_number=measurement_number,
                acquisition_attempt_number=attempts,
                valid_analysis_count=valid_count,
                scheduled_measurement_time=schedule.scheduled_time,
                actual_cycle_start=schedule.actual_cycle_start,
                scheduling_delay_seconds=schedule.scheduling_delay_seconds,
                nmr_acquisition_started_at=observation.nmr_acquisition_started_at,
                nmr_acquisition_completed_at=observation.nmr_acquisition_completed_at,
                analysis_completed_at=observation.analysis_completed_at,
                plateau_detected=observation.plateau_detected,
                result_classification=(
                    "valid_analysis"
                    if observation.valid_analysis
                    else "invalid_analysis"
                ),
                **monitoring_fields,
            )

        if analysis_finished_monotonic >= hard_deadline:
            outcome = maximum_duration_outcome(stage)
            if recorder is not None:
                recorder.record(
                    "stage_transition_decision",
                    result_classification=StageOutcome.RUNTIME_LIMIT_REACHED.value,
                    scheduled_measurement_number=measurement_number,
                    **monitoring_fields,
                )
            return MonitoringResult(
                outcome, measurement_number, attempts, valid_count
            )

        if observation.plateau_detected:
            if recorder is not None:
                recorder.record(
                    "plateau_detection",
                    scheduled_measurement_number=measurement_number,
                    result_classification=(
                        "stage_completed"
                        if stage.plateau_stopping_enabled
                        else "detected_but_ignored"
                    ),
                    **monitoring_fields,
                )
            if stage.plateau_stopping_enabled:
                outcome = RunOutcome(
                    TerminalStatus.COMPLETED,
                    f"Stage {stage.name} reached plateau at scheduled "
                    f"measurement {measurement_number}.",
                    StageOutcome.PLATEAU_REACHED,
                )
                if recorder is not None:
                    recorder.record(
                        "stage_transition_decision",
                        result_classification=StageOutcome.PLATEAU_REACHED.value,
                        scheduled_measurement_number=measurement_number,
                        **monitoring_fields,
                    )
                return MonitoringResult(
                    outcome,
                    measurement_number,
                    attempts,
                    valid_count,
                    measurement_number,
                )

        if (
            not stage.plateau_stopping_enabled
            and measurement_number == stage.max_measurements
        ):
            outcome = RunOutcome(
                TerminalStatus.COMPLETED,
                f"Stage {stage.name} completed all {stage.max_measurements} "
                "scheduled measurements.",
                StageOutcome.SCHEDULED_MONITORING_COMPLETED,
            )
            if recorder is not None:
                recorder.record(
                    "stage_transition_decision",
                    result_classification=(
                        StageOutcome.SCHEDULED_MONITORING_COMPLETED.value
                    ),
                    scheduled_measurement_number=measurement_number,
                    **monitoring_fields,
                )
            return MonitoringResult(
                outcome, measurement_number, attempts, valid_count
            )

    outcome = RunOutcome(
        TerminalStatus.PLATEAU_NOT_REACHED_WITHIN_LIMIT,
        f"Stage {stage.name} completed {stage.max_measurements} scheduled "
        "measurements without satisfying the required plateau criterion. No "
        "next reagent-addition stage was started.",
        StageOutcome.PLATEAU_NOT_REACHED_WITHIN_LIMIT,
    )
    if recorder is not None:
        recorder.record(
            "stage_transition_decision",
            result_classification=(
                StageOutcome.PLATEAU_NOT_REACHED_WITHIN_LIMIT.value
            ),
            scheduled_measurement_number=stage.max_measurements,
            **monitoring_fields,
        )
    return MonitoringResult(
        outcome, stage.max_measurements, attempts, valid_count
    )


def run_stage_sequence(
    stages: list[Stage],
    operator_fn: Callable[[str], None],
    stage_runner: Callable[[Stage], RunOutcome],
    recorder: EventRecorder | None = None,
) -> RunOutcome:
    """Advance chemistry only after a stage is scientifically completed."""
    for stage in stages:
        if recorder is not None:
            recorder.record(
                "phase_transition",
                previous_state=None,
                new_state=stage.name,
                workflow_phase=stage.name,
            )
        if operator_fn is operator_checkpoint:
            operator_checkpoint(
                stage.operator_prompt,
                recorder,
                workflow_phase=stage.name,
            )
        else:
            operator_fn(stage.operator_prompt)
        outcome = stage_runner(stage)
        if outcome.status is not TerminalStatus.COMPLETED:
            return outcome
    return RunOutcome(
        TerminalStatus.COMPLETED,
        "All configured stages met the completion criterion.",
    )


def stage_within_duration(
    stage: Stage,
    started_monotonic: float,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> bool:
    return monotonic_fn() - started_monotonic < stage.max_hours * 3600


def maximum_duration_outcome(stage: Stage) -> RunOutcome:
    return RunOutcome(
        TerminalStatus.MAXIMUM_DURATION_REACHED,
        f"Stage {stage.name} reached {stage.max_hours:g} hours without "
        "satisfying the completion criterion. No next reagent-addition "
        "stage was started.",
        StageOutcome.RUNTIME_LIMIT_REACHED,
    )


def outcome_from_exception(
    exc: BaseException,
    state: PumpSafetyState,
) -> RunOutcome:
    retained = f" Estimated retained syringe volume: {state.retained_volume_ml:g} mL."
    if state.uncertain or isinstance(exc, PumpStateUncertainError):
        operation = state.uncertain_operation or state.current_operation or "unknown operation"
        reason = state.uncertain_reason or str(exc)
        return RunOutcome(
            TerminalStatus.SAFETY_STOP,
            "Physical pump state is uncertain during "
            f"{operation}: {reason}. No automatic recovery or chemistry will "
            f"continue. Inspect the pump, syringe, tubing, and sample manually.{retained}",
        )
    if isinstance(exc, (KeyboardInterrupt, SystemExit, OperatorAbortError)):
        return RunOutcome(
            TerminalStatus.OPERATOR_ABORTED,
            f"Operator aborted the run. Pump stop was attempted.{retained}",
        )
    if isinstance(exc, (PumpConnectionError, EchoMismatchError, NmrRpcError)):
        return RunOutcome(
            TerminalStatus.INSTRUMENT_FAILURE,
            f"Instrument failure: {exc}. No automatic recovery will run.{retained}",
        )
    if isinstance(exc, JournalError):
        return RunOutcome(
            TerminalStatus.UNEXPECTED_FAILURE,
            "Required journal or state persistence failed before a safe next "
            f"action: {exc}.{retained}",
        )
    if isinstance(exc, (NmrProcessingError, AnalysisInconclusiveError)):
        return RunOutcome(
            TerminalStatus.ANALYSIS_INCONCLUSIVE,
            f"NMR analysis was inconclusive: {exc}.{retained}",
        )
    return RunOutcome(
        TerminalStatus.UNEXPECTED_FAILURE,
        f"Unexpected software failure: {type(exc).__name__}: {exc}.{retained}",
    )


def build_run_summary(
    outcome: RunOutcome,
    *,
    started: datetime | None = None,
    finished: datetime | None = None,
    paths: RunPaths | None = None,
    iterations: int = 0,
    state: PumpSafetyState | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": outcome.status.value,
        "exit_code": outcome.exit_code,
        "message": outcome.message,
        "stage_outcome": (
            outcome.stage_outcome.value if outcome.stage_outcome else None
        ),
        "iterations": int(iterations),
    }
    if started is not None:
        summary["started_at"] = started.isoformat(timespec="seconds")
    if finished is not None:
        summary["finished_at"] = finished.isoformat(timespec="seconds")
    if paths is not None:
        summary.update(
            {
                "run_dir": str(paths.run_dir),
                "time_series_csv": str(paths.time_series_csv),
                "spectra_csv": str(paths.spectra_csv),
                "plots_dir": str(paths.plots_dir),
            }
        )
    if state is not None:
        summary["pump_safety"] = {
            "uncertain": state.uncertain,
            "uncertain_operation": state.uncertain_operation,
            "uncertain_reason": state.uncertain_reason,
            "retained_volume_ml": state.retained_volume_ml,
            "stop_attempts": state.stop_attempts,
            "last_stop_status": (
                state.last_stop_status.value if state.last_stop_status else None
            ),
            "last_stop_error": state.last_stop_error,
            "persistence_errors": list(state.persistence_errors or []),
        }
    return summary


def create_run_recorder(paths: RunPaths) -> RunRecorder:
    if paths.journal_jsonl is None or paths.state_json is None:
        raise ValueError("Run paths do not define journal and state files")
    journal = OperationJournal(
        paths.journal_jsonl,
        paths.run_dir.name,
        software_version=discover_git_commit(config.REPO_ROOT),
    )
    return RunRecorder(journal, paths.state_json)


def effective_configuration_hashes(raw: dict[str, Any]) -> tuple[str, str]:
    effective_payload = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    workflow_payload = json.dumps(
        {
            key: raw[key]
            for key in ("workflow", "pump", "nmr", "analysis")
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return (
        hashlib.sha256(effective_payload).hexdigest(),
        hashlib.sha256(workflow_payload).hexdigest(),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the automated Si6 pump/NMR kinetics workflow")
    parser.add_argument("--workflow-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--machine-config", type=Path, default=config.REPO_ROOT / "configs" / "machines" / "00_machine.local.yaml")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--inspect-run",
        type=Path,
        help="offline journal replay for an existing Si6 run directory",
    )
    parser.add_argument(
        "--rebuild-state",
        action="store_true",
        help="with --inspect-run, atomically rebuild run_state.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.inspect_run is not None:
        result = inspect_run(args.inspect_run, rebuild_state=args.rebuild_state)
        print(format_inspection(result))
        return result.exit_code
    if args.rebuild_state:
        print("FAILED: --rebuild-state requires --inspect-run")
        return EXIT_CODES[TerminalStatus.VALIDATION_FAILURE]
    try:
        raw = load_si6_config(args.workflow_config)
        pump_cfg, nmr_cfg = build_instrument_settings(raw, args.machine_config)
        root = Path(raw["output"]["run_root_dir"])
        effective_config_hash, workflow_identity_hash = (
            effective_configuration_hashes(raw)
        )
        print_plan(raw, root)
    except (ValueError, config.ConfigError) as exc:
        outcome = RunOutcome(TerminalStatus.VALIDATION_FAILURE, str(exc))
        print(f"STATUS: {outcome.status.value} (exit {outcome.exit_code})")
        print(f"FAILED: {outcome.message}")
        return outcome.exit_code
    if args.validate_only:
        print("Validation only. No output directory or hardware was opened.")
        return 0
    if args.dry_run:
        try:
            paths = create_run_paths(root)
            recorder = create_run_recorder(paths)
            write_json_atomic(paths.run_dir / "config_snapshot.json", raw)
            recorder.record(
                "phase_transition",
                previous_state=None,
                new_state="dry_run",
                workflow_phase="dry_run",
                result_classification="offline_no_hardware",
                effective_configuration_sha256=effective_config_hash,
                workflow_identity_sha256=workflow_identity_hash,
                expected_retained_volume_before_ml=raw["pump"].get(
                    "initial_retained_volume_ml", 0.0
                ),
            )
            recorder.record(
                "terminal",
                workflow_phase="dry_run",
                terminal_status=TerminalStatus.OPERATOR_ABORTED.value,
                result_classification="dry_run_stopped_before_hardware",
                physical_state_certainty="certain",
            )
        except BaseException as exc:
            outcome = outcome_from_exception(exc, PumpSafetyState())
            print(f"STATUS: {outcome.status.value} (exit {outcome.exit_code})")
            print(outcome.message)
            return outcome.exit_code
        print("Dry run only. No hardware was opened.")
        print(f"Journal-backed dry-run results: {paths.run_dir}")
        return 0
    if not sys.stdin.isatty() or input("Type RUN SI6 to create the run and connect to hardware: ").strip() != "RUN SI6":
        outcome = RunOutcome(
            TerminalStatus.OPERATOR_ABORTED,
            "Operator aborted before connecting to hardware.",
        )
        print(f"STATUS: {outcome.status.value} (exit {outcome.exit_code})")
        print(outcome.message)
        return outcome.exit_code

    try:
        paths = create_run_paths(root)
        recorder = create_run_recorder(paths)
        write_json_atomic(paths.run_dir / "config_snapshot.json", raw)
        recorder.record(
            "phase_transition",
            previous_state=None,
            new_state="initializing",
            workflow_phase="initializing",
            result_classification="real_run_authorized",
            effective_configuration_sha256=effective_config_hash,
            workflow_identity_sha256=workflow_identity_hash,
            expected_retained_volume_before_ml=raw["pump"].get(
                "initial_retained_volume_ml", 0.0
            ),
        )
    except BaseException as exc:
        outcome = outcome_from_exception(exc, PumpSafetyState())
        print(f"STATUS: {outcome.status.value} (exit {outcome.exit_code})")
        print(outcome.message)
        return outcome.exit_code
    rows: list[dict] = []
    operations: list[dict] = []
    started = datetime.now()
    capacity = validate_syringe_capacity(raw)
    safety_state = PumpSafetyState(
        retained_volume_ml=capacity.initial_retained_volume_ml
    )
    iteration = 0
    try:
        write_json_atomic(
            paths.manifest_json,
            {
                "started_at": started.isoformat(timespec="seconds"),
                "workflow_config": str(args.workflow_config),
                "machine_config": str(args.machine_config),
                "status": "running",
                "target_ppm": nmr_cfg.target_ppm,
                "authoritative_journal": "operation_journal.jsonl",
                "effective_configuration_sha256": effective_config_hash,
                "workflow_identity_sha256": workflow_identity_hash,
            },
        )
        with Pump(port=pump_cfg.port, baud_rate=pump_cfg.baud_rate, channel=pump_cfg.channel, units=pump_cfg.units, timeout=pump_cfg.timeout, response_delay=pump_cfg.response_delay) as pump:
            def run_automatic_workflow() -> RunOutcome:
                nonlocal iteration
                configure_pump(pump, pump_cfg)

                def run_stage(stage: Stage) -> RunOutcome:
                    nonlocal iteration
                    def perform_measurement(
                        schedule: MeasurementSchedule,
                    ) -> MeasurementObservation:
                        nonlocal iteration
                        iteration += 1
                        cycle_result = run_cycle(
                            pump,
                            pump_cfg,
                            nmr_cfg,
                            raw,
                            paths,
                            f"{stage.name}_{iteration:04d}",
                            safety_state,
                            recorder=recorder,
                            cycle_number=iteration,
                        )
                        dx_path = cycle_result.path
                        acquired_at = cycle_result.acquisition_completed_at
                        metadata = {
                            "iteration": iteration,
                            "stage": stage.name,
                            "stage_iteration": (
                                schedule.scheduled_measurement_number
                            ),
                            "scheduled_measurement_number": (
                                schedule.scheduled_measurement_number
                            ),
                            "acquisition_attempt_number": (
                                schedule.acquisition_attempt_number
                            ),
                            "valid_analysis_index": (
                                schedule.valid_analysis_count_before + 1
                            ),
                            "stage_started_at": schedule.stage_started_at,
                            "scheduled_measurement_time": schedule.scheduled_time,
                            "actual_cycle_start": schedule.actual_cycle_start,
                            "scheduling_delay_seconds": (
                                schedule.scheduling_delay_seconds
                            ),
                            "nmr_acquisition_started_at": (
                                cycle_result.acquisition_started_at.isoformat(
                                    timespec="seconds"
                                )
                            ),
                            "nmr_acquisition_completed_at": (
                                cycle_result.acquisition_completed_at.isoformat(
                                    timespec="seconds"
                                )
                            ),
                            "acquired_at": acquired_at.isoformat(timespec="seconds"),
                            "elapsed_hours": (
                                acquired_at - started
                            ).total_seconds() / 3600.0,
                            "target_ppm": nmr_cfg.target_ppm,
                        }
                        try:
                            recorder.record(
                                "spectrum_validation",
                                workflow_phase=stage.name,
                                cycle_number=iteration,
                                result_classification="started",
                                result_path=str(dx_path.relative_to(paths.run_dir)),
                            )
                            row, spectrum_rows = analyze_timepoint(
                                dx_path, paths, raw["analysis"], metadata
                            )
                            previous_area = next(
                                (
                                    float(old["peak_area"])
                                    for old in reversed(rows)
                                    if old.get("stage") == stage.name
                                    and not old.get("error")
                                ),
                                None,
                            )
                            row["growth_percent"] = growth_percent(
                                previous_area,
                                float(row["peak_area"]),
                                epsilon=float(raw["analysis"]["area_epsilon"]),
                            )
                            rows.append(row)
                            stage_rows = [
                                old for old in rows if old.get("stage") == stage.name
                            ]
                            row["plateau"] = plateau_reached(
                                stage_rows, raw["analysis"]
                            )
                            analysis_completed_at = datetime.now()
                            row["analysis_completed_at"] = (
                                analysis_completed_at.isoformat(timespec="seconds")
                            )
                            recorder.record(
                                "spectrum_validation",
                                workflow_phase=stage.name,
                                cycle_number=iteration,
                                result_classification=(
                                    "valid" if row["peak_clear"] else "invalid"
                                ),
                                result_path=str(dx_path.relative_to(paths.run_dir)),
                            )
                            recorder.record(
                                "analysis_result",
                                workflow_phase=stage.name,
                                cycle_number=iteration,
                                result_classification=(
                                    "valid" if row["peak_clear"] else "invalid"
                                ),
                                analysis_result={
                                    "target_ppm": row["target_ppm"],
                                    "peak_ppm": row["peak_ppm"],
                                    "peak_area": row["peak_area"],
                                    "snr": row["snr"],
                                    "prominence_snr": row["prominence_snr"],
                                    "growth_percent": row["growth_percent"],
                                },
                                plateau_progress={
                                    "required_intervals": raw["analysis"][
                                        "plateau_consecutive_intervals"
                                    ],
                                    "plateau_reached": row["plateau"],
                                },
                                result_path=str(dx_path.relative_to(paths.run_dir)),
                            )
                            append_spectra(paths.spectra_csv, spectrum_rows)
                        except NmrProcessingError as exc:
                            row = dict(
                                metadata,
                                file=dx_path.name,
                                error=str(exc),
                                peak_clear=False,
                                plateau=False,
                            )
                            rows.append(row)
                            write_csv(
                                paths.time_series_csv,
                                rows,
                                TIME_SERIES_COLUMNS,
                            )
                            recorder.record(
                                "spectrum_validation",
                                workflow_phase=stage.name,
                                cycle_number=iteration,
                                result_classification="invalid",
                                result_path=str(dx_path.relative_to(paths.run_dir)),
                                error_type=type(exc).__name__,
                                error_message=str(exc),
                            )
                            raise AnalysisInconclusiveError(
                                f"Could not process {dx_path.name}: {exc}"
                            ) from exc
                        write_csv(paths.time_series_csv, rows, TIME_SERIES_COLUMNS)
                        if paths.spectra_csv.exists():
                            update_summary_plots(rows, paths, paths.spectra_csv)
                        operations.append(
                            {
                                "time": datetime.now().isoformat(timespec="seconds"),
                                "stage": stage.name,
                                "iteration": iteration,
                                "event": "cycle_complete",
                                "file": dx_path.name,
                            }
                        )
                        write_csv(
                            paths.operations_csv,
                            operations,
                            ["time", "stage", "iteration", "event", "file"],
                        )
                        recorder.record(
                            "cycle_completed",
                            workflow_phase=stage.name,
                            cycle_number=iteration,
                            scheduled_measurement_number=(
                                schedule.scheduled_measurement_number
                            ),
                            acquisition_attempt_number=(
                                schedule.acquisition_attempt_number
                            ),
                            valid_analysis_count=(
                                schedule.valid_analysis_count_before + 1
                            ),
                            result_classification="completed",
                        )
                        return MeasurementObservation(
                            valid_analysis=True,
                            plateau_detected=bool(row.get("plateau")),
                            nmr_acquisition_started_at=(
                                cycle_result.acquisition_started_at.isoformat(
                                    timespec="seconds"
                                )
                            ),
                            nmr_acquisition_completed_at=(
                                cycle_result.acquisition_completed_at.isoformat(
                                    timespec="seconds"
                                )
                            ),
                            analysis_completed_at=(
                                analysis_completed_at.isoformat(
                                    timespec="seconds"
                                )
                            ),
                        )

                    result = run_monitoring_stage(
                        stage,
                        perform_measurement,
                        recorder=recorder,
                    )
                    return result.outcome

                return run_stage_sequence(
                    build_stages(raw["workflow"]),
                    operator_checkpoint,
                    run_stage,
                    recorder,
                )

            outcome = execute_with_emergency_stop(
                pump, safety_state, run_automatic_workflow, recorder
            )
    except BaseException as exc:
        outcome = outcome_from_exception(exc, safety_state)

    try:
        recorder.record(
            "terminal",
            workflow_phase="terminal",
            terminal_status=outcome.status.value,
            result_classification=outcome.status.value,
            stage_outcome=(
                outcome.stage_outcome.value if outcome.stage_outcome else None
            ),
            physical_state_certainty=(
                "uncertain" if safety_state.uncertain else "certain"
            ),
            error_message=(
                outcome.message
                if outcome.status is not TerminalStatus.COMPLETED
                else None
            ),
        )
    except SnapshotWriteError as exc:
        print(
            "WARNING: the terminal journal event is durable, but run_state.json "
            f"could not be refreshed: {exc}. Use --inspect-run --rebuild-state."
        )
    except BaseException as exc:
        original = outcome
        outcome = RunOutcome(
            (
                TerminalStatus.SAFETY_STOP
                if safety_state.uncertain
                else TerminalStatus.UNEXPECTED_FAILURE
            ),
            "The workflow stopped, but its authoritative terminal event could "
            f"not be made durable: {type(exc).__name__}: {exc}. Original "
            f"outcome was {original.status.value}: {original.message}",
        )
    print(f"STATUS: {outcome.status.value} (exit {outcome.exit_code})")
    print(outcome.message)
    manifest = build_run_summary(
        outcome,
        started=started,
        finished=datetime.now(),
        paths=paths,
        iterations=len(rows),
        state=safety_state,
    )
    try:
        write_json_atomic(paths.manifest_json, manifest)
    except Exception as exc:
        print(
            "WARNING: authoritative journal/state remain available, but the "
            f"rebuildable final manifest could not be replaced: {exc}"
        )
    print(f"Run results: {paths.run_dir}")
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
