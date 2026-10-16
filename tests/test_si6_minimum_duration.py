"""Mandatory duration gates, independent stage clocks and protected transitions."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.analysis.stage_completion import completion_evidence
from chemyx_lab.testing.false_completion_validation import recipe as current_recipe, rows_for, run_mock as current_run_mock
from chemyx_lab.workflows import si6_automated_nmr as base
from chemyx_lab.workflows import three_instrument_si6 as si6


def recipe():
    """Freeze the previous minimum-duration policy to retain its compatibility tests."""
    raw = current_recipe()
    for key in ("initial_stage", "first_addition_stage"):
        raw["workflow"][key].pop("control_mode", None)
        raw["workflow"][key].pop("advance_at_duration", None)
    raw["workflow"]["initial_stage"]["min_completion_hours"] = 40.0
    return raw


def run_mock(*args, **kwargs):
    kwargs.setdefault("workflow_recipe", recipe())
    return current_run_mock(*args, **kwargs)


def stage(index):
    return base.build_stages(recipe()["workflow"])[index-1]


def observations(index, values, times):
    rows = rows_for(values, index)
    for row, elapsed in zip(rows, times):
        row["stage_elapsed_hours"] = elapsed
    return rows


@pytest.mark.parametrize("index,initial,qualifying,times", [
    (1, 100, 1, [40,42,44,46]), (2, 1, 100, [2,2.5,3,3.5]),
])
def test_four_new_observations_required_and_original_reference_retained(index, initial, qualifying, times):
    rules = stage(index).completion
    rows = observations(index, [initial] + [qualifying]*4, [0] + [t/10 for t in times])
    assert not completion_evidence(rows, rules)["complete"]
    for count, time in enumerate(times, 1):
        rows = observations(index, [initial]+[qualifying]*4+[qualifying]*count,
                            [0]+[t/10 for t in times]+times[:count])
        evidence = completion_evidence(rows, rules)
        assert evidence["initial_area"] == initial
        assert evidence["confirmations_passed"] == count
        assert evidence["complete"] is (count == 4)


@pytest.mark.parametrize("index", [1, 2])
@pytest.mark.parametrize("offset,eligible", [(-1,False),(0,True),(1,True)])
def test_minimum_boundary_and_no_pre_threshold_comparator(index, offset, eligible):
    s = stage(index)
    at = s.min_completion_hours
    boundary = np.nextafter(at, -np.inf if offset < 0 else np.inf) if offset else at
    values = [100,1,1,1,1] if index == 1 else [1,100,100,100,100]
    rows = observations(index, values, [0,at/4,at/2,at*.75,boundary])
    evidence = completion_evidence(rows, s.completion)
    assert evidence["confirmations_passed"] == int(eligible)
    assert not evidence["complete"]


def test_early_false_low_then_recovery_does_not_latch_completion():
    rows = observations(1,[100,0,0,0,0,50,50,50,50],[0,2,4,6,8,40,42,44,46])
    assert not completion_evidence(rows,stage(1).completion)["complete"]


@pytest.mark.parametrize("index", [1, 2])
def test_missing_monotonic_timing_fails_closed(index):
    values = [100,1,1,1,1] if index == 1 else [1,100,100,100,100]
    rows = rows_for(values,index)
    assert not completion_evidence(rows,stage(index).completion)["complete"]


def test_post_threshold_plateau_risk_is_preserved_and_reported():
    rows = observations(2,[1,30,100,45,45,45,45],[0,.5,1,2,2.5,3,3.5])
    assert completion_evidence(rows,stage(2).completion)["complete"]
    assert not completion_evidence(rows[:-1],stage(2).completion)["complete"]


@pytest.mark.parametrize("minimum,maximum", [(0,1),(.25,1.5),(40,48),(2,6)])
def test_yaml_durations_are_configurable(tmp_path, minimum, maximum):
    raw = recipe()
    for key in ("initial_stage","first_addition_stage"):
        raw["workflow"][key].update(min_completion_hours=minimum,max_hours=maximum)
    path = tmp_path/"recipe.yaml"
    path.write_text(yaml.safe_dump(raw))
    loaded = base.load_si6_config(path)
    for s in base.build_stages(loaded["workflow"]):
        assert (s.min_completion_hours,s.max_hours) == (minimum,maximum)
        assert not s.max_measurements_explicit


@pytest.mark.parametrize("key,value", [
    ("min_completion_hours",-1),("min_completion_hours",float("nan")),
    ("min_completion_hours",float("inf")),("min_completion_hours",True),
    ("min_completion_hours",None),("min_completion_hours",48),
    ("min_completion_hours",49),("max_hours",0),("max_hours",-1),
    ("max_hours",float("nan")),("max_hours",float("inf")),("max_hours",None),
])
def test_invalid_durations_rejected_before_transport(tmp_path, monkeypatch, key, value):
    raw = recipe()
    raw["workflow"]["initial_stage"][key] = value
    path=tmp_path/"invalid.yaml"
    path.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(si6,"SerialTransport",lambda *a,**k:pytest.fail("hardware opened"))
    monkeypatch.setattr(si6,"Pump",lambda *a,**k:pytest.fail("pump opened"))
    with pytest.raises(ValueError):
        si6.prepare(path,config.REPO_ROOT/"configs/machines/si6_real_COM4.yaml",
            config.REPO_ROOT/"arduino/configs/arduino_real_COM3.yaml",mock=False)


@pytest.mark.parametrize("key", ["min_completion_hours","max_hours"])
def test_missing_new_recipe_duration_is_rejected(tmp_path, key):
    raw=recipe();del raw["workflow"]["initial_stage"][key]
    path=tmp_path/"missing.yaml";path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):base.load_si6_config(path)


def test_legacy_count_limited_recipe_is_unchanged():
    raw=base.load_si6_config(config.REPO_ROOT/"config_templates/experiments/si6_two_stage_fast_sim.yaml")
    for s in base.build_stages(raw["workflow"]):
        assert s.min_completion_hours is None
        assert s.max_measurements_explicit
        assert s.max_measurements == s.completion["max_iterations"]


@pytest.mark.parametrize("index", [1,2])
@pytest.mark.parametrize("finish_offset", [-1.e-6,0.,1.e-6])
def test_scheduler_deadline_and_late_active_cycle(index, finish_offset):
    s=stage(index);clock=[0.];starts=[]
    def measure(schedule):
        starts.append(clock[0]);clock[0]+=s.interval_minutes*60+finish_offset
        return base.MeasurementObservation(True,False,"a","b","c")
    result=base.run_monitoring_stage(s,measure,monotonic_fn=lambda:clock[0],
        wall_now_fn=lambda:datetime(2026,1,1,tzinfo=timezone.utc),
        sleep_fn=lambda label,seconds:clock.__setitem__(0,clock[0]+seconds))
    assert result.outcome.stage_outcome is base.StageOutcome.RUNTIME_LIMIT_REACHED
    assert all(t < s.max_hours*3600 for t in starts)
    assert s.max_hours*3600 <= clock[0] <= s.max_hours*3600+s.interval_minutes*60


@pytest.mark.parametrize("case,allowed", [("early",False),("insufficient",False),("cached",False),("valid",True)])
def test_scheduler_rejects_cached_or_insufficient_plateau(case, allowed):
    s=stage(1);clock=[0.]
    def measure(schedule):
        acquisition=clock[0]
        post=acquisition >= 40*3600
        plateau=not post if case=="early" else True
        valid=not (case=="cached" and acquisition > 40*3600)
        return base.MeasurementObservation(valid,plateau,"a","b","c",acquisition,3 if case=="insufficient" else 4)
    result=base.run_monitoring_stage(s,measure,monotonic_fn=lambda:clock[0],
        wall_now_fn=lambda:datetime(2026,1,1,tzinfo=timezone.utc),
        sleep_fn=lambda label,seconds:clock.__setitem__(0,clock[0]+seconds))
    assert (result.outcome.stage_outcome is base.StageOutcome.PLATEAU_REACHED) is allowed


@pytest.mark.parametrize("name,values1,values2,missing,status,doses", [
    ("CHEMICAL",[100,1,1,1,1],[1,100,100,100,100],None,"completed",1),
    ("INCOMPLETE_S1",[100]*30,[1]*30,None,"maximum_duration_reached",0),
    ("INCOMPLETE_S2",[100,1,1,1,1],list(range(1,30)),None,"maximum_duration_reached",1),
    ("MISSING_SOLVENT",[100]*30,[1]*30,True,"completed",1),
    ("RECOVERY_AFTER_FALSE_LOW",[100,0,0,0,0]+[50]*30,[1]*30,None,"maximum_duration_reached",0),
])
def test_full_controller_mandatory_durations(tmp_path,name,values1,values2,missing,status,doses):
    result=run_mock(tmp_path/name,name,values1,values2,missing=missing)
    assert result["status"] == status
    assert result["dose_count"] == doses
    starts={r["stage"]:r["virtual_seconds"] for r in result["clocks"] if r["event"]=="monitoring_stage_started"}
    decisions={r["stage"]:r for r in result["clocks"] if r["event"]=="stage_transition_decision"}
    for key,decision in decisions.items():
        elapsed=(decision["virtual_seconds"]-starts[key])/3600
        if key in result["chemical_completion_stages"]:
            assert elapsed >= (46 if key=="stage_1" else 3.5)
        else:
            assert elapsed == (48 if key=="stage_1" else 6)
    info=json.loads((Path(result["run_dir"])/"metadata/experiment_info.json").read_text())
    assert info["stage_timing"]["stage_1"]["min_completion_hours"] == 40
    assert info["stage_timing"]["stage_2"]["max_hours"] == 6
    report=json.loads((Path(result["run_dir"])/"final_qc/qc_summary.json").read_text())
    rows=report["observations"]
    for key,minimum in [("stage_1",40),("stage_2",2)]:
        selected=[r for r in rows if r["stage"]==key]
        assert all(not r["plateau"] for r in selected if r["stage_elapsed_hours"]<minimum)
        if key in result["chemical_completion_stages"]:
            endpoint=report["stages"][0 if key=="stage_1" else 1]["endpoint"]
            assert endpoint["confirmations_passed"] == 4
            assert len([r for r in selected if r["stage_elapsed_hours"]>=minimum]) >= 4
    if name=="INCOMPLETE_S1":assert len(rows)==24


def test_actual_nmr_deadline_crossed_during_transfer_cleans_up_without_acquisition(tmp_path):
    raw=recipe()
    raw["workflow"]["initial_stage"].update(min_completion_hours=0,max_hours=.01)
    result=run_mock(tmp_path/"late","LATE_ACTUAL_ACQUISITION",[100]*30,[1]*30,
                    fault="acquisition",workflow_recipe=raw)
    assert result["status"] == "maximum_duration_reached"  # Not the injected acquisition error.
    assert result["observations"] == 0
    assert result["dose_count"] == 0
    events=[json.loads(line) for line in Path(result["journal"]).read_text().splitlines()]
    assert any(e["event_type"]=="acquisition_skipped_at_deadline" for e in events)
    assert any(e["event_type"]=="cycle_status" and e.get("status")=="CLEANUP_COMPLETE" for e in events)
    assert not any(e["event_type"]=="solvent_timed_fallback" for e in events)


@pytest.mark.parametrize("fault", ["analysis","pump"])
def test_minimum_does_not_override_safety_failure(tmp_path,fault):
    result=run_mock(tmp_path/fault,"SAFETY_"+fault,[100]*30,[1]*30,fault=fault)
    assert not result["chemical_completion_stages"]
    assert result["dose_count"] == 0
    assert result["status"] in {"instrument_failure","raised_PumpStateUncertainError"}


def test_deadline_skip_cannot_mask_unsafe_cleanup(tmp_path,monkeypatch):
    raw=recipe()
    raw["workflow"]["initial_stage"].update(min_completion_hours=0,max_hours=.01)
    def unsafe(*args,**kwargs):
        raise si6.PhysicalStateUncertain("TEST deadline cleanup state not proven")
    monkeypatch.setattr(si6.Services,"verify_cleanup_preconditions",unsafe)
    result=run_mock(tmp_path/"unsafe","UNSAFE_DEADLINE_CLEANUP",[100]*30,[1]*30,workflow_recipe=raw)
    assert result["status"] == "raised_PhysicalStateUncertain"
    assert result["dose_count"] == 0


@pytest.mark.parametrize("offset", [-1,0,1])
def test_actual_nmr_maximum_acquisition_boundary(tmp_path,offset):
    origin=100.;deadline=origin+6*3600
    now=np.nextafter(deadline,-np.inf if offset < 0 else np.inf) if offset else deadline
    class AcquisitionReached(RuntimeError):
        pass
    def acquire(*args,**kwargs):
        raise AcquisitionReached("TEST acquire callback reached, no instrument")
    fake=SimpleNamespace(raw=recipe(),
        simulation=SimpleNamespace(monotonic=lambda:now,now=lambda:datetime(2026,1,1,tzinfo=timezone.utc)),
        active_schedule=SimpleNamespace(stage_name="stage_2",stage_started_monotonic=origin),
        record=lambda *a,**k:None,paths=SimpleNamespace(for_stage=lambda stage:SimpleNamespace(raw_dir=tmp_path)),
        acquire=acquire,nmr_cfg=None)
    expected=AcquisitionReached if offset < 0 else si6.StageAcquisitionDeadlineReached
    with pytest.raises(expected):
        si6.Services.nmr_measurement(fake,stage="stage_2",cycle=1,rows=[],started=datetime.now(timezone.utc))


def test_pre_deadline_final_acquisition_may_complete_after_cleanup_overrun():
    s=stage(2);clock=[0.]
    def measure(schedule):
        acquisition=clock[0]
        final=acquisition >= 5.5*3600
        if final:clock[0]=6.1*3600
        return base.MeasurementObservation(True,final,"a","b","c",acquisition,4 if final else 0)
    result=base.run_monitoring_stage(s,measure,monotonic_fn=lambda:clock[0],
        wall_now_fn=lambda:datetime(2026,1,1,tzinfo=timezone.utc),
        sleep_fn=lambda label,seconds:clock.__setitem__(0,clock[0]+seconds))
    assert result.outcome.stage_outcome is base.StageOutcome.PLATEAU_REACHED
    assert clock[0] > 6*3600


def test_deadline_rechecked_after_local_acquisition_setup(tmp_path):
    clock=[6*3600-1.e-6]
    def paths(stage):
        clock[0]=6*3600
        return SimpleNamespace(raw_dir=tmp_path)
    fake=SimpleNamespace(raw=recipe(),
        simulation=SimpleNamespace(monotonic=lambda:clock[0],now=lambda:datetime(2026,1,1,tzinfo=timezone.utc)),
        active_schedule=SimpleNamespace(stage_name="stage_2",stage_started_monotonic=0.),
        record=lambda *a,**k:None,paths=SimpleNamespace(for_stage=paths),
        acquire=lambda *a,**k:pytest.fail("late NMR command"),nmr_cfg=None)
    with pytest.raises(si6.StageAcquisitionDeadlineReached):
        si6.Services.nmr_measurement(fake,stage="stage_2",cycle=1,rows=[],started=datetime.now(timezone.utc))


def test_live_csv_preserves_timing_evidence_before_final_reports(tmp_path):
    import csv
    path=tmp_path/"live_series.csv"
    fake=SimpleNamespace(paths=SimpleNamespace(time_series_csv=path,layout=None))
    row=dict(stage="stage_1",peak_area=1.,stage_elapsed_hours=40.,
             stage_elapsed_source="monotonic NMR acquisition start minus monotonic stage start",
             min_completion_hours=40.,max_hours=48.,completion_time_active=True,completion_confirmations=1)
    si6.Services.persist_rows(fake,[row])
    saved=next(csv.DictReader(path.open()))
    for key in ("stage_elapsed_hours","stage_elapsed_source","min_completion_hours","max_hours","completion_time_active","completion_confirmations"):
        assert saved[key]==str(row[key])
