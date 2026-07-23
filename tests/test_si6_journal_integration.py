import copy
from pathlib import Path

import pytest
import yaml

from chemyx_lab.config import PumpConfig
from chemyx_lab.recovery import RecoveryClassification, inspect_run
from chemyx_lab.runtime_journal import (
    OperationJournal,
    RunRecorder,
    TerminalJournalError,
)
from chemyx_lab.runtime_state import replay_journal
from chemyx_lab.workflows.si6_automated_nmr import (
    OperatorAbortError,
    PumpSafetyState,
    StopStatus,
    TerminalStatus,
    attempt_emergency_stop,
    load_si6_config,
    main,
    operator_checkpoint,
    run_safe_metered_move,
)


CONFIG = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "experiments"
    / "02_si6_automated_nmr.yaml"
)


class FakePump:
    def __init__(self, *, stop_response="stop", stop_error=None):
        self.calls = []
        self.stop_response = stop_response
        self.stop_error = stop_error

    def set_volume(self, value):
        self.calls.append(("set_volume", value))
        return "volume"

    def start(self, delay=None):
        self.calls.append(("start", delay))
        return "start"

    def stop(self):
        self.calls.append(("stop", None))
        if self.stop_error:
            raise self.stop_error
        return self.stop_response


def pump_config():
    return PumpConfig(
        port="FAKE",
        baud_rate=115200,
        channel=1,
        units=0,
        diameter=28.6,
        rate=5.0,
        volume=5.0,
        timeout=2.0,
        response_delay=0.0,
    )


class FailAtRecorder:
    def __init__(self, fail_at):
        self.fail_at = fail_at
        self.calls = []

    def record(self, event_type, **fields):
        self.calls.append((event_type, fields))
        if len(self.calls) == self.fail_at:
            raise OSError("injected journal failure")
        return {"event_type": event_type, **fields}


@pytest.mark.parametrize("fail_at", [1, 2])
def test_motion_is_not_called_if_write_ahead_lifecycle_fails(fail_at):
    pump = FakePump()

    with pytest.raises(OSError, match="injected journal failure"):
        run_safe_metered_move(
            pump,
            pump_config(),
            "withdraw",
            8.0,
            PumpSafetyState(),
            sleep_fn=lambda _label, _seconds: None,
            recorder=FailAtRecorder(fail_at),
        )

    assert pump.calls == []


class FailMoveCompletionRecorder:
    def __init__(self):
        self.calls = []

    def record(self, event_type, **fields):
        self.calls.append((event_type, fields))
        if (
            fields.get("operation_type") == "withdraw"
            and fields.get("lifecycle_state") == "completed"
        ):
            raise OSError("completion journal failure")
        return {"event_type": event_type, **fields}


def test_journal_failure_after_motion_makes_state_uncertain_and_stops():
    pump = FakePump()
    state = PumpSafetyState()

    with pytest.raises(OSError, match="completion journal failure"):
        run_safe_metered_move(
            pump,
            pump_config(),
            "withdraw",
            8.0,
            state,
            sleep_fn=lambda _label, _seconds: None,
            recorder=FailMoveCompletionRecorder(),
        )

    assert state.uncertain
    assert state.uncertain_operation == "withdraw 8 mL"
    assert [name for name, _value in pump.calls].count("stop") >= 2


def test_successful_move_and_stop_have_replayable_lifecycles(tmp_path):
    journal = OperationJournal(tmp_path / "operation_journal.jsonl", "run-1")
    recorder = RunRecorder(journal, tmp_path / "run_state.json")
    pump = FakePump()

    run_safe_metered_move(
        pump,
        pump_config(),
        "withdraw",
        8.0,
        PumpSafetyState(),
        sleep_fn=lambda _label, _seconds: None,
        recorder=recorder,
        workflow_phase="initial",
        cycle_number=1,
    )

    replay = replay_journal(journal.path)
    move_states = [
        record["lifecycle_state"]
        for record in replay.records
        if record.get("operation_type") == "withdraw"
    ]
    stop_states = [
        record["lifecycle_state"]
        for record in replay.records
        if record.get("operation_type") == "pump_stop"
    ]
    assert move_states == ["planned", "dispatch_started", "completed"]
    assert stop_states == ["planned", "dispatch_started", "completed"]
    assert replay.state.estimated_retained_syringe_volume_ml == 8.0
    assert replay.valid


@pytest.mark.parametrize(
    ("response", "error", "status", "final_state"),
    [
        ("stop", None, StopStatus.SUCCEEDED, "completed"),
        (None, None, StopStatus.UNCONFIRMED, "uncertain"),
        (None, RuntimeError("failed"), StopStatus.FAILED, "uncertain"),
    ],
)
def test_stop_outcomes_are_journaled(tmp_path, response, error, status, final_state):
    journal = OperationJournal(tmp_path / "operation_journal.jsonl", "run-1")
    recorder = RunRecorder(journal, tmp_path / "run_state.json")
    state = PumpSafetyState()

    actual = attempt_emergency_stop(
        FakePump(stop_response=response, stop_error=error),
        state,
        recorder,
    )

    records = replay_journal(journal.path).records
    assert actual is status
    assert records[-1]["lifecycle_state"] == final_state
    assert records[-1]["result_classification"] == status.value


