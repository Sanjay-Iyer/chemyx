"""Sequential independent channels through the real three-instrument mock rig."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json

import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.recovery import RecoveryClassification, inspect_run
from chemyx_lab.runtime_journal import OperationJournal
from chemyx_lab.runtime_state import replay_journal
from chemyx_lab.workflows import si6_automated_nmr as base
from chemyx_lab.workflows import three_instrument_si6 as integrated


EXAMPLE = config.REPO_ROOT / "config_templates/experiments/si6_two_channel_once.yaml"
ARDUINO = config.REPO_ROOT / "arduino/configs/arduino.example.yaml"


@pytest.fixture(autouse=True)
def no_environment_overrides(monkeypatch):
    for key in ("CHEMYX_CHANNEL", "CHEMYX_DIAMETER", "CHEMYX_RATE", "CHEMYX_UNITS", "CHEMYX_VOLUME"):
        monkeypatch.delenv(key, raising=False)


def recipe():
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def write_recipe(tmp_path, raw):
    raw = deepcopy(raw)
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def rig(tmp_path, raw):
    path = write_recipe(tmp_path, raw)
    settings = integrated.prepare(path, tmp_path / "missing_machine.yaml", ARDUINO, mock=True)
    return integrated.open_services(*settings, identity=integrated.RunIdentity("si6", True), fast_mock_processing=True)


def events(s):
    return [json.loads(line) for line in s.paths.journal_jsonl.read_text(encoding="utf-8").splitlines()]


def completed_transfers(s):
    return [e for e in events(s) if e.get("operation_type") in {"withdraw", "infuse"} and e.get("lifecycle_state") == "completed"]


def starts(s):
    return [line.strip() for line in s.pump.ser.tx_log if "start" in line and "help" not in line]


@pytest.mark.parametrize("channel", [0, 1, 2])
def test_legacy_cycle_and_entire_experiment_on_selected_channel(tmp_path, channel):
    raw = yaml.safe_load(base.DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["pump"]["channel"] = channel
    raw["workflow"]["first_addition_stage"] = None
    raw["workflow"]["repeat_addition_rounds"] = 0
    raw["workflow"]["initial_stage"]["plateau_stopping_enabled"] = False
    raw["three_instrument"]["initial_plateau_stopping_enabled"] = False
    with rig(tmp_path, raw) as s:
        outcome = integrated.run_experiment(s, mock_cycles_per_stage=1)
        assert outcome.status is base.TerminalStatus.COMPLETED
        assert [e["channel"] for e in completed_transfers(s)] == [channel] * 5
        assert starts(s) == [(f"{channel} " if channel else "") + "start 0"] * 5
        assert s.state.retained_volume_ml == 0
        assert not s.state.uncertain


def test_sequence_1_2_1_independent_settings_volumes_and_replay(tmp_path):
    raw = recipe()
    raw["pump"]["channels"]["1"]["initial_retained_volume_ml"] = 1
    with rig(tmp_path, raw) as s:
        integrated.home_and_raise(s)
        s.pump_move("withdraw", 1, "UP", stage="sequence", cycle=1)
        s.pump_move("infuse", 0.05, "UP", stage="sequence", cycle=1, channel=2)
        s.pump_move("infuse", 1, "UP", stage="sequence", cycle=1, channel=1)
        assert starts(s) == ["1 start 0", "2 start 0", "1 start 0"]
        assert s.channel_states[1].retained_volume_ml == 1
        assert s.channel_states[2].retained_volume_ml == pytest.approx(0.45)
        assert s.channel_states[1].cumulative_withdrawn_ml == 1
        assert s.channel_states[1].cumulative_infused_ml == 1
        assert s.channel_states[2].cumulative_infused_ml == 0.05
        device = s.pump.ser.channel_states
        assert float(device[1]["diameter"]) == 20
        assert float(device[2]["diameter"]) == 4.7
        assert float(device[1]["rate"]) == 5
        assert float(device[2]["rate"]) == 0.5
        replay = inspect_run(s.paths.run_dir, rebuild_state=True).replay
        assert replay.valid
        assert replay.state.estimated_retained_syringe_volume_ml == 1
        assert replay.state.pump_channels["1"]["retained_volume_ml"] == 1
        assert replay.state.pump_channels["2"]["retained_volume_ml"] == pytest.approx(0.45)
        assert replay.state.pump_channels["2"]["cumulative_infused_ml"] == 0.05
        assert json.loads(s.paths.state_json.read_text())["pump_channels"] == replay.state.pump_channels


def test_configured_default_2_and_omitted_operation_channel(tmp_path):
    raw = recipe()
    raw["pump"]["default_channel"] = 2
    raw["pump"]["channels"]["2"]["syringe_capacity_ml"] = 20
    raw["pump"]["channels"]["2"]["initial_retained_volume_ml"] = 0
    raw["workflow"]["initial_stage"]["after_monitoring"] = []
    with rig(tmp_path, raw) as s:
        integrated.home_and_raise(s)
        s.pump_move("withdraw", 0.5, "UP", stage="default", cycle=1)
        assert starts(s) == ["2 start 0"]
        assert s.channel_states[1].retained_volume_ml == 0
        assert s.channel_states[2].retained_volume_ml == 0.5


def test_one_channel_2_event_in_full_three_instrument_workflow(tmp_path):
    with rig(tmp_path, recipe()) as s:
        outcome = integrated.run_experiment(s, mock_cycles_per_stage=2)
        assert outcome.status is base.TerminalStatus.COMPLETED
        transfers = completed_transfers(s)
        assert [e["channel"] for e in transfers] == [1] * 10 + [2] + [1] * 10
        assert starts(s) == ["1 start 0"] * 10 + ["2 start 0"] + ["1 start 0"] * 10
        assert s.channel_states[1].retained_volume_ml == 0
        assert s.channel_states[2].retained_volume_ml == pytest.approx(0.45)
        log = events(s)
        assert len([e for e in log if e["event_type"] == "nmr_retrieved"]) == 4
        assert any(e["event_type"] == "needle_transition" and e["target"] == "DOWN" for e in log)
        assert any(e["event_type"] == "stage_action" and e["action"] == "pause" for e in log)
        assert inspect_run(s.paths.run_dir).classification is RecoveryClassification.TERMINAL_COMPLETED


def test_boundary_nmr_wait_and_arduino_use_existing_processing_without_plateau_contamination(tmp_path):
    raw = recipe()
    # A balanced channel-1 sample around an extra NMR following the single dose.
    raw["workflow"]["initial_stage"]["after_monitoring"].extend([
        {"action": "withdraw", "volume_ml": 8, "needle_position": "UP"},
        {"action": "needle", "position": "DOWN"},
        {"action": "withdraw", "volume_ml": 5, "needle_position": "DOWN"},
        {"action": "pause", "seconds": 15},
        {"action": "nmr", "needle_position": "DOWN"},
        {"action": "infuse", "volume_ml": 13, "needle_position": "DOWN"},
        {"action": "needle", "position": "UP"},
    ])
    with rig(tmp_path, raw) as s:
        outcome = integrated.run_experiment(s, mock_cycles_per_stage=1)
        assert outcome.status is base.TerminalStatus.COMPLETED
        retrieved = [e for e in events(s) if e["event_type"] == "nmr_retrieved"]
        assert [e["workflow_phase"] for e in retrieved] == ["before_dose", "before_dose_after_monitoring", "after_dose"]
        assert [e["channel"] for e in completed_transfers(s)] == [1] * 5 + [2] + [1] * 8
        assert s.channel_states[1].retained_volume_ml == 0


@pytest.mark.parametrize("case", ["invalid", "missing", "fractional", "bool", "zero", "nonpump"])
def test_invalid_or_unconfigured_operation_channel_fails_before_transports(tmp_path, monkeypatch, case):
    raw = recipe()
    operation = raw["workflow"]["initial_stage"]["after_monitoring"][1]
    if case == "missing":
        del raw["pump"]["channels"]["2"]
    elif case == "nonpump":
        raw["workflow"]["cycle"][4]["channel"] = 2
    else:
        operation["channel"] = {"invalid": 3, "fractional": 1.5, "bool": True, "zero": 0}[case]
    opened = []
    monkeypatch.setattr(integrated, "Pump", lambda *a, **kw: opened.append("pump"))
    monkeypatch.setattr(integrated, "SerialTransport", lambda *a, **kw: opened.append("arduino"))
    with pytest.raises(ValueError, match="channel"):
        integrated.prepare(write_recipe(tmp_path, raw), tmp_path / "missing.yaml", ARDUINO, mock=True)
    assert opened == []


def test_cycle_overrides_route_cleanup_on_channel_2_and_return_to_default_1(tmp_path):
    raw = recipe()
    raw["workflow"]["initial_stage"]["after_monitoring"] = []
    for index in (7, 8):
        raw["workflow"]["cycle"][index]["channel"] = 2
    raw["pump"]["channels"]["2"]["syringe_capacity_ml"] = 10
    with rig(tmp_path, raw) as s:
        integrated.run_experiment(s, mock_cycles_per_stage=1)
        assert [e["channel"] for e in completed_transfers(s)] == [1, 1, 1, 2, 2] * 2
        assert s.channel_states[1].retained_volume_ml == 0
        assert s.channel_states[2].retained_volume_ml == 0.5


@pytest.mark.parametrize("case", ["capacity", "empty", "cycle_unbalanced", "repeat_doses"])
def test_capacity_and_balance_are_checked_per_channel(tmp_path, case):
    raw = recipe()
    if case == "capacity":
        raw["pump"]["channels"]["2"]["syringe_capacity_ml"] = 0.55
    elif case == "empty":
        raw["pump"]["channels"]["2"]["initial_retained_volume_ml"] = 0.01
    elif case == "cycle_unbalanced":
        raw["workflow"]["cycle"][0]["channel"] = 2
        raw["pump"]["channels"]["2"]["syringe_capacity_ml"] = 20
    else:
        raw["workflow"]["repeating_stages"] = [deepcopy(raw["workflow"]["initial_stage"])]
        raw["workflow"]["repeating_stages"][0]["name"] = "repeated_dose"
        raw["workflow"]["repeat_addition_rounds"] = 10
    with pytest.raises(ValueError, match="channel" if case == "cycle_unbalanced" else "channel 2"):
        base.load_si6_config(write_recipe(tmp_path, raw))


def test_runtime_capacity_refuses_start_and_preserves_both_states(tmp_path):
    with rig(tmp_path, recipe()) as s:
        integrated.home_and_raise(s)
        with pytest.raises(ValueError, match="channel 2"):
            s.pump_move("infuse", 0.6, "UP", stage="limit", cycle=1, channel=2)
        assert starts(s) == []
        assert s.channel_states[1].retained_volume_ml == 0
        assert s.channel_states[2].retained_volume_ml == 0.5


@pytest.mark.parametrize("condition", ["motion_active", "uncertain", "stop_unconfirmed"])
def test_channel_2_state_blocks_channel_1_and_needle_motion(tmp_path, condition):
    with rig(tmp_path, recipe()) as s:
        integrated.home_and_raise(s)
        if condition == "stop_unconfirmed":
            s.channel_states[2].last_stop_status = base.StopStatus.UNCONFIRMED
        else:
            setattr(s.channel_states[2], condition, True)
        with pytest.raises(integrated.VerificationError, match="channel 2"):
            s.move_needle("DOWN")
        with pytest.raises(integrated.VerificationError, match="channel 2"):
            s.pump_move("withdraw", 1, "UP", stage="blocked", cycle=1)
        assert starts(s) == []


def test_channel_2_interruption_stops_both_and_leaves_channel_1_bookkeeping_intact(tmp_path):
    with rig(tmp_path, recipe()) as s:
        integrated.home_and_raise(s)
        s.sleep = lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("channel 2 interrupted"))
        with pytest.raises(KeyboardInterrupt):
            integrated.run_stage_actions(s, ({"action": "infuse", "channel": 2, "volume_ml": 0.05, "needle_position": "UP"},), stage="fault", phase="before_monitoring", cycle=1, rows=[], started=datetime.now())
        assert s.channel_states[2].uncertain
        assert not s.channel_states[1].uncertain
        assert s.channel_states[1].retained_volume_ml == 0
        assert s.channel_states[2].retained_volume_ml == 0.5
        assert s.pump.ser.tx_log[-2:] == ["1 stop\r", "2 stop\r"]
        assert "2 stop\r" in s.pump.ser.tx_log[:-2]
        assert inspect_run(s.paths.run_dir).classification is RecoveryClassification.PHYSICAL_STATE_UNCERTAIN


def test_failed_stop_on_channel_2_does_not_skip_channel_1_stop(tmp_path):
    with rig(tmp_path, recipe()) as s:
        original = s.pump.stop
        def stop(channel=None):
            if channel == 2:
                raise RuntimeError("channel 2 stop link failed")
            return original(channel=channel)
        s.pump.stop = stop
        s.channel_states[2].motion_active = True
        s.stop_all()
        assert s.channel_states[2].last_stop_status is base.StopStatus.FAILED
        assert s.channel_states[1].last_stop_status is base.StopStatus.SUCCEEDED
        with pytest.raises(integrated.VerificationError):
            s.move_needle("DOWN")


def test_units_and_rate_override_are_restored_per_channel(tmp_path):
    raw = recipe()
    raw["pump"]["channels"]["2"]["units"] = "uL/min"
    raw["pump"]["channels"]["2"]["rate_ml_min"] = 500
    raw["workflow"]["initial_stage"]["after_monitoring"] = []
    with rig(tmp_path, raw) as s:
        integrated.home_and_raise(s)
        waits = []
        s.sleep = lambda _, seconds: waits.append(seconds)
        s.pump_move("infuse", 0.05, "UP", stage="units", cycle=1, channel=2, rate_ml_min=250)
        assert "2 set volume 50.0\r" in s.pump.ser.tx_log
        s.pump_move("withdraw", 1, "UP", stage="units", cycle=1)
        s.pump_move("infuse", 0.05, "UP", stage="units", cycle=1, channel=2)
        assert starts(s) == ["2 start 0", "1 start 0", "2 start 0"]
        assert waits == [14, 14, 8]
        assert float(s.pump.ser.channel_states[2]["rate"]) == 500
        assert s.pump.ser.channel_states[1]["units"] == 0
        assert s.pump.ser.channel_states[2]["units"] == 2


def test_legacy_runner_rejects_extensions_instead_of_ignoring_them(tmp_path, capsys):
    assert base.main(["--workflow-config", str(write_recipe(tmp_path, recipe())), "--validate-only"]) == 2
    assert "three-instrument runner" in capsys.readouterr().out


def test_boundary_lists_run_once_even_when_monitoring_is_extended(tmp_path, monkeypatch):
    raw = recipe()
    raw["workflow"]["initial_stage"]["before_monitoring"] = [
        {"action": "infuse", "channel": 2, "volume_ml": 0.05, "needle_position": "UP"},
    ]
    calls = []
    def monitoring(stage, *_args, **_kwargs):
        calls.append(stage.name)
        if len(calls) == 1:
            outcome = base.RunOutcome(base.TerminalStatus.MAXIMUM_DURATION_REACHED, "limit", base.StageOutcome.RUNTIME_LIMIT_REACHED)
        else:
            outcome = base.RunOutcome(base.TerminalStatus.COMPLETED, "done", base.StageOutcome.SCHEDULED_MONITORING_COMPLETED)
        return base.MonitoringResult(outcome, 0, 0, 0)
    monkeypatch.setattr(base, "run_monitoring_stage", monitoring)
    with rig(tmp_path, raw) as s:
        outcome = integrated.run_experiment(s, stage_decision=lambda *_: "continue")
        assert outcome.status is base.TerminalStatus.COMPLETED
        assert calls == ["before_dose", "before_dose", "after_dose"]
        assert [e["workflow_phase"] for e in completed_transfers(s)] == ["before_dose_before_monitoring", "before_dose_after_monitoring"]
        assert s.channel_states[2].retained_volume_ml == pytest.approx(0.4)


def test_aborted_monitoring_skips_after_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "run_monitoring_stage", lambda *_a, **_kw: base.MonitoringResult(
        base.RunOutcome(base.TerminalStatus.MAXIMUM_DURATION_REACHED, "limit", base.StageOutcome.RUNTIME_LIMIT_REACHED), 0, 0, 0,
    ))
    with rig(tmp_path, recipe()) as s:
        outcome = integrated.run_experiment(s)
        assert outcome.status is base.TerminalStatus.OPERATOR_ABORTED
        assert starts(s) == []
        assert s.channel_states[2].retained_volume_ml == 0.5


def test_boundary_nmr_failure_requires_review_and_never_redoses(tmp_path):
    with rig(tmp_path, recipe()) as s:
        integrated.home_and_raise(s)
        s.acquire = lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("NMR link failed"))
        with pytest.raises(RuntimeError, match="NMR link failed"):
            integrated.run_stage_actions(s, (
                {"action": "infuse", "channel": 2, "volume_ml": 0.05, "needle_position": "UP"},
                {"action": "nmr", "needle_position": "UP"},
                {"action": "infuse", "channel": 2, "volume_ml": 0.05, "needle_position": "UP"},
            ), stage="fault", phase="before_monitoring", cycle=0, rows=[], started=datetime.now())
        assert starts(s) == ["2 start 0"]
        assert s.channel_states[2].retained_volume_ml == pytest.approx(0.45)
        assert inspect_run(s.paths.run_dir).classification is RecoveryClassification.MANUAL_INSPECTION_REQUIRED


def test_sampling_nmr_failure_cleans_up_each_channel_with_nonzero_initial_volumes(tmp_path):
    raw = recipe()
    raw["pump"]["channels"]["1"]["initial_retained_volume_ml"] = 1
    raw["pump"]["channels"]["2"]["syringe_capacity_ml"] = 10
    raw["workflow"]["initial_stage"]["after_monitoring"] = []
    for index in (7, 8):
        raw["workflow"]["cycle"][index]["channel"] = 2
    with rig(tmp_path, raw) as s:
        integrated.home_and_raise(s)
        s.acquire = lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("NMR unavailable"))
        with pytest.raises(integrated.MeasurementFailedAfterCleanup):
            integrated.sample_cycle(s, stage="failure", cycle=1, rows=[], started=datetime.now())
        assert [e["channel"] for e in completed_transfers(s)] == [1, 1, 1, 2, 2]
        assert s.channel_states[1].retained_volume_ml == 1
        assert s.channel_states[2].retained_volume_ml == 0.5
        assert not any(state.uncertain for state in s.channel_states.values())


def test_replay_marks_only_interrupted_channel_uncertain(tmp_path):
    journal = OperationJournal(tmp_path / "journal.jsonl", "channels")
    journal.append("phase_transition", default_pump_channel=1, pump_channels={
        "1": {"retained_volume_ml": 1}, "2": {"retained_volume_ml": 0.5},
    })
    fields = dict(operation_id="dose", channel=2, operation_type="infuse", physical_state_effect=True)
    journal.append("operation_lifecycle", lifecycle_state="planned", **fields)
    journal.append("operation_lifecycle", lifecycle_state="dispatch_started", **fields)
    replay = replay_journal(journal.path)
    assert replay.valid
    assert replay.state.physical_state_certainty == "uncertain"
    assert replay.state.pump_channels["2"]["uncertain"]
    assert not replay.state.pump_channels["1"].get("uncertain", False)
    assert replay.state.estimated_retained_syringe_volume_ml == 1


@pytest.mark.parametrize("case", ["bad_initial", "changed_channel"])
def test_replay_rejects_invalid_channel_evidence(tmp_path, case):
    journal = OperationJournal(tmp_path / "journal.jsonl", "channels")
    if case == "bad_initial":
        journal.append("phase_transition", default_pump_channel=1, pump_channels={"2": {"retained_volume_ml": "bad"}})
        expected = "invalid_initial_channels"
    else:
        fields = dict(operation_id="dose", operation_type="infuse", physical_state_effect=True)
        journal.append("operation_lifecycle", lifecycle_state="planned", channel=1, **fields)
        journal.append("operation_lifecycle", lifecycle_state="dispatch_started", channel=2, **fields)
        expected = "inconsistent_operation_channel"
    replay = replay_journal(journal.path)
    assert not replay.valid
    assert expected in [issue.code for issue in replay.errors]
