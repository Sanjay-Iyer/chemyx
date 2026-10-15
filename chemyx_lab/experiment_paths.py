"""Filesystem layout for the main Si6 experiment; no instrument dependencies.

Execution suffixes never alter the physical experiment ID. Historical readers
use explicit layout detection, so an old folder need not be moved or rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from . import config


def safe_experiment_id(value: str) -> str:
    # Keep the established canonical ledger spelling. Reject rather than alias
    # two physical IDs through lossy replacement of unsafe path characters.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError("workflow.experiment_id must be 1–80 letters/digits/underscore/dot/hyphen, starting with a letter or digit; paths and empty IDs are unsafe")
    if value.endswith(".") or value.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
        *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ValueError("workflow.experiment_id cannot be a Windows device name or end with a dot")
    return value


def resolve_output_root(value: str | Path) -> Path:
    # A deployed YAML may use Windows separators even for repository-relative
    # paths. Do not resolve relative paths against the process working directory.
    value = str(value).replace("\\", "/")
    return config.resolve_repo_path(value).resolve()


@dataclass(frozen=True)
class ExperimentLayout:
    root: Path
    experiment_id: str
    started_at_local: datetime
    suffix: int
    stage_names: tuple[str, str]

    @property
    def metadata(self):
        return self.root / "metadata"

    @property
    def logs(self):
        return self.root / "logs"

    @property
    def final_qc(self):
        return self.root / "final_qc"

    @property
    def dose(self):
        return self.root / "channel_2_dose"

    def step(self, stage: str) -> Path:
        # Boundary actions retain their internal phase name and belong to the
        # source step, except the protected intermediate addition's own records.
        for index, name in enumerate(self.stage_names, 1):
            if stage == name or stage in {f"{name}_before_monitoring", f"{name}_after_monitoring"}:
                return self.root / f"step_{index}"
        raise ValueError(f"No output step for workflow stage {stage!r}")


def allocate_experiment(root: str | Path, experiment_id: str, stage_names=("stage_1", "stage_2"), *, now: datetime | None = None) -> ExperimentLayout:
    identity = safe_experiment_id(experiment_id)
    if len(stage_names) != 2 or len(set(stage_names)) != 2:
        raise ValueError("The main experiment output layout requires two distinct stages")
    started = now or datetime.now().astimezone()
    if started.tzinfo is None:
        started = started.astimezone()
    parent = resolve_output_root(root)
    parent.mkdir(parents=True, exist_ok=True)
    stem = f"{started:%Y%m%d}_{identity}"
    suffix = 0
    while True:
        directory = parent / (stem if suffix == 0 else f"{stem}_{suffix}")
        try:
            directory.mkdir()  # Exclusive atomic allocation; never exist_ok.
            break
        except FileExistsError:
            suffix += 1
    layout = ExperimentLayout(directory, experiment_id, started, suffix, tuple(stage_names))
    for path in (layout.metadata, layout.logs, layout.dose, layout.final_qc / "figures"):
        path.mkdir(parents=True)
    for stage in stage_names:
        for name in ("nmr_raw", "nmr_processed", "analysis", "figures", "logs"):
            (layout.step(stage) / name).mkdir(parents=True)
    return layout


def organized(run: Path) -> bool:
    return (run / "metadata" / "experiment_info.json").is_file() or (run / "logs" / "experiment_journal.jsonl").is_file()


def run_file(run: Path, legacy: str, current: str) -> Path:
    return run / (current if organized(run) else legacy)


def journal_path(run: Path) -> Path:
    return run_file(run, "operation_journal.jsonl", "logs/experiment_journal.jsonl")


def state_path(run: Path) -> Path:
    return run_file(run, "run_state.json", "logs/run_state.json")


class ExperimentRecorder:
    """Keep the authoritative journal and small readable stage projections.

    All safety decisions still use the underlying durable RunRecorder. These
    views neither replace its journal nor create a second recovery authority.
    """
    def __init__(self, recorder, layout):
        self.recorder = recorder
        self.layout = layout
        self.active_phase = None

    def __getattr__(self, name):
        return getattr(self.recorder, name)

    def record(self, event_type, **fields):
        import json
        event = self.recorder.record(event_type, **fields)
        with (self.layout.logs / "experiment.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{event['timestamp_utc']} {event_type} {json.dumps(fields, default=str)}\n")
        phase = self.active_phase or fields.get("workflow_phase")
        try:
            step = self.layout.step(phase)
        except ValueError:
            step = None
        destination = self.layout.dose if phase == f"{self.layout.stage_names[0]}_after_monitoring" or event_type.startswith("dose_") else step / "logs" if step else None
        if destination:
            with (destination / "operations.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, allow_nan=False) + "\n")
        return event