def test_operator_confirmation_and_abort_are_durably_recorded(
    tmp_path, monkeypatch
):
    journal = OperationJournal(tmp_path / "operation_journal.jsonl", "run-1")
    recorder = RunRecorder(journal, tmp_path / "run_state.json")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["yes", "no"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    operator_checkpoint("lower needle", recorder)
    with pytest.raises(OperatorAbortError):
        operator_checkpoint("add reagent", recorder)

    outcomes = [
        record["result_classification"]
        for record in replay_journal(journal.path).records
    ]
    assert outcomes == ["requested", "confirmed", "requested", "aborted"]


def test_inspection_cli_never_constructs_pump(tmp_path, monkeypatch, capsys):
    journal = OperationJournal(tmp_path / "operation_journal.jsonl", "run-1")
    recorder = RunRecorder(journal, tmp_path / "run_state.json")
    recorder.record("terminal", terminal_status="completed")

    class ForbiddenPump:
        def __init__(self, *args, **kwargs):
            raise AssertionError("inspection must never construct hardware")

    monkeypatch.setattr(
        "chemyx_lab.workflows.si6_automated_nmr.Pump", ForbiddenPump
    )

    assert main(["--inspect-run", str(tmp_path)]) == 0
    assert "terminal_completed" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("fixture_kind", "expected_exit", "expected_text"),
    [
        ("completed", 0, "terminal_completed"),
        ("timeout", 10, "terminal_noncompletion"),
        ("operator_aborted", 10, "terminal_noncompletion"),
        ("uncertain", 12, "physical_state_uncertain"),
        ("stale", 11, "Snapshot status: behind"),
        ("corrupt", 13, "journal_corrupt"),
        ("legacy", 14, "legacy_run_without_journal"),
    ],
)
def test_inspection_mode_fixture_matrix_never_constructs_hardware(
    tmp_path,
    monkeypatch,
    capsys,
    fixture_kind,
    expected_exit,
    expected_text,
):
    run_dir = tmp_path / fixture_kind
    run_dir.mkdir()
    if fixture_kind != "legacy":
        journal = OperationJournal(
            run_dir / "operation_journal.jsonl", fixture_kind
        )
        recorder = RunRecorder(journal, run_dir / "run_state.json")
        if fixture_kind == "completed":
            recorder.record("terminal", terminal_status="completed")
        elif fixture_kind == "timeout":
            recorder.record(
                "terminal", terminal_status="maximum_duration_reached"
            )
        elif fixture_kind == "operator_aborted":
            recorder.record("terminal", terminal_status="operator_aborted")
        elif fixture_kind == "uncertain":
            recorder.record(
                "operation_lifecycle",
                operation_id="withdraw-1",
                operation_type="withdraw",
                lifecycle_state="planned",
                physical_state_effect=True,
            )
            recorder.record(
                "operation_lifecycle",
                operation_id="withdraw-1",
                operation_type="withdraw",
                lifecycle_state="dispatch_started",
                physical_state_effect=True,
            )
        elif fixture_kind == "stale":
            recorder.record("phase_transition", new_state="one")
            journal.append("phase_transition", new_state="two")
        elif fixture_kind == "corrupt":
            recorder.record("phase_transition", new_state="one")
            with journal.path.open("ab") as handle:
                handle.write(b'{"partial":')
    else:
        (run_dir / "manifest.json").write_text('{"status":"historical"}')

    class ForbiddenPump:
        def __init__(self, *args, **kwargs):
            raise AssertionError("inspection must never construct hardware")

    monkeypatch.setattr(
        "chemyx_lab.workflows.si6_automated_nmr.Pump", ForbiddenPump
    )

    assert main(["--inspect-run", str(run_dir)]) == expected_exit
    assert expected_text in capsys.readouterr().out


def test_dry_run_creates_journal_without_constructing_pump(
    tmp_path, monkeypatch
):
    raw = copy.deepcopy(load_si6_config(CONFIG))
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    config_path = tmp_path / "si6.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    class ForbiddenPump:
        def __init__(self, *args, **kwargs):
            raise AssertionError("dry-run must never construct hardware")

    monkeypatch.setattr(
        "chemyx_lab.workflows.si6_automated_nmr.Pump", ForbiddenPump
    )

    assert main(["--workflow-config", str(config_path), "--dry-run"]) == 0
    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    replay = replay_journal(run_dirs[0] / "operation_journal.jsonl")
    assert replay.valid
    assert replay.state.terminal_status == TerminalStatus.OPERATOR_ABORTED.value
    assert (run_dirs[0] / "run_state.json").exists()


def test_uncertain_fixture_is_not_future_resume_candidate(tmp_path):
    journal = OperationJournal(tmp_path / "operation_journal.jsonl", "run-1")
    journal.append(
        "operation_lifecycle",
        operation_id="withdraw-1",
        operation_type="withdraw",
        lifecycle_state="planned",
        physical_state_effect=True,
    )
    journal.append(
        "operation_lifecycle",
        operation_id="withdraw-1",
        operation_type="withdraw",
        lifecycle_state="dispatch_started",
        physical_state_effect=True,
    )

    result = inspect_run(tmp_path)

    assert result.classification is RecoveryClassification.PHYSICAL_STATE_UNCERTAIN
    assert not result.possible_future_resume_candidate


def test_analysis_failure_terminal_cannot_become_completed(tmp_path):
    journal = OperationJournal(tmp_path / "operation_journal.jsonl", "run-1")
    recorder = RunRecorder(journal, tmp_path / "run_state.json")
    recorder.record(
        "spectrum_validation",
        result_classification="invalid",
        error_type="NmrProcessingError",
        error_message="invalid spectrum",
    )
    recorder.record(
        "terminal",
        terminal_status=TerminalStatus.ANALYSIS_INCONCLUSIVE.value,
    )

    with pytest.raises(TerminalJournalError):
        recorder.record("terminal", terminal_status=TerminalStatus.COMPLETED.value)

    replay = replay_journal(journal.path)
    assert replay.state.terminal_status == TerminalStatus.ANALYSIS_INCONCLUSIVE.value
