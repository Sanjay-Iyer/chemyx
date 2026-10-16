"""Offline checkout validation. Test transfer values below are MOCK ONLY, never approved live settings."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.testing.false_completion_validation import run_mock, synthetic_fid, recipe
from chemyx_lab.workflows import si6_automated_nmr as base, three_instrument_si6 as si6
from chemyx_lab.workflows.si6_checkout import cycle_lower_bound_seconds
from chemyx_lab.workflows.si6_checkout import write_checkout_report
from chemyx_lab.workflows.dose_guard import DoseGuard, DoseReplayBlocked
from chemyx_lab.workflows.chemyx_position import PositionLedger, ChannelIdentity

ROOT = config.REPO_ROOT
CHECKOUT = ROOT/"configs/experiments/si6_real_10min_checkout.yaml"
REAL = ROOT/"configs/experiments/si6_real_two_stage.yaml"


def configured(tmp_path):
    raw = yaml.safe_load(CHECKOUT.read_text())
    raw["checkout"].update(base_recipe=str(REAL),test_fluid="MOCK ONLY fluid",routing="MOCK ONLY reservoir")
    raw["workflow"]["experiment_id"] = "SI6_CHECKOUT_MOCK_ONLY"
    dose = next(e for e in raw["workflow"]["initial_stage"]["after_monitoring"] if e["action"] == "infuse")
    dose.update(volume_ml=.1,rate_ml_min=1.)  # Synthetic test values, not live recommendations.
    raw["pump"]["channels"]["2"]["initial_retained_volume_ml"] = 1.
    path = tmp_path/"checkout.yaml"
    path.write_text(yaml.safe_dump(raw,sort_keys=False))
    return path,raw


def effective(tmp_path):
    path,_ = configured(tmp_path)
    return base.load_si6_config(path)


def test_shipping_recipe_blocks_unapproved_transfer_before_transports(monkeypatch):
    monkeypatch.setattr(si6,"SerialTransport",lambda *a,**k:pytest.fail("hardware contacted"))
    with pytest.raises(ValueError,match="unique workflow.experiment_id"):
        si6.prepare(CHECKOUT,ROOT/"configs/machines/si6_real_COM4.yaml",
                    ROOT/"arduino/configs/arduino_real_COM3.yaml",mock=False)


@pytest.mark.parametrize("field", ["volume_ml","rate_ml_min","loaded","identity"])
def test_required_operator_settings_cannot_inherit_production_dose(tmp_path,field):
    path,raw = configured(tmp_path)
    if field in {"volume_ml","rate_ml_min"}:
        next(e for e in raw["workflow"]["initial_stage"]["after_monitoring"] if e["action"]=="infuse")[field]=None
    elif field == "loaded":raw["pump"]["channels"]["2"]["initial_retained_volume_ml"]=None
    elif field == "identity":raw["workflow"]["experiment_id"] = recipe()["workflow"]["experiment_id"]
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):base.load_si6_config(path)


def test_overlay_reuses_geometry_cycle_nmr_and_analysis_without_editing_real(tmp_path):
    before = REAL.read_bytes()
    raw = effective(tmp_path)
    original = recipe()
    for key in ("nmr","analysis","solvent_monitor","qc_reporting"):
        assert raw[key] == original[key]
    assert raw["workflow"]["cycle"] == original["workflow"]["cycle"]
    for ch in ("1","2"):
        for key in ("syringe_diameter_mm","syringe_capacity_ml"):
            assert raw["pump"]["channels"][ch][key] == original["pump"]["channels"][ch][key]
    stages = base.build_stages(raw["workflow"])
    assert all(s.control_mode=="fixed_duration" and s.min_completion_hours is None and s.max_hours*3600==300 for s in stages)
    assert cycle_lower_bound_seconds(raw)==452
    assert REAL.read_bytes()==before


@pytest.mark.parametrize("index", [0,1])
def test_early_chemical_flags_cannot_shorten_either_5min_stage(tmp_path,index):
    stage=base.build_stages(effective(tmp_path)["workflow"])[index]
    clock=[0.];starts=[]
    def measure(schedule):
        starts.append(clock[0])
        return base.MeasurementObservation(True,True,"","","",clock[0],4)
    result=base.run_monitoring_stage(stage,measure,monotonic_fn=lambda:clock[0],
        wall_now_fn=lambda:datetime(2026,1,1,tzinfo=timezone.utc),
        sleep_fn=lambda label,seconds:clock.__setitem__(0,clock[0]+seconds))
    assert clock[0]==300 and starts==[0,60,120,180,240]
    assert result.outcome.stage_outcome is base.StageOutcome.TIME_LIMIT_COMPLETED


@pytest.mark.parametrize("name,fault,missing,acq", [
    ("COMPLETE",None,None,30), ("NO_TARGET","target_absent",None,30),
    ("NO_SOLVENT",None,True,30), ("ANALYSIS_UNAVAILABLE","analysis",None,30),
    ("LONG_ACQUISITION",None,None,200),
])
def test_full_checkout_same_controller_and_cleanup(tmp_path,name,fault,missing,acq):
    raw=effective(tmp_path)
    if name == "COMPLETE":
        raw["checkout"].update(test_fluid="unspecified",routing="unspecified")
    result=run_mock(tmp_path/name,name,[100],[1],workflow_recipe=raw,fault=fault,missing=missing,acquisition_seconds=acq)
    assert result["status"]=="completed" and result["dose_count"]==1
    assert result["dose_ledger"]["volume_ml"]==.1 and result["dose_ledger"]["rate_ml_min"]==1
    clocks=result["clocks"]
    starts={e["stage"]:e["virtual_seconds"] for e in clocks if e["event"]=="monitoring_stage_started"}
    decisions={e["stage"]:e for e in clocks if e["event"]=="stage_transition_decision"}
    expected=452+acq
    for stage in ("stage_1","stage_2"):
        assert decisions[stage]["virtual_seconds"]-starts[stage]==expected
        assert decisions[stage]["classification"]=="time_limit_completed"
    confirmed=next(e for e in clocks if e["event"]=="dose_confirmed")
    assert starts["stage_2"]==confirmed["virtual_seconds"]==expected+8
    run=Path(result["run_dir"])
    report=json.loads((run/"final_qc/checkout_report.json").read_text())
    assert report["result"]=="mock_validation_passed",report
    assert not report["physical_instruments_verified"]
    assert report["evidence"]["channel1_completed_transfers"]==10
    assert len(report["evidence"]["raw_nmr_files"])==2
    assert all(len(e["raw_nmr_files"])==1 and e["analysis_completed_or_documented_unavailable"]==1
               for e in report["evidence"]["by_stage"].values())
    events=[json.loads(s) for s in Path(result["journal"]).read_text().splitlines()]
    contexts=[e for e in events if e["event_type"]=="pump_needle_context" and e["channel"]==1]
    assert [e["operation_type"] for e in contexts]==["withdraw","withdraw","infuse","withdraw","infuse"]*2
    assert sum(e["event_type"]=="cycle_status" and e.get("status")=="CLEANUP_COMPLETE" for e in events)==2
    if name=="NO_SOLVENT":
        assert sum(e["event_type"]=="analysis_result" and e.get("result_classification")=="completed" for e in events)==2
    assert not any(e["event_type"]=="stage_limit_decision" for e in events)


@pytest.mark.parametrize("solvent", [True, False])
def test_actual_acme_with_weak_target_or_missing_solvent_is_retained(tmp_path,solvent):
    raw=effective(tmp_path)
    source=tmp_path/"no_target.dx"
    synthetic_fid(source,seed=6100,target_height=200,noise_fft=100,solvent=solvent)
    result=run_mock(tmp_path/"raw","RAW_ACME",[100],[1],workflow_recipe=raw,
        raw_sources={"stage_1":[source],"stage_2":[source]})
    assert result["status"]=="completed" and result["dose_count"]==1
    rows=json.loads((Path(result["run_dir"])/"final_qc/qc_summary.json").read_text())["observations"]
    assert len(rows)==2 and all(r["phase_method"]=="acme" and r["peak_area"] is None for r in rows)


def test_shared_physical_home_reference_is_updated_without_reset(tmp_path,monkeypatch):
    raw=effective(tmp_path);root=tmp_path/"physical_position"
    monkeypatch.setattr(si6,"state_root",lambda mock:root)
    with PositionLedger(root,owner="mock setup").acquire() as ledger:
        for ch in (1,2):
            settings=raw["pump"]["channels"][str(ch)]
            ledger.set_home(ChannelIdentity(ch,"mock",settings["syringe_diameter_mm"],settings["syringe_capacity_ml"],"MOCK operator HOME",0 if ch==1 else 1),receipt_id=f"HOME{ch}",established_by="mock operator")
        homes={ch:deepcopy(ledger.state(ch)["home"]) for ch in (1,2)}
    result=run_mock(tmp_path/"position","POSITION",[100],[1],workflow_recipe=raw)
    assert result["status"]=="completed"
    with PositionLedger(root,owner="mock inspect").acquire() as ledger:
        for ch in (1,2):
            state=ledger.state(ch)
            assert state["home"]==homes[ch] and state["reference_valid"]
            # Existing ledger convention: infusion decreases displacement.
            assert state["current_displacement_ml"]==pytest.approx(0 if ch==1 else -.1)


def test_sampling_overrun_still_attempts_both_first_nmr_cycles(tmp_path,monkeypatch):
    original = si6.Services.nmr_measurement
    def delayed_preparation(self,*args,**kwargs):
        self.sleep("MOCK ONLY slow sample preparation",300)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(si6.Services,"nmr_measurement",delayed_preparation)
    result=run_mock(tmp_path/"late_sampling","LATE_SAMPLING",[100],[1],workflow_recipe=effective(tmp_path))
    assert result["status"]=="completed" and result["dose_count"]==1
    events=[json.loads(s) for s in Path(result["journal"]).read_text().splitlines()]
    assert not any(e["event_type"]=="acquisition_skipped_at_deadline" for e in events)
    assert sum(e["event_type"]=="cycle_status" and e.get("status")=="CLEANUP_COMPLETE" for e in events)==2
    assert {e["workflow_phase"] for e in events if e["event_type"]=="nmr_retrieved"}=={"stage_1","stage_2"}
    report=json.loads((Path(result["run_dir"])/"final_qc/checkout_report.json").read_text())
    assert report["result"]=="mock_validation_passed"


@pytest.mark.parametrize("descriptions", [{}, {"test_fluid":None,"routing":None,"purpose":None},
                                         {"test_fluid":42,"routing":[],"purpose":False}])
def test_descriptions_are_optional_and_never_authorize_routing(tmp_path,descriptions):
    path,raw=configured(tmp_path)
    for key in ("test_fluid","routing","purpose"):
        raw["checkout"].pop(key,None)
    raw["checkout"].update(descriptions)
    path.write_text(yaml.safe_dump(raw))
    resolved=base.load_si6_config(path)
    assert resolved["checkout"]["test_fluid"]==resolved["checkout"]["routing"]=="unspecified"
    assert resolved["checkout"]["purpose"]=="physical_instrument_checkout"


def test_overall_timeout_preserves_cleanup_and_refuses_dose(tmp_path):
    raw=effective(tmp_path);raw["checkout"]["overall_timeout_minutes"]=1
    result=run_mock(tmp_path/"timeout","TIMEOUT",[100],[1],workflow_recipe=raw)
    assert result["status"]=="instrument_failure" and result["dose_count"]==0
    report=json.loads((Path(result["run_dir"])/"final_qc/checkout_report.json").read_text())
    assert report["result"]=="checkout_evidence_incomplete"
    assert report["evidence"]["cleanup_completed_cycles"]==1


def test_no_raw_file_is_incomplete_even_when_time_endpoint_is_reached(tmp_path,monkeypatch):
    from chemyx_lab.workflows.si6_simulation import TrendSimulation
    original=TrendSimulation.acquire
    def empty(self,*args,**kwargs):
        path=original(self,*args,**kwargs);path.write_bytes(b"");return path
    monkeypatch.setattr(TrendSimulation,"acquire",empty)
    result=run_mock(tmp_path/"empty","EMPTY",[100],[1],workflow_recipe=effective(tmp_path))
    assert result["status"]=="instrument_failure" and result["dose_count"]==0
    report=json.loads((Path(result["run_dir"])/"final_qc/checkout_report.json").read_text())
    assert report["result"]=="checkout_evidence_incomplete"


@pytest.mark.parametrize("fault", ["dose_uncertain","dose_interrupted"])
def test_checkout_uncertain_dose_blocks_stage2_and_restart(tmp_path,fault):
    raw=effective(tmp_path)
    result=run_mock(tmp_path/fault,fault.upper(),[100],[1],workflow_recipe=raw,fault=fault)
    assert result["status"].startswith("raised_") and result["dose_count"]==0
    assert result["dose_ledger"]["status"]=="DISPATCH_INTENT"
    assert not any(e["event"]=="monitoring_stage_started" and e["stage"]=="stage_2" for e in result["clocks"])
    dose=json.loads((Path(result["run_dir"])/"channel_2_dose/dose_record.json").read_text())
    canonical=Path(dose["canonical_ledger_path"])
    before=canonical.read_bytes()
    with pytest.raises(DoseReplayBlocked):
        DoseGuard(canonical.parent,result["dose_ledger"]["experiment_id"],raw,"restart").reserve()
    assert canonical.read_bytes()==before


@pytest.mark.parametrize("mock", [False,True])
def test_prepare_selects_simulation_only_for_explicit_mock(tmp_path,monkeypatch,mock):
    path,_=configured(tmp_path)
    monkeypatch.setattr(si6,"SerialTransport",lambda *a,**k:pytest.fail("hardware contacted"))
    raw,*_=si6.prepare(path,ROOT/"configs/machines/si6_real_COM4.yaml",
                      ROOT/"arduino/configs/arduino_real_COM3.yaml",mock=mock)
    assert ("simulation" in raw) is mock


@pytest.mark.parametrize("answer", ["y","n"])
def test_checkout_summary_precedes_single_prompt(tmp_path,monkeypatch,capsys,answer):
    path,_=configured(tmp_path)
    script=ROOT/"scripts/02_si6_experiment.py"
    monkeypatch.syspath_prepend(str(script.parent))
    spec=importlib.util.spec_from_file_location("checkout_entry_test",script)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prompts=[];opened=[]
    def confirm(prompt):
        summary=capsys.readouterr().out
        for expected in ("CHECKOUT MODE", "COM4", "COM3", "MOCK ONLY fluid",
                         "Channel 1 withdraw", "ONE protected Channel 2", "0.1 mL",
                         "SI6_CHECKOUT_MOCK_ONLY", "No overlapping cycles"):
            assert expected in summary
        prompts.append(prompt)
        return answer
    monkeypatch.setattr("builtins.input",confirm)
    @contextmanager
    def session(*args,**kwargs):
        opened.append(True)
        yield SimpleNamespace(paths=SimpleNamespace(run_dir=tmp_path))
    monkeypatch.setattr(si6,"open_services",session)
    monkeypatch.setattr(si6,"run_experiment",lambda *a,**k:base.RunOutcome(base.TerminalStatus.COMPLETED,"MOCK boundary test"))
    monkeypatch.setattr(module,"summarize_run",lambda path:path)
    from chemyx_lab.analysis import phase_comparison
    monkeypatch.setattr(phase_comparison,"compare_run",lambda path:path)
    status=module.main(["--live","--workflow-config",str(path),"--machine-config",
        str(ROOT/"configs/machines/si6_real_COM4.yaml"),"--arduino-config",
        str(ROOT/"arduino/configs/arduino_real_COM3.yaml")])
    assert len(prompts)==1 and len(opened)==(1 if answer=="y" else 0)
    assert status==(0 if answer=="y" else 1)


def test_checkout_and_production_dose_identities_never_overwrite(tmp_path,monkeypatch):
    shared=tmp_path/"shared_dose_ledger"
    monkeypatch.setattr(si6,"DoseGuard",lambda root,experiment_id,raw,run_id:DoseGuard(shared,experiment_id,raw,run_id))
    prod=recipe();prod["workflow"]["initial_stage"]["max_hours"]=.2
    prod["workflow"]["first_addition_stage"].update(min_completion_hours=.01,max_hours=.2)
    production=run_mock(tmp_path/"production","PRODUCTION",[100],[1],workflow_recipe=prod)
    production_file=shared/(production["dose_ledger"]["experiment_id"]+".json")
    before=production_file.read_bytes()
    checkout=run_mock(tmp_path/"checkout","IDENTITY",[100],[1],workflow_recipe=effective(tmp_path))
    assert production["dose_count"]==checkout["dose_count"]==1
    assert production_file.read_bytes()==before
    assert production["dose_ledger"]["experiment_id"]!=checkout["dose_ledger"]["experiment_id"]
    for result in (production,checkout):
        with pytest.raises(DoseReplayBlocked):
            DoseGuard(shared,result["dose_ledger"]["experiment_id"],{},"restart").reserve()


def test_checkout_report_requires_acquisition_evidence_from_each_stage(tmp_path):
    """Two Stage 1 spectra must not conceal a missing Stage 2 acquisition."""
    for name in ("one.dx","two.dx"):
        (tmp_path/name).write_text("MOCK ONLY file")
    events=[dict(event_type="stage_transition_decision",workflow_phase=stage,
                 result_classification="time_limit_completed") for stage in ("stage_1","stage_2")]
    events += [dict(event_type="nmr_retrieved",workflow_phase="stage_1",result_path=name)
               for name in ("one.dx","two.dx")]
    events += [dict(event_type="analysis_result",workflow_phase="stage_1",result_classification="completed")]*2
    events += [dict(event_type="dose_confirmed")]
    events += [dict(event_type="needle_transition",result_classification="completed")]*4
    for stage in ("stage_1","stage_2"):
        events += [dict(event_type="cycle_status",workflow_phase=stage,status="CLEANUP_COMPLETE")]
        events += [dict(event_type="pump_move",workflow_phase=stage,channel=1,operation_type="infuse",
                        lifecycle_state="completed",result_classification="positively_confirmed")]*5
    output=tmp_path/"final_qc";output.mkdir()
    metadata=dict(status="completed",mode="MOCK",experiment_id="SI6_CHECKOUT_REPORT_ONLY",
                  stage_names=["stage_1","stage_2"],checkout={})
    write_checkout_report(SimpleNamespace(run_dir=tmp_path,layout=SimpleNamespace(final_qc=output)),metadata,events)
    report=json.loads((output/"checkout_report.json").read_text())
    assert report["result"]=="checkout_evidence_incomplete"
    assert not report["evidence"]["by_stage"]["stage_2"]["raw_nmr_files"]
