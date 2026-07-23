"""Offline-only recovery inspection for journal-backed Si6 runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .runtime_state import (
    ReplayIssue,
    ReplayResult,
    load_state_snapshot,
    replay_journal,
    write_state_atomic,
)


class RecoveryClassification(str, Enum):
    TERMINAL_COMPLETED = "terminal_completed"
    TERMINAL_NONCOMPLETION = "terminal_noncompletion"
    CLEAN_NONPHYSICAL_INTERRUPTION = "clean_nonphysical_interruption"
    MANUAL_INSPECTION_REQUIRED = "manual_inspection_required"
    PHYSICAL_STATE_UNCERTAIN = "physical_state_uncertain"
    JOURNAL_CORRUPT = "journal_corrupt"
    LEGACY_RUN_WITHOUT_JOURNAL = "legacy_run_without_journal"


INSPECTION_EXIT_CODES = {
    RecoveryClassification.TERMINAL_COMPLETED: 0,
    RecoveryClassification.TERMINAL_NONCOMPLETION: 10,
    RecoveryClassification.CLEAN_NONPHYSICAL_INTERRUPTION: 11,
    RecoveryClassification.PHYSICAL_STATE_UNCERTAIN: 12,
    RecoveryClassification.JOURNAL_CORRUPT: 13,
    RecoveryClassification.LEGACY_RUN_WITHOUT_JOURNAL: 14,
    RecoveryClassification.MANUAL_INSPECTION_REQUIRED: 15,
}


@dataclass
class InspectionResult:
    run_dir: Path
    classification: RecoveryClassification
    replay: ReplayResult | None = None
    snapshot_status: str = "not_checked"
    diagnostics: list[ReplayIssue] = field(default_factory=list)
    possible_future_resume_candidate: bool = False

    @property
    def exit_code(self) -> int:
        return INSPECTION_EXIT_CODES[self.classification]


def inspect_run(run_dir: Path, *, rebuild_state: bool = False) -> InspectionResult:
    """Inspect or explicitly rebuild projection data; never initialize hardware."""
    run_dir = Path(run_dir)
    journal_path = run_dir / "operation_journal.jsonl"
    state_path = run_dir / "run_state.json"
    if not journal_path.exists():
        return InspectionResult(
            run_dir,
            RecoveryClassification.LEGACY_RUN_WITHOUT_JOURNAL,
            snapshot_status="legacy",
        )

    replay = replay_journal(journal_path)
    diagnostics = list(replay.warnings) + list(replay.errors)
    snapshot_status = "current"
    try:
        snapshot = load_state_snapshot(state_path)
    except FileNotFoundError:
        snapshot_status = "missing"
    except (OSError, ValueError) as exc:
        snapshot_status = "corrupt"
        diagnostics.append(ReplayIssue("snapshot_corrupt", str(exc)))
    else:
        snapshot_sequence = snapshot.get("last_applied_sequence")
        journal_sequence = replay.state.last_applied_sequence
        if not isinstance(snapshot_sequence, int):
            snapshot_status = "corrupt"
            diagnostics.append(
                ReplayIssue(
                    "snapshot_corrupt", "Snapshot sequence is not an integer"
                )
            )
        elif snapshot_sequence < journal_sequence:
            snapshot_status = "behind"
            diagnostics.append(
                ReplayIssue(
                    "snapshot_behind",
                    f"Snapshot sequence {snapshot_sequence} is behind journal "
                    f"sequence {journal_sequence}",
                )
            )
        elif snapshot_sequence > journal_sequence:
            snapshot_status = "ahead"
            diagnostics.append(
                ReplayIssue(
                    "snapshot_ahead",
                    f"Snapshot sequence {snapshot_sequence} is ahead of journal "
                    f"sequence {journal_sequence}",
                )
            )

    if rebuild_state and replay.valid:
        write_state_atomic(state_path, replay.state)
        snapshot_status = "rebuilt"

    state = replay.state
    if replay.errors:
        classification = RecoveryClassification.JOURNAL_CORRUPT
    elif state.physical_state_certainty == "uncertain":
        classification = RecoveryClassification.PHYSICAL_STATE_UNCERTAIN
    elif state.manual_inspection_required:
        classification = RecoveryClassification.MANUAL_INSPECTION_REQUIRED
    elif state.terminal_status == "completed":
        classification = RecoveryClassification.TERMINAL_COMPLETED
    elif state.terminal_status is not None:
        classification = RecoveryClassification.TERMINAL_NONCOMPLETION
    else:
        classification = RecoveryClassification.CLEAN_NONPHYSICAL_INTERRUPTION
    candidate = (
        classification
        is RecoveryClassification.CLEAN_NONPHYSICAL_INTERRUPTION
        and replay.valid
        and state.physical_state_certainty == "certain"
    )
    return InspectionResult(
        run_dir,
        classification,
        replay,
        snapshot_status,
        diagnostics,
        candidate,
    )


def format_inspection(result: InspectionResult) -> str:
    lines = [
        f"Run directory: {result.run_dir}",
        f"Classification: {result.classification.value}",
        f"Inspection exit code: {result.exit_code}",
        f"Snapshot status: {result.snapshot_status}",
    ]
    if result.replay is None:
        lines.extend(
            [
                "Journal: missing (legacy run)",
                "Safe resume: unavailable; start a new journal-backed run",
            ]
        )
        return "\n".join(lines)
    state = result.replay.state
    lines.extend(
        [
            f"Last durable sequence: {state.last_applied_sequence}",
            f"Last durable operation: {state.last_operation}",
            f"Unresolved operations: {result.replay.incomplete_operations}",
            "Estimated retained volume: "
            f"{state.estimated_retained_syringe_volume_ml:g} mL",
            f"Physical state: {state.physical_state_certainty}",
            f"Terminal status: {state.terminal_status or 'none'}",
            f"Manual inspection required: {state.manual_inspection_required}",
            "Possible future resume candidate: "
            + (
                "yes, pending a later implementation and operator approval"
                if result.possible_future_resume_candidate
                else "no"
            ),
        ]
    )
    for issue in result.diagnostics:
        lines.append(f"{issue.code}: {issue.message}")
    return "\n".join(lines)
