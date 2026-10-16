"""Adversarial HOME validation. Test-local mocks/state; no live entry points.

This is an evidence generator, not an alternative completion implementation.
Every decision is delegated to production completion/controller functions.
"""
from __future__ import annotations
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
from unittest.mock import patch

import numpy as np

from .. import config
from ..analysis.phase_audit import write_json, write_rows
from ..analysis.stage_completion import completion_evidence
from ..analysis.solvent_presence import record_sample_presence
from ..workflows import three_instrument_si6 as si6
from ..workflows import si6_automated_nmr as base
from ..workflows.si6_simulation import TrendSimulation

RECIPE = config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml"


def prohibited(*a, **k):
    raise AssertionError("Physical instrument/network/input access forbidden in HOME campaign")


def recipe():
    return base.load_si6_config(RECIPE)


def guard(stack):
    import serial
    stack.enter_context(patch.object(serial, "Serial", prohibited))
    stack.enter_context(patch.object(socket, "create_connection", prohibited))
    stack.enter_context(patch.object(socket.socket, "connect", prohibited))
    stack.enter_context(patch("builtins.input", prohibited))


def rows_for(values, stage, *, invalid=(), timing=None, duplicate=False):
    interval = recipe()["workflow"]["initial_stage" if stage == 1 else "first_addition_stage"]["interval_minutes"]
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i, area in enumerate(values):
        stamp = (origin + timedelta(minutes=i*interval)).isoformat()
        row = dict(peak_area=area, solvent_present=True, stage_timed_fallback=False,
            endpoint_eligible=True, peak_clear=True, measurement_valid=True, qc_pass=True,
            area_uncertainty=.01, snr=100., acquired_at=stamp, timestamp_source="LONG DATE header",
            raw_sha256=f"unique-{i}", iteration=i+1)
        if i in invalid:
            row.update(endpoint_eligible=False, measurement_valid=False, qc_pass=False, error="TEST known invalid evidence")
        if timing == "missing":
            row.update(acquired_at=None, timestamp_source="unavailable")
        elif timing == "duplicate":
            row["acquired_at"] = origin.isoformat()
        if duplicate:
            row["raw_sha256"] = "repeated-acquisition"
        rows.append(row)
    return rows


