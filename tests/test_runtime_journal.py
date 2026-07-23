import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chemyx_lab.recovery import (
    INSPECTION_EXIT_CODES,
    RecoveryClassification,
    inspect_run,
)
from chemyx_lab.runtime_journal import (
    JournalWriteError,
    OperationJournal,
    RunRecorder,
    TerminalJournalError,
)
from chemyx_lab.runtime_state import (
    JOURNAL_SCHEMA_VERSION,
    replay_journal,
)


def make_journal(tmp_path, *, ids=None):
    identifiers = iter(ids or [f"event-{number}" for number in range(1, 100)])
    ticks = iter(range(100))
    return OperationJournal(
        tmp_path / "operation_journal.jsonl",
        "run-1",
        monotonic_fn=lambda: next(ticks),
        utc_now_fn=lambda: datetime(2026, 7, 22, tzinfo=timezone.utc),
        event_id_fn=lambda: next(identifiers),
        software_version="test-commit",
    )


def append_operation(journal, state, *, operation_id="op-1", physical=True, **extra):
    return journal.append(
        "operation_lifecycle",
        operation_id=operation_id,
        operation_type=extra.pop("operation_type", "withdraw"),
        lifecycle_state=state,
        physical_state_effect=physical,
        workflow_phase="initial",
        **extra,
    )


def issue_codes(replay):
    return {issue.code for issue in replay.errors}


def test_normal_operation_is_ordered_unique_and_durable(tmp_path, monkeypatch):
    fsync_calls = []
    monkeypatch.setattr("chemyx_lab.runtime_journal.os.fsync", fsync_calls.append)
    journal = make_journal(tmp_path)

    append_operation(journal, "planned")
    append_operation(journal, "dispatch_started")
    append_operation(
        journal,
        "completed",
        expected_retained_volume_before_ml=0.0,
        expected_retained_volume_after_ml=8.0,
        physical_state_certainty="certain",
    )

    replay = replay_journal(journal.path)
    assert replay.valid
    assert [record["sequence"] for record in replay.records] == [1, 2, 3]
    assert len({record["event_id"] for record in replay.records}) == 3
    assert len(fsync_calls) == 3
    assert journal.path.read_bytes().endswith(b"\n")
    assert replay.state.estimated_retained_syringe_volume_ml == 8.0
    assert replay.state.physical_state_certainty == "certain"


def test_planned_without_dispatch_is_clean_nonphysical_interruption(tmp_path):
    journal = make_journal(tmp_path)
    append_operation(journal, "planned")

    result = inspect_run(tmp_path)

    assert result.classification is RecoveryClassification.CLEAN_NONPHYSICAL_INTERRUPTION
    assert result.possible_future_resume_candidate
    assert result.replay.state.physical_state_certainty == "certain"


@pytest.mark.parametrize("final_state", ["dispatch_started", "uncertain"])
def test_dispatched_or_uncertain_motion_requires_manual_inspection(tmp_path, final_state):
    journal = make_journal(tmp_path)
    append_operation(journal, "planned")
    append_operation(journal, "dispatch_started")
    if final_state == "uncertain":
        append_operation(
            journal,
            "uncertain",
            physical_state_certainty="uncertain",
        )

    result = inspect_run(tmp_path)

    assert result.classification is RecoveryClassification.PHYSICAL_STATE_UNCERTAIN
    assert result.replay.state.manual_inspection_required
    assert not result.possible_future_resume_candidate


def test_hardware_completed_before_durable_completion_remains_uncertain(tmp_path):
    journal = make_journal(tmp_path)
    append_operation(journal, "planned")
    append_operation(journal, "dispatch_started")

    replay = replay_journal(journal.path)

    assert replay.state.physical_state_certainty == "uncertain"
    assert replay.incomplete_operations[0]["lifecycle_state"] == "dispatch_started"


def test_confirmed_completion_reconstructs_as_certain(tmp_path):
    journal = make_journal(tmp_path)
    append_operation(journal, "planned")
    append_operation(journal, "dispatch_started")
    append_operation(
        journal,
        "completed",
        physical_state_certainty="certain",
        expected_retained_volume_after_ml=13.0,
    )

    replay = replay_journal(journal.path)

    assert replay.valid
    assert replay.state.physical_state_certainty == "certain"
    assert replay.state.estimated_retained_syringe_volume_ml == 13.0
    assert not replay.incomplete_operations


