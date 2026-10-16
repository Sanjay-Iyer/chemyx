"""Production controller, mock instruments, isolated durable state and virtual time."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from chemyx_lab.analysis.stage_measurement import PeakEvidenceError
from chemyx_lab.testing.false_completion_validation import recipe, run_mock
from chemyx_lab.workflows import si6_automated_nmr as base
from chemyx_lab.workflows import three_instrument_si6 as si6
from chemyx_lab.workflows.dose_guard import DoseGuard, DoseReplayBlocked


def test_real_recipe_explicit_accepted_protocol():
    stages = base.build_stages(recipe()["workflow"])
    assert (stages[0].control_mode, stages[0].advance_at_duration, stages[0].max_hours,
            stages[0].interval_minutes, stages[0].min_completion_hours) == ("fixed_duration", True, 48, 120, None)
    assert (stages[1].control_mode, stages[1].min_completion_hours, stages[1].max_hours,
            stages[1].interval_minutes) == ("minimum_then_nmr", 2, 6, 30)
    assert stages[1].completion["consecutive_iterations"] == 4
    assert all(not s.max_measurements_explicit for s in stages)


@pytest.mark.parametrize("update", [
    {"advance_at_duration": False}, {"advance_at_duration": "true"},
    {"min_completion_hours": 40}, {"max_measurements": 20},
    {"control_mode": "typo"}, {"control_mode": "nmr"},
])
def test_unsafe_or_conflicting_protocol_rejected(update):
    raw = recipe()
    raw["workflow"]["initial_stage"].update(update)
    from chemyx_lab.workflows.si6_profile import validate_profile
    with pytest.raises(ValueError):
        validate_profile(raw, base.build_stages(raw["workflow"]))


@pytest.mark.parametrize("early_hour", [8,40,46])
def test_scheduler_ignores_early_flags_and_stale_count(early_hour):
    from dataclasses import replace
    stage = replace(base.build_stages(recipe()["workflow"])[0], max_measurements=1)
    clock = [0.]
    starts = []
    def measure(schedule):
        starts.append(clock[0])
        return base.MeasurementObservation(True, clock[0] >= early_hour*3600, "", "", "", clock[0], 4)
    result = base.run_monitoring_stage(stage, measure, monotonic_fn=lambda:clock[0],
        wall_now_fn=lambda:datetime(2026,1,1,tzinfo=timezone.utc),
        sleep_fn=lambda label,seconds:clock.__setitem__(0,clock[0]+seconds))
    assert result.outcome.stage_outcome is base.StageOutcome.TIME_LIMIT_COMPLETED
    assert result.outcome.status is base.TerminalStatus.COMPLETED
    assert clock[0] == 48*3600
    assert len(starts) == 24


@pytest.mark.parametrize("name,values1,values2,fault,missing", [
    ("A_NO_TARGET", [100]*30, [1]*30, "target_absent", None),
    ("B_EARLY_8", [100]+[1]*30, [1]*30, None, None),
    ("B_EARLY_40", [100]*17+[1]*20, [1]*30, None, None),
    ("B_EARLY_46", [100]*20+[1]*20, [1]*30, None, None),
    ("C_BOTH_TIME_LIMITS", [100]*30, list(range(1,30)), None, None),
    ("D_STAGE2_CHEMICAL", [100]*30, [1]+[100]*30, None, None),
    ("E_PROCESSING_UNAVAILABLE", [100]*30, [1]*30, "analysis", None),
    ("E_ACME_UNAVAILABLE", [100]*30, [1]*30, "acme", None),
    ("E_INVALID_AREA", [100]*30, [1]*30, "nonfinite_area", None),
    ("E_COMPLETION_UNAVAILABLE", [100]*30, [1]*30, "completion", None),
    ("MISSING_SOLVENT", [100]*30, [1]*30, None, True),
])
def test_complete_timed_protocol(tmp_path,name,values1,values2,fault,missing):
    result = run_mock(tmp_path/name,name,values1,values2,fault=fault,missing=missing)
    assert result["status"] == "completed", result
    assert result["dose_count"] == 1
    assert result["dose_ledger"]["status"] == "CONFIRMED"
    assert result["dose_ledger"]["transition_basis"] == "time_limit_completed"
    assert result["dose_ledger"]["volume_ml"] == 1.86
    assert result["dose_ledger"]["rate_ml_min"] == 1.0
    clocks = result["clocks"]
    starts = {e["stage"]:e["virtual_seconds"] for e in clocks if e["event"] == "monitoring_stage_started"}
    decisions = {e["stage"]:e for e in clocks if e["event"] == "stage_transition_decision"}
    assert decisions["stage_1"]["classification"] == "time_limit_completed"
    assert decisions["stage_1"]["virtual_seconds"]-starts["stage_1"] == 48*3600
    dispatch = next(e for e in clocks if e["event"] == "dose_dispatch_intent")
    confirmed = next(e for e in clocks if e["event"] == "dose_confirmed")
    assert dispatch["virtual_seconds"] >= starts["stage_1"]+48*3600
    assert starts["stage_2"] >= confirmed["virtual_seconds"] > dispatch["virtual_seconds"]
    duration2 = (decisions["stage_2"]["virtual_seconds"]-starts["stage_2"])/3600
    if name == "D_STAGE2_CHEMICAL":
        assert 3.5 <= duration2 < 4
        assert result["chemical_completion_stages"] == ["stage_2"]
    else:
        assert duration2 == 6
        assert not result["chemical_completion_stages"]
    run = Path(result["run_dir"])
    report = json.loads((run/"final_qc/qc_summary.json").read_text())
    rows = report["observations"]
    assert len([r for r in rows if r["stage"] == "stage_1"]) == 24
    assert len([r for r in rows if r["stage"] == "stage_2"]) >= 8
    if fault:
        assert all(r["peak_area"] is None and not r["endpoint_eligible"] for r in rows)
        assert all((run/r["raw_path"]).is_file() for r in rows)
    info = json.loads((run/"metadata/experiment_info.json").read_text())
    assert info["stage_end_reasons"]["stage_1"] == "time_limit_completed"
    assert info["dose_confirmed"] and not info["chemical_completion_verified"]
    events = [json.loads(s) for s in Path(result["journal"]).read_text().splitlines()]
    authorization = next(e for e in events if e["event_type"] == "stage_transition_authorized")
    assert authorization["accepted_time_protocol"]
    if name.startswith("B_"):
        assert authorization["chemical_completion_verified"]
        advisory = [e for e in events if e["event_type"] == "stage_completion_evidence"
                    and e["workflow_phase"] == "stage_1" and e["evidence"]["complete"]]
        assert advisory
    assert not any(e["event_type"] == "stage_limit_decision" for e in events)
    assert any(e["event_type"] == "cycle_status" and e.get("status") == "CLEANUP_COMPLETE"
               and e["workflow_phase"] == "stage_2" for e in events)
    assert not any(e["event_type"] == "report_failed" for e in events)


@pytest.mark.parametrize("fault", ["acquisition","pump"])
def test_physical_or_transport_failure_not_hidden(tmp_path,fault):
    result = run_mock(tmp_path/fault,"FAIL_"+fault,[100]*30,[1]*30,fault=fault)
    assert result["status"] in {"instrument_failure","raised_PumpStateUncertainError"}
    assert result["dose_count"] == 0
    assert not any(e["event"] == "monitoring_stage_started" and e["stage"] == "stage_2" for e in result["clocks"])


@pytest.mark.parametrize("status", ["RESERVED","DISPATCH_INTENT","CONFIRMED"])
def test_durable_dose_guard_blocks_repeat_and_restart(tmp_path,status):
    guard = DoseGuard(tmp_path,"TEST_TIMED_DOSE",recipe(),"first")
    guard.reserve()
    if status != "RESERVED":
        guard.mark("DISPATCH_INTENT",transition_basis="time_limit_completed")
    if status == "CONFIRMED":
        guard.mark("CONFIRMED",pump_completion_sequence=99)
    before = guard.path.read_bytes()
    with pytest.raises(DoseReplayBlocked):
        DoseGuard(tmp_path,"TEST_TIMED_DOSE",recipe(),"restart").reserve()
    if status != "RESERVED":
        with pytest.raises(DoseReplayBlocked):
            guard.mark("DISPATCH_INTENT")
    assert guard.path.read_bytes() == before


@pytest.mark.parametrize("error", [OSError("storage failed"),RuntimeError("unexpected software failure"),
                                  si6.PhysicalStateUncertain("motion uncertain")])
def test_scientific_boundary_does_not_suppress_other_errors(error):
    from types import SimpleNamespace
    fake = SimpleNamespace(timed_protocol=lambda:True)
    def fail():
        raise error
    with pytest.raises(type(error)):
        si6.Services.scientific_call(fake,fail)


def test_wrapped_storage_error_not_treated_as_science():
    from types import SimpleNamespace
    def fail():
        try:
            raise OSError("disk failed")
        except OSError as exc:
            raise PeakEvidenceError("evidence read failed") from exc
    with pytest.raises(PeakEvidenceError):
        si6.Services.scientific_call(SimpleNamespace(timed_protocol=lambda:True),fail)


def test_scientific_failure_requires_verified_cleanup(tmp_path,monkeypatch):
    def unsafe(*args, **kwargs):
        raise si6.PhysicalStateUncertain("TEST cannot verify cleanup state")
    monkeypatch.setattr(si6.Services,"verify_cleanup_preconditions",unsafe)
    result = run_mock(tmp_path/"unsafe","UNSAFE_SCIENTIFIC_CLEANUP",[100]*30,[1]*30,fault="analysis")
    assert result["status"] == "raised_PhysicalStateUncertain"
    assert result["dose_count"] == 0


@pytest.mark.parametrize("fault", ["dose_uncertain", "dose_interrupted"])
def test_uncertain_or_interrupted_transition_never_starts_stage2(tmp_path,fault):
    raw = recipe()
    raw["workflow"]["initial_stage"]["max_hours"] = .2
    result = run_mock(tmp_path/fault,"BOUNDARY_"+fault,[100]*30,[1]*30,
                      fault=fault,workflow_recipe=raw)
    assert result["dose_count"] == 0
    assert result["dose_ledger"]["status"] == "DISPATCH_INTENT"
    assert result["status"].startswith("raised_")
    assert not any(e["event"] == "monitoring_stage_started" and e["stage"] == "stage_2" for e in result["clocks"])
    # Use the exact durable ledger path written by the controller.
    dose = json.loads((Path(result["run_dir"])/"channel_2_dose/dose_record.json").read_text())
    ledger = Path(dose["canonical_ledger_path"])
    assert json.loads(ledger.read_text())["status"] == "DISPATCH_INTENT"
    with pytest.raises(DoseReplayBlocked):
        DoseGuard(ledger.parent,result["dose_ledger"]["experiment_id"],raw,"restart").reserve()


@pytest.mark.parametrize("code,marker,expected", [
    (1,True,base.AnalysisInconclusiveError), (1,False,base.ProcessingExecutionError),
    (2,True,base.ProcessingExecutionError), (1,None,base.ProcessingExecutionError),
    (1,"corrupt",base.ProcessingExecutionError),
])
def test_subprocess_failure_requires_typed_scientific_evidence(tmp_path,code,marker,expected):
    from types import SimpleNamespace
    source = tmp_path/"acquisition.dx"
    source.write_text("TEST metadata carrier")
    paths = SimpleNamespace(processed_dir=tmp_path/"processed",layout=True)
    def runner(command, **kwargs):
        output = Path(command[command.index("--output-dir")+1])/command[command.index("--run-name")+1]
        if marker is not None:
            failure = output/"processing_failures/source_failure.json"
            failure.parent.mkdir(parents=True)
            failure.write_text("{partial" if marker == "corrupt" else json.dumps(dict(
                source_path=str(source),error="TEST numerical failure",recoverable_scientific_error=marker)))
        return SimpleNamespace(returncode=code)
    with pytest.raises(expected) as caught:
        base.run_process_fid_postprocessing(source,paths,"TEST",runner=runner)
    assert type(caught.value) is expected


def test_live_status_never_promises_early_transition(tmp_path):
    from types import SimpleNamespace
    from chemyx_lab.run_metadata import publish_observation
    stage = base.build_stages(recipe()["workflow"])[0]
    path = tmp_path/"status.json"
    publish_observation(SimpleNamespace(status_json=path),"TEST_STATUS",stage,
        SimpleNamespace(scheduled_measurement_number=5, actual_cycle_start_monotonic=8*3600,
                        scheduled_monotonic=8*3600),
        dict(plateau=True,peak_area=1.,solvent_present=True))
    status = json.loads(path.read_text())
    assert status["chemical_evidence_satisfied"]
    assert not status["endpoint_satisfied"]
    assert status["next_action"] == "continue monitoring to configured time endpoint"


@pytest.mark.parametrize("enabled", [False,True])
@pytest.mark.parametrize("error_type", [ValueError, ArithmeticError, si6.NmrProcessingError,
                                       PeakEvidenceError, base.AnalysisInconclusiveError])
def test_scientific_recovery_is_explicitly_opt_in(enabled,error_type):
    from types import SimpleNamespace
    def fail():
        raise error_type("TEST scientific failure")
    with pytest.raises(si6.ScientificAnalysisUnavailable if enabled else error_type):
        si6.Services.scientific_call(SimpleNamespace(timed_protocol=lambda:enabled),fail)


def test_actual_acme_nondetection_remains_unavailable_in_saved_evidence(tmp_path):
    from chemyx_lab.testing.false_completion_validation import synthetic_fid
    source = tmp_path/"noisy_source.dx"
    synthetic_fid(source,seed=6100,target_height=200.,noise_fft=100.)
    raw = recipe()
    raw["workflow"]["initial_stage"]["max_hours"] = .2
    raw["workflow"]["first_addition_stage"].update(min_completion_hours=.01,max_hours=.2)
    result = run_mock(tmp_path/"raw_acme","RAW_ACME_NONDETECTION",[100],[1],
        raw_sources={"stage_1":[source],"stage_2":[source]},workflow_recipe=raw)
    assert result["status"] == "completed"
    assert result["dose_count"] == 1
    root = Path(result["run_dir"])
    report = json.loads((root/"final_qc/qc_summary.json").read_text())
    assert len(report["observations"]) == 2
    for row in report["observations"]:
        assert row["phase_method"] == "acme"
        assert row["peak_area"] is None and not row["endpoint_eligible"]
        tracking = json.loads((root/row["tracking_evidence_path"]).read_text())
        assert tracking["peak_area"] is None
        assert not tracking["endpoint_eligible"]


def test_time_endpoint_cannot_be_blocked_by_boundary_analysis(tmp_path):
    raw = recipe()
    raw["workflow"]["initial_stage"]["max_hours"] = .2
    raw["workflow"]["first_addition_stage"].update(min_completion_hours=.01,max_hours=.2)
    result = run_mock(tmp_path/"boundary","NO_BOUNDARY_ANALYSIS",[100],[1],
                      fault="boundary_evaluator",workflow_recipe=raw)
    assert result["status"] == "completed"
    assert result["dose_count"] == 1
    assert any(e["event"] == "monitoring_stage_started" and e["stage"] == "stage_2" for e in result["clocks"])


def test_analysis_recovery_journal_records_completed_physical_cycles(tmp_path):
    from chemyx_lab.runtime_state import replay_journal
    raw = recipe()
    raw["workflow"]["initial_stage"]["max_hours"] = .2
    raw["workflow"]["first_addition_stage"].update(min_completion_hours=.01,max_hours=.2)
    result = run_mock(tmp_path/"journal","ANALYSIS_RECOVERY_JOURNAL",[100],[1],
                      fault="completion",workflow_recipe=raw)
    assert result["status"] == "completed" and result["dose_count"] == 1
    replay = replay_journal(Path(result["journal"]))
    assert not replay.errors
    assert replay.state.completed_cycle_count == 2
    assert replay.state.physical_state_certainty == "certain"
    events = [json.loads(s) for s in Path(result["journal"]).read_text().splitlines()]
    assert sum(e["event_type"] == "stage_completion_evidence" for e in events) == 2


def test_unavailable_last_result_clears_stale_advisory_metadata(tmp_path):
    raw = recipe()
    raw["workflow"]["initial_stage"].update(max_hours=1,interval_minutes=10)
    raw["workflow"]["first_addition_stage"].update(min_completion_hours=.01,max_hours=.2)
    result = run_mock(tmp_path/"stale","STALE_ADVISORY",[100]+[1]*10,[1],
                      fault="last_analysis",workflow_recipe=raw)
    assert result["status"] == "completed" and result["dose_count"] == 1
    root = Path(result["run_dir"])
    info = json.loads((root/"metadata/experiment_info.json").read_text())
    assert info["chemical_evidence_by_stage"]["stage_1"] is False
    rows = json.loads((root/"final_qc/qc_summary.json").read_text())["observations"]
    stage1 = [r for r in rows if r["stage"] == "stage_1"]
    assert len(stage1) == 6
    assert stage1[-2]["plateau"]
    assert not stage1[-1]["plateau"] and stage1[-1]["peak_area"] is None