def observation_campaign(output, *, histories=2000, seed=20261016):
    output.mkdir(parents=True, exist_ok=False)
    stages = base.build_stages(recipe()["workflow"])
    cases = [
        (1, "normal_completion", [100,80,55,30,12,4,1,1,1,1], (), 10),
        (1, "still_progressing", [100,85,70,60,50,40,30,20], (), None),
        (1, "isolated_false_low", [100,80,60,1,45,35,25], (), None),
        (1, "three_lows_then_recovery", [100,70,2,1,1,45,30], (), None),
        (1, "high_flat", [100,75,65,65,65,65], (), None),
        (1, "four_invalid_lows", [100,1,1,1,1], (1,2,3,4), None),
        (1, "invalid_initial_spike", [1000,20,20,20,20], (0,), None),
        (1, "zero_initial_reference", [0,0,0,0], (), None),
        (2, "normal_given_sequence", [1,15,40,65,85,97,100,100,100,100], (), 10),
        (2, "valid_early_plateau", [1,30,100,100,100,100], (), 6),
        (2, "still_growing", [1,10,20,35,50,70,90], (), None),
        (2, "temporary_three_points", [1,20,40,40,40,65,85,100], (), None),
        (2, "no_reaction", [1,1,1,1,1,1], (), None),
        (2, "abnormal_spike", [1,15,30,200,45,60,75], (), None),
        (2, "lower_secondary_plateau", [1,30,65,100,45,45,45,45], (), None),
        (2, "four_invalid_plateau", [1,10,10,10,10], (1,2,3,4), None),
        (2, "temporary_four_points_then_growth", [1,20,40,40,40,40,65,85,100], (), None),
    ]
    results, traces = [], []
    for stage, name, values, invalid, expected in cases:
        rows = rows_for(values, stage, invalid=invalid)
        evidence = [completion_evidence(rows[:i], stages[stage-1].completion) for i in range(1,len(rows)+1)]
        actual = next((i for i,e in enumerate(evidence,1) if e["complete"]), None)
        policy_risk = name in {"zero_initial_reference", "lower_secondary_plateau", "temporary_four_points_then_growth"}
        results.append(dict(scenario_id=name, stage=f"stage_{stage}", input_source="explicit area history",
            validity="known invalid rows" if invalid else "policy-risk oracle" if policy_risk else "valid synthetic observations",
            expected_endpoint=expected, actual_endpoint=actual,
            completion_time_hours=(actual-1)*stages[stage-1].interval_minutes/60 if actual else None,
            chemical_completion_verified=actual is not None, timed_fallback=False,
            channel2_authorization=(actual is not None) if stage==1 else "not a Stage 1 decision",
            passed=actual==expected, details="current-rule sensitivity; not a second endpoint model"))
        for i,e in enumerate(evidence,1):
            traces.append(dict(scenario=name, stage=stage, observation=i, input_area=values[i-1], **e))
    # Perturb actual configured thresholds; report exact floating-point behavior.
    boundary = []
    for stage, kind, values in (
        (1,"low",[100]+[2.5]*4), (2,"growth",[1]+[1.25]*4),
        (2,"relative_change",[1,10,10.2,10.2,10.2])):
        for direction in (-1,0,1):
            v = list(values)
            value = v[1] if kind != "relative_change" else v[2]
            value = np.nextafter(value, -np.inf if direction<0 else np.inf) if direction else value
            if kind=="relative_change": v[2:] = [value]*3
            else: v[1:] = [value]*4
            e = completion_evidence(rows_for(v,stage), stages[stage-1].completion)
            boundary.append(dict(stage=stage, threshold=kind, perturbation=direction, value=value, evidence=e))
    rng = np.random.default_rng(seed)
    random_rows = []
    for index in range(histories):
        stage = index%2+1; kind = (index//2)%6
        values = ([100]+rng.uniform(.1,2.,8).tolist()) if stage==1 else ([1]+rng.uniform(9.95,10.05,8).tolist())
        invalid = tuple(range(1,len(values))) if kind==0 else ()
        history = rows_for(values,stage,invalid=invalid)
        if kind==1:
            for row in history[1:]: row.update(solvent_present=False,peak_area=None,endpoint_eligible=False)
        elif kind==2:
            for row in history: row.update(stage_timed_fallback=True,endpoint_eligible=False)
        elif kind==3:
            for row in history: row.update(raw_sha256="duplicate",acquired_at=history[0]["acquired_at"])
        elif kind==4:
            for row in history: row.update(measurement_valid=False,qc_pass=False,high_noise_warning=True,area_uncertainty=1.e9)
        actual = completion_evidence(history,stages[stage-1].completion)
        insufficient = kind in {0,1,2,3,4}
        random_rows.append(dict(seed=seed,history=index,stage=stage,kind=kind,
            explicit_invalid=insufficient,complete=actual["complete"],
            invariant_pass=not(insufficient and actual["complete"]), evidence=actual))
    write_json(output/"randomized_histories.json",random_rows)
    write_json(output/"boundary_tests.json",boundary)
    write_json(output/"observation_traces.json",traces)
    for stage in (1,2): write_rows(output/f"stage{stage}_scenarios.csv",[r for r in results if r["stage"]==f"stage_{stage}"])
    result=dict(scenarios=results,random_seed=seed,histories=histories,
        invalid_evidence_completions=sum(not r["invariant_pass"] for r in random_rows),
        random_results_by_kind={str(k):sum(not r["invariant_pass"] for r in random_rows if r["kind"]==k) for k in range(6)})
    write_json(output/"results.json",result)
    return result


def run_mock(output, name, values1, values2, *, timing=None, missing=None, fault=None, flags=False, acquisition_seconds=30, raw_sources=None, corrupt_at=(), workflow_recipe=None):
    """Real orchestrator and nominal settings with a virtual controller.

    Observations are injected unless raw_sources supplies complex FIDs for the
    production FFT/ACME/baseline/integration path. Endpoint code is never mocked.
    """
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    raw=deepcopy(workflow_recipe) if workflow_recipe is not None else recipe()
    raw["output"]["run_root_dir"]=str(output/"experiments")
    raw["workflow"]["experiment_id"]=f"TEST_{name}"
    fixture=output/"observations.json"
    write_json(fixture,{"simulation_only":True,"stages":{
        "stage_1":list(values1)+[values1[-1]]*100,"stage_2":list(values2)+[values2[-1]]*100}})
    raw["simulation"]={"fixture_file":str(fixture),"acquisition_seconds":acquisition_seconds}
    clocks=[];all_rows=[]
    def no_figure(fig,*a,**k):
        import matplotlib.pyplot as plt
        plt.close(fig)
        return "TEST reporting rendering omitted; control and JSON unchanged"
    with ExitStack() as stack, (output/"controller.log").open("w",encoding="utf-8") as log, redirect_stdout(log):
        guard(stack)
        stack.enter_context(patch.dict(os.environ, {"CHEMYX_POSITION_STATE_DIR": str(output / "mock_chemyx_state")}))
        stack.enter_context(patch("chemyx_lab.analysis.si6_stage_reports.save_figure",no_figure))
        stack.enter_context(patch("chemyx_lab.analysis.si6_final_qc.save_figure",no_figure))
        settings=list(si6.prepare(RECIPE,config.REPO_ROOT/"configs/machines/si6_real_COM4.yaml",
            config.REPO_ROOT/"arduino/configs/arduino.example.yaml",mock=True));settings[0]=raw
        with si6.open_services(*settings,identity=si6.RunIdentity("si6",True)) as s:
            sim=TrendSimulation({"fixture_file":str(fixture),"acquisition_seconds":acquisition_seconds},si6.MOCK_NMR_FIXTURE)
            s.simulation=sim;s.acquire=sim.acquire;s.process=sim.process;s.analyze=sim.analyze;s.sleep=sim.sleep
            original_acquire=s.acquire;original_analyze=s.analyze;original_record=s.recorder.record
            def acquire(*a,**k):
                path=original_acquire(*a,**k)
                index=sum(sim.indices.values())
                if raw_sources:
                    stage=path.stem.rsplit("_",1)[0]
                    sources=raw_sources.get(stage,raw_sources["stage_1"])
                    source=sources[min(sim.indices[stage]-1,len(sources)-1)]
                    text=Path(source).read_text(encoding="latin-1")
                    text=re.sub(r"(?m)^##LONG DATE=.*$","##LONG DATE="+sim.now().strftime("%Y/%m/%d %H:%M:%S%z"),text)
                    path.write_text(text,encoding="latin-1")
                stamp="2026/01/01 00:00:00+0000" if timing in {"duplicate_time","duplicate_raw"} else "" if timing=="missing_time" else "2025/12/31 23:00:00+0000" if timing=="backwards_time" and index>1 else None
                if stamp is not None:
                    text=re.sub(r"(?m)^##LONG DATE=.*$","##LONG DATE="+stamp,path.read_text(encoding="latin-1"))
                    path.write_text(text,encoding="latin-1")
                if index in corrupt_at:
                    path.write_bytes(b"TEST interrupted JCAMP acquisition")
                return path
            s.acquire=acquire
            def analyze(*a,**k):
                if raw_sources:
                    row,_=measure_source(a[0],Path(a[1])/"actual_acme",a[3],"acme",all_rows)
                    row.update(a[4]);spectrum=[]
                else:
                    row,spectrum=original_analyze(*a,**k)
                row["raw_sha256"]=("TEST repeated raw hash" if timing=="duplicate_raw" else hashlib.sha256(a[0].read_bytes()).hexdigest())
                if flags:row.update(measurement_valid=False,qc_pass=False,high_noise_warning=True,area_uncertainty=1.e9)
                all_rows.append(row)
                return row,spectrum
            s.analyze=analyze
            def recorded(event,**fields):
                r=original_record(event,**fields)
                if event in {"monitoring_stage_started","stage_transition_decision","dose_dispatch_intent","dose_confirmed","terminal"}:
                    clocks.append(dict(event=event,stage=fields.get("workflow_phase"),virtual_seconds=sim.seconds,
                                       virtual_time=sim.now().isoformat(),classification=fields.get("result_classification")))
                return r
            s.recorder.record=recorded
            if missing is not None:
                def presence(path,settings):
                    stage=path.stem.rsplit("_",1)[0];index=sim.indices[stage]-1
                    value=missing[index%len(missing)] if isinstance(missing,list) else False
                    return dict(present=value,reason="TEST injected independent solvent evidence",windows=[])
                stack.enter_context(patch.object(si6,"solvent_presence",presence))
            if fault=="analysis":s.process=lambda *a: (_ for _ in ()).throw(base.AnalysisInconclusiveError("TEST recoverable processing failure"))
            if fault=="acquisition":s.acquire=lambda *a,**k: (_ for _ in ()).throw(RuntimeError("TEST acquisition transport failure"))
            if fault=="pump":s.pump_move=lambda *a,**k: (_ for _ in ()).throw(base.PumpStateUncertainError("TEST pump state unknown"))
            try:
                outcome=si6.run_experiment(s)
                status=outcome.status.value;reason=outcome.message
            except BaseException as exc:
                status="raised_"+type(exc).__name__;reason=str(exc)
            run=s.paths.run_dir;journal=s.paths.journal_jsonl
            ledger=deepcopy(s.dose_guard.data) if s.dose_guard else None
        assert run.is_relative_to(output)
    events=[json.loads(line) for line in journal.read_text().splitlines()]
    result=dict(scenario=name,status=status,reason=reason,run_dir=str(run),journal=str(journal),
        clocks=clocks,dose_count=sum(e["event_type"]=="dose_confirmed" for e in events),
        dose_ledger=ledger,chemical_completion_stages=[e["workflow_phase"] for e in events if e["event_type"]=="stage_transition_decision" and e.get("result_classification")=="plateau_reached"],
        timed_fallback_stages=[e["workflow_phase"] for e in events if e["event_type"]=="stage_transition_decision" and e.get("result_classification")=="timed_fallback_completed"],
        observations=len(all_rows),test_input_kind=("synthetic complex FIDs; production FFT/ACME/baseline/target integration; real orchestrator/mock instruments/virtual clock" if raw_sources else "injected controller observations; real orchestrator/mock instruments/virtual clock"),
        hardware_contacted=False)
    write_json(output/"result.json",result)
    write_json(output/"observations_measured.json",all_rows)
    return result


def workflow_campaign(output):
    output.mkdir(parents=True,exist_ok=False)
    valid1=[100,80,55,30,12,4,1,1,1,1]
    valid2=[1,30,100,100,100,100]
    specs=[("A_both_incomplete",[100]*40,[1]*40,{}),
        ("A2_stage2_incomplete",valid1,[1]*40,{}),
        ("B_solvent_missing",[100]*40,[1]*40,{"missing":True}),
        ("C_invalid_qc_lows",[100,1,1,1,1],valid2,{"flags":True}),
        ("C_analysis_failure",valid1,valid2,{"fault":"analysis"}),
        ("C_transport_failure",valid1,valid2,{"fault":"acquisition"}),
        ("C_pump_unknown",valid1,valid2,{"fault":"pump"}),
        ("normal_valid_completion",valid1,valid2,{})]
    for timing in ("missing_time","duplicate_time","backwards_time","duplicate_raw"):
        specs.append(("D_"+timing,[100,1,1,1,1]+[20]*30,valid2,{"timing":timing}))
    results=[]
    for name,v1,v2,kw in specs:
        results.append(run_mock(output/name,name,v1,v2,**kw))
        print("WORKFLOW",name,results[-1]["status"],"dose",results[-1]["dose_count"],flush=True)
    write_json(output/"full_mock_experiment.json",results)
    timings=[]
    for result in results:
        for stage in ("stage_1","stage_2"):
            start=next((r for r in result["clocks"] if r["event"]=="monitoring_stage_started" and r["stage"]==stage),None)
            end=next((r for r in result["clocks"] if r["event"]=="stage_transition_decision" and r["stage"]==stage),None)
            timings.append(dict(scenario=result["scenario"],stage=stage,configured_hours=48 if stage=="stage_1" else 4,
                elapsed_hours=(end["virtual_seconds"]-start["virtual_seconds"])/3600 if start and end else None,
                completion_reason=end["classification"] if end else "not entered/failed before decision",
                chemical_completion=stage in result["chemical_completion_stages"],timed_fallback=stage in result["timed_fallback_stages"],dose_count=result["dose_count"]))
    write_rows(output/"fallback_timing.csv",timings)
    return results


def synthetic_fid(path, *, seed=4100, target_height=1000., center=5.8, width=1.9,
                  noise_fft=2., solvent=True, neighbor=None, baseline=False,
                  phase=(0.,0.), stored=None, bad=None):
    """Independent causal damped resonances -> complex FID, then JCAMP encoding."""
    from ..analysis.synthetic_phase_benchmark import write_jcamp
    n,sw,sf=8192,1250.,60.5508184
    settings=dict(acquired_points=n,processed_points=65536,spectral_width_hz=sw,
                  observe_frequency_mhz=sf,center_ppm=5.,line_broadening_hz=.03)
    t=np.arange(n)/sw;z=np.zeros(n,complex)
    lines=[(center,target_height,width)]
    if solvent:lines += [(2.08,80676.,4.5),(6.99,70000.,4.5)]
    if neighbor:lines.append(neighbor)
    for ppm,height,w in lines:
        z += height*np.pi*(w+.03)/sw*np.exp(-np.pi*w*t)*np.exp(2j*np.pi*(ppm-5.)*sf*t)
    z[0]*=.5
    rng=np.random.default_rng(seed)
    sigma=noise_fft/np.sqrt(np.sum(np.exp(-2*np.pi*.03*t)))
    z += sigma*(rng.standard_normal(n)+1j*rng.standard_normal(n))
    if baseline:z[:3] += np.array([1500,700+300j,-200+100j])
    if phase != (0.,0.):
        import nmrglue as ng
        fft=np.fft.fftshift(np.fft.fft(z))
        z=np.fft.ifft(np.fft.ifftshift(ng.proc_base.ps(fft,p0=phase[0],p1=phase[1])))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():raise FileExistsError(path)
    write_jcamp(path,z,settings)
    p0,p1=stored if stored is not None else phase
    text=path.read_text(encoding="ascii")
    text=text.replace("##$PHC0=59.5",f"##$PHC0={p0}").replace("##$PHC1=-79",f"##$PHC1={p1}")
    stamp=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=seed*10)
    text=text.replace("##NTUPLES=NMR FID", "##LONG DATE="+stamp.strftime("%Y/%m/%d %H:%M:%S%z")+"\n##NTUPLES=NMR FID",1)
    if bad=="empty":text=""
    elif bad=="truncated":text=text[:200]
    elif bad=="nonfinite":text=re.sub(r"(##DATA TABLE=.*\n0 )-?\d+",r"\g<1>NaN",text,count=1)
    elif bad=="metadata":text=re.sub(r"(?m)^##\$SF=.*$","##$SF=0",text)
    elif bad=="time":text=re.sub(r"(?m)^##LONG DATE=.*$","##LONG DATE=invalid",text)
    path.write_text(text,encoding="ascii")
    return dict(source=str(path),seed=seed,target_physically_present=target_height!=0,
                target_center_ppm=center,nominal_target_height=target_height,solvent_generated=solvent,
                input_kind="synthetic complex FID from independent damped resonances",settings=settings,bad=bad,
                generated_parameters=dict(width_hz=width,noise_fft=noise_fft,neighbor=neighbor,baseline=baseline,
                                          phase=phase,stored=stored))