def test_missing_snapshot_is_rebuilt_only_when_explicit(tmp_path):
    journal = make_journal(tmp_path)
    journal.append("phase_transition", new_state="initial")

    inspected = inspect_run(tmp_path)
    assert inspected.snapshot_status == "missing"
    assert not (tmp_path / "run_state.json").exists()

    rebuilt = inspect_run(tmp_path, rebuild_state=True)
    assert rebuilt.snapshot_status == "rebuilt"
    assert json.loads((tmp_path / "run_state.json").read_text())["last_applied_sequence"] == 1


def test_stale_snapshot_is_detected_and_rebuilt(tmp_path):
    journal = make_journal(tmp_path)
    recorder = RunRecorder(journal, tmp_path / "run_state.json")
    recorder.record("phase_transition", new_state="one")
    stale = (tmp_path / "run_state.json").read_text()
    recorder.record("phase_transition", new_state="two")
    (tmp_path / "run_state.json").write_text(stale)

    assert inspect_run(tmp_path).snapshot_status == "behind"
    rebuilt = inspect_run(tmp_path, rebuild_state=True)
    assert rebuilt.snapshot_status == "rebuilt"
    assert json.loads((tmp_path / "run_state.json").read_text())["last_applied_sequence"] == 2


def test_snapshot_ahead_and_corrupt_snapshot_are_detected(tmp_path):
    journal = make_journal(tmp_path)
    journal.append("phase_transition", new_state="one")
    state_path = tmp_path / "run_state.json"
    state_path.write_text('{"last_applied_sequence":99}')
    assert inspect_run(tmp_path).snapshot_status == "ahead"

    state_path.write_text("not json")
    result = inspect_run(tmp_path)
    assert result.snapshot_status == "corrupt"
    assert result.replay.valid


def test_partial_final_record_is_reported_and_preserved(tmp_path):
    journal = make_journal(tmp_path)
    journal.append("phase_transition", new_state="one")
    with journal.path.open("ab") as handle:
        handle.write(b'{"schema_version":1')

    replay = replay_journal(journal.path)

    assert replay.partial_trailing_record
    assert "partial_trailing_record" in issue_codes(replay)
    assert replay.state.last_applied_sequence == 1


def test_empty_journal_is_corrupt(tmp_path):
    journal_path = tmp_path / "operation_journal.jsonl"
    journal_path.write_bytes(b"")

    replay = replay_journal(journal_path)

    assert "journal_empty" in issue_codes(replay)


def test_complete_record_without_final_newline_is_applied_but_corrupt(tmp_path):
    journal = make_journal(tmp_path)
    journal.append("terminal", terminal_status="completed")
    journal.path.write_bytes(journal.path.read_bytes().rstrip(b"\n"))

    replay = replay_journal(journal.path)

    assert replay.state.terminal_status == "completed"
    assert "missing_final_newline" in issue_codes(replay)


def test_malformed_middle_record_causes_corruption_without_rewriting(tmp_path):
    journal = make_journal(tmp_path)
    journal.append("phase_transition", new_state="one")
    journal.append("terminal", terminal_status="completed")
    lines = journal.path.read_bytes().splitlines(keepends=True)
    original = lines[0] + b"not-json\n" + lines[1]
    journal.path.write_bytes(original)

    result = inspect_run(tmp_path)

    assert result.classification is RecoveryClassification.JOURNAL_CORRUPT
    assert journal.path.read_bytes() == original
    assert "malformed_record" in issue_codes(result.replay)
    assert result.replay.state.terminal_status == "completed"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda records: records[1].update(sequence=1), "duplicate_sequence"),
        (lambda records: records[1].update(sequence=3), "sequence_gap"),
        (lambda records: records[1].update(event_id=records[0]["event_id"]), "duplicate_event_id"),
        (lambda records: records[1].update(schema_version=99), "unknown_schema_version"),
        (lambda records: records[1].update(run_id="other"), "inconsistent_run_id"),
    ],
)
def test_replay_detects_identity_and_order_corruption(tmp_path, mutate, expected):
    journal = make_journal(tmp_path)
    journal.append("phase_transition", new_state="one")
    journal.append("phase_transition", new_state="two")
    records = [json.loads(line) for line in journal.path.read_text().splitlines()]
    mutate(records)
    journal.path.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    assert expected in issue_codes(replay_journal(journal.path))