def measure_source(source, output, analysis, method, prior=(), reporting=None):
    from ..analysis.nmr import _build_complex_spectrum
    from ..analysis.nmr_validation import production_args
    from ..analysis.phase_comparison import evaluate_shared
    args=production_args(source)
    fid,x,z,_,_=_build_complex_spectrum(source,line_broadening_hz=args.line_broadening_hz,
        zero_fill_points=args.zero_fill_points,truncation_window=args.truncation_window)
    return evaluate_shared(source,x,z,fid.metadata,args,analysis,method,output,prior,
        role="primary" if method=="acme" else "comparison",reporting=reporting)


def spectral_campaign(output):
    from ..analysis.nmr import _build_complex_spectrum
    from ..analysis.nmr_validation import production_args, acquisition_time
    from ..analysis.phase_comparison import evaluate_shared, plot_acquisition
    from ..analysis.solvent_presence import solvent_presence
    output.mkdir(parents=True,exist_ok=False)
    raw=recipe();methods=("stored","acme","combined_objective","ernst_p0")
    specs=[("strong_target",{}),("weak_target",dict(target_height=80.,noise_fft=8.)),
        ("detection_limit",dict(target_height=40.,noise_fft=10.)),
        ("absent_target",dict(target_height=0.)),
        ("overlapping_target",dict(target_height=500.,neighbor=(5.83,600.,1.9))),
        ("drifted_target",dict(center=5.94)),
        ("large_neighbor",dict(target_height=60.,neighbor=(5.89,10000.,1.9))),
        ("broad_target",dict(width=20.)),
        ("baseline_distortion",dict(baseline=True)),
        ("low_snr",dict(target_height=200.,noise_fft=100.)),
        ("phase_distortion",dict(phase=(60.,-80.))),
        ("wrong_phase_seed",dict(stored=(120.,-160.))),
        ("empty_acquisition",dict(bad="empty")),("truncated_acquisition",dict(bad="truncated")),
        ("nonfinite_data",dict(bad="nonfinite")),("incorrect_metadata",dict(bad="metadata")),
        ("invalid_time",dict(bad="time")),("solvent_absent",dict(solvent=False))]
    inputs=[]
    for i,(name,options) in enumerate(specs):
        inputs.append(synthetic_fid(output/"raw"/("TEST-FID-"+name)/(name+".dx"),seed=4100+i,**options))
    from ..analysis.nmr import read_jcamp_fid
    for dataset in ("06-08-26","06-09-26"):
        sources=list((config.REPO_ROOT/"results/raw/nmr"/dataset).glob("*.dx"))
        for source in sorted(sources,key=lambda p:acquisition_time(read_jcamp_fid(p).metadata)[0]):
            inputs.append(dict(source=str(source),input_kind="archived real acquisition; read-only",target_physically_present=None))
    before={item["source"]:hashlib.sha256(Path(item["source"]).read_bytes()).hexdigest() for item in inputs}
    write_json(output/"input_manifest.json",inputs)
    rows=[];references={};archive_priors={}
    for i,item in enumerate(inputs):
        source=Path(item["source"]);folder=output/f"{i:02d}_{source.parent.name}"
        folder.mkdir();spectra={}
        try:
            args=production_args(source)
            fid,x,z,_,_=_build_complex_spectrum(source,line_broadening_hz=args.line_broadening_hz,
                zero_fill_points=args.zero_fill_points,truncation_window=args.truncation_window)
            present=solvent_presence(source,raw["solvent_monitor"])
            for method in methods:
                prior=archive_priors.setdefault((source.parent.name,method),[]) if item["target_physically_present"] is None else references.get(method,[])
                try:
                    row,spectra[method]=evaluate_shared(source,x,z,fid.metadata,args,raw["analysis"],method,
                        folder/method,prior,role="primary" if method=="acme" else "comparison",reporting=raw["qc_reporting"])
                    record_sample_presence(row,prior,raw["solvent_monitor"],present)
                    row.update(source=str(source),scenario=source.stem,input_kind=item["input_kind"],
                        generated_target_present=item["target_physically_present"])
                    if i==0:references[method]=[deepcopy(row)]
                    if item["target_physically_present"] is None:prior.append(row)
                    write_json(folder/method/"result.json",row)
                except Exception as exc:
                    row=dict(source=str(source),scenario=source.stem,method=method,success=False,peak_area=None,error=f"{type(exc).__name__}: {exc}")
                rows.append(row)
        except Exception as exc:
            for method in methods:
                rows.append(dict(source=str(source),scenario=source.stem,method=method,success=False,peak_area=None,error=f"{type(exc).__name__}: {exc}"))
        if spectra:plot_acquisition(x,spectra,folder,source.parent.name)
        write_json(output/"progress.json",rows)
        print("SPECTRUM",i+1,len(inputs),source.stem,[(r["method"],r["success"],r.get("peak_area")) for r in rows[-4:]],flush=True)
    after={item["source"]:hashlib.sha256(Path(item["source"]).read_bytes()).hexdigest() for item in inputs}
    assert before==after,"Input acquisition changed during validation"
    write_json(output/"input_integrity.json",dict(unchanged=True,sha256=before))
    write_json(output/"results.json",rows)
    write_rows(output/"acme_sensitivity.csv",[{k:v for k,v in row.items() if not isinstance(v,(dict,list))} for row in rows])
    return rows