def test_impossible_transition_is_detected(tmp_path):
    journal = make_journal(tmp_path)
    append_operation(journal, "planned")
    append_operation(journal, "completed")

    assert "invalid_state_transition" in issue_codes(replay_journal(journal.path))


def test_terminal_event_blocks_writer_dispatch(tmp_path):
    journal = make_journal(tmp_path)
    journal.append("terminal", terminal_status="operator_aborted")

    with pytest.raises(TerminalJournalError):
        append_operation(journal, "planned")


def test_replay_detects_physical_operation_after_terminal(tmp_path):
    journal = make_journal(tmp_path)
    journal.append("terminal", terminal_status="operator_aborted")
    records = [json.loads(journal.path.read_text().splitlines()[0])]
    later = dict(records[0])
    later.update(
        sequence=2,
        event_id="later",
        event_type="operation_lifecycle",
        operation_id="op-late",
        operation_type="withdraw",
        lifecycle_state="planned",
        physical_state_effect=True,
    )
    journal.path.write_text("\n".join(json.dumps(item) for item in [records[0], later]) + "\n")

    replay = replay_journal(journal.path)

    assert "physical_operation_after_terminal" in issue_codes(replay)
    assert replay.state.physical_state_certainty == "uncertain"


@pytest.mark.parametrize(
    ("status", "classification"),
    [
        ("completed", RecoveryClassification.TERMINAL_COMPLETED),
        ("maximum_duration_reached", RecoveryClassification.TERMINAL_NONCOMPLETION),
        ("operator_aborted", RecoveryClassification.TERMINAL_NONCOMPLETION),
        ("analysis_inconclusive", RecoveryClassification.TERMINAL_NONCOMPLETION),
    ],
)
def test_terminal_classifications(tmp_path, status, classification):
    journal = make_journal(tmp_path)
    journal.append("terminal", terminal_status=status)

    result = inspect_run(tmp_path)

    assert result.classification is classification
    assert result.exit_code == INSPECTION_EXIT_CODES[classification]


def test_legacy_run_is_not_a_resume_candidate(tmp_path):
    (tmp_path / "manifest.json").write_text('{"status":"completed"}')

    result = inspect_run(tmp_path)

    assert result.classification is RecoveryClassification.LEGACY_RUN_WITHOUT_JOURNAL
    assert not result.possible_future_resume_candidate


def test_operator_confirmation_and_abort_are_reconstructable(tmp_path):
    journal = make_journal(tmp_path)
    journal.append(
        "operator_checkpoint",
        operation_id="check-1",
        checkpoint="lower needle",
        result_classification="confirmed",
    )
    journal.append(
        "operator_checkpoint",
        operation_id="check-2",
        checkpoint="add reagent",
        result_classification="aborted",
    )
    journal.append("terminal", terminal_status="operator_aborted")

    replay = replay_journal(journal.path)

    assert [record["result_classification"] for record in replay.records[:2]] == ["confirmed", "aborted"]
    assert replay.state.terminal_status == "operator_aborted"


class FailingJournal(OperationJournal):
    def __init__(self, *args, fail_sequence, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_sequence = fail_sequence

    def _durable_write(self, payload):
        if self.next_sequence == self.fail_sequence:
            raise OSError("injected durable-write failure")
        super()._durable_write(payload)


def test_failed_write_does_not_advance_sequence(tmp_path):
    journal = FailingJournal(
        tmp_path / "operation_journal.jsonl",
        "run-1",
        fail_sequence=2,
    )
    journal.append("phase_transition", new_state="one")

    with pytest.raises(JournalWriteError, match="injected"):
        journal.append("phase_transition", new_state="two")

    assert journal.next_sequence == 2
    assert len(journal.path.read_text().splitlines()) == 1