def raw_counterexample(output):
    """A real raw-FID chain with target physically retained above the low limit.

    The instrument model changes receiver noise, never substitutes a zero-target
    specimen. Independent seeds make each acquisition a distinct numerical FID.
    """
    output.mkdir(parents=True,exist_ok=False)
    inputs=[]
    inputs.append(synthetic_fid(output/"inputs/baseline.dx",seed=6000))
    noisy=[]
    for i in range(12):
        item=synthetic_fid(output/f"inputs/noisy_target_{i:02d}.dx",seed=6100+i,
                           target_height=200.,noise_fft=100.)
        inputs.append(item);noisy.append(Path(item["source"]))
    clean=synthetic_fid(output/"inputs/ideal_same_target.dx",seed=6200,target_height=200.,noise_fft=0.)
    oracle,_=measure_source(Path(clean["source"]),output/"noise_free_reference",recipe()["analysis"],"stored")
    write_json(output/"input_manifest.json",inputs+[clean])
    baseline=Path(inputs[0]["source"])
    result=run_mock(output/"controller","RAW_FID_UNTRUSTED_LOW",[100]*40,[1]*40,
        raw_sources={"stage_1":[baseline]+noisy+[baseline]*30,"stage_2":[baseline]*40})
    observed=json.loads((output/"controller/observations_measured.json").read_text())
    first=next((r["peak_area"] for r in observed if r.get("stage")=="stage_1"),None)
    result.update(noise_free_same_target_area=oracle["peak_area"],
        original_reference_area=first,configured_near_zero_area=first*.025 if first else None,
        physical_target_fraction_of_initial=.2,
        false_completion_observed="stage_1" in result["chemical_completion_stages"],
        real_raw_FIDs_processed=True,actual_primary="acme")
    write_json(output/"raw_fid_result.json",result)
    return result

