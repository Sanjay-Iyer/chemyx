"""Offline drift, identity, toggles, censored absence and plot title coverage."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import numpy as np
import pytest
from chemyx_lab.analysis.peak_tracking import DEFAULTS, configure_candidates, tracking_config
from chemyx_lab.analysis.stage_measurement import select_candidate
from chemyx_lab.testing.si6_synthetic_analysis import SyntheticSpectra, historical_nominal as nominal_config, metadata_carrier


def candidate(center=5.8, **changes):
    p = dict(interpolated_ppm=center, peak_height=100, snr=100, prominence_snr=50, prominence=50,
             width_ppm=.03, positive_area=2, interpolation_quality=1,
             asymmetry=.1, flank_fraction=.5)
    p.update(changes)
    return p


def test_stronger_neighbor_does_not_steal_identity():
    target, neighbor = candidate(5.79), candidate(5.86, snr=1000)
    selected, audit = select_candidate([neighbor,target], tracking_config({}),5.8,5.8,5.8)
    assert selected["interpolated_ppm"] == 5.79
    assert sum(p["selected"] for p in audit) == 1


def test_rejected_target_does_not_permit_neighbor_hop():
    selected, audit = select_candidate([candidate(5.8,width_ppm=.25),candidate(5.86)],tracking_config({}),5.8,5.8,5.8)
    assert selected is None and "width" in audit[0]["rejection_reasons"]


def test_excessive_drift_loses_identity():
    selected, audit = select_candidate([candidate(5.91)],tracking_config({}),5.8,5.8,5.8)
    assert selected is None and "previous_shift" in audit[0]["rejection_reasons"]


def test_continuity_filter_disabled_does_not_reject_farther_eligible_peak():
    candidates = [candidate(5.8, width_ppm=.25), candidate(5.86)]
    cfg = tracking_config({"continuity": {"enabled": False}})
    assert select_candidate(candidates,cfg,5.8,5.8,5.8)[0]["interpolated_ppm"] == 5.86


def test_disabled_snr_value_cannot_reject_via_neighbor_or_continuity():
    cfg = tracking_config({"snr": {"enabled": False, "minimum": 1.e20}})
    assert select_candidate([candidate()],cfg,5.8,5.8,5.8)[0] is not None


@pytest.mark.parametrize("name,changes,target,previous,reference",[
    ("search",{},5.0,None,None),
    ("previous_shift",{},5.8,5.6,None),
    ("reference_shift",{},5.8,None,5.5),
    ("snr",{"snr":1},5.8,None,None),
    ("prominence",{"prominence_snr":1},5.8,None,None),
    ("absolute_prominence",{"prominence":-1},5.8,None,None),
    ("width",{"width_ppm":.005},5.8,None,None),
    ("height",{"peak_height":-1},5.8,None,None),
    ("area",{"positive_area":0},5.8,None,None),
    ("shoulder",{"asymmetry":.9},5.8,None,None),
    ("slope",{"flank_fraction":0},5.8,None,None),
    ("shape",{"interpolation_quality":0},5.8,None,None),
])
def test_each_filter_enabled_rejects_disabled_accepts(name,changes,target,previous,reference):
    cfg=tracking_config({name:{"enabled":True}})
    p=candidate(**changes)
    assert select_candidate([p],cfg,target,previous,reference)[0] is None
    cfg[name]["enabled"]=False
    assert select_candidate([p],cfg,target,previous,reference)[0] is not None


def test_neighbor_toggle_and_candidate_generation_separation_toggle():
    candidates=[candidate(5.8),candidate(5.84)]
    cfg=tracking_config({"neighbor":{"enabled":True}})
    assert select_candidate(candidates,cfg,5.8)[0] is None
    cfg["neighbor"]["enabled"]=False
    assert select_candidate(candidates,cfg,5.8)[0] is not None
    args=configure_candidates(SimpleNamespace(),{"separation":{"enabled":False,"minimum_ppm":1}})
    assert args.min_peak_distance_ppm == 1.e-12
    assert args.min_peak_width_ppm == 1.e-12 and args.min_prominence_snr == 0


def test_moving_spectrum_uses_variable_bounds_and_existing_peak_area(tmp_path):
    engine=SyntheticSpectra()
    analysis=nominal_config()["analysis"]
    rows=[]
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    centers=[5.79,5.77,5.81,5.75,5.83]
    for i,center in enumerate(centers):
        source=tmp_path/f"{i}.dx"
        stamp=start+timedelta(hours=i)
        metadata_carrier(source,stamp)
        meta=dict(target_ppm=5.8,acquired_at=stamp.isoformat(),timestamp_source="LONG DATE header",dataset_display_name="DRIFT TEST",stage="stage_1",iteration=i+1,elapsed_hours=i)
        row,_=engine.measure(source,tmp_path/f"processed_{i}",meta,analysis,rows,area=100,seed=500,drift_ppm=center-5.8)
        rows.append(row)
        assert row["peak_ppm"] == pytest.approx(center,abs=.001)
        assert row["integration_left_ppm"]<center<row["integration_right_ppm"]
        assert row["integration_right_ppm"]-row["integration_left_ppm"]<.1
        assert row["peak_area"] == next(p["positive_area"] for p in json.loads(row["candidate_audit"]) if p["selected"])
    assert max(r["peak_area"] for r in rows)/min(r["peak_area"] for r in rows)<1.01


def test_new_ppm_plots_share_dataset_title_and_manifest(tmp_path):
    from chemyx_lab.analysis.si6_stage_reports import write_stage_reports
    from chemyx_lab.workflows.si6_automated_nmr import build_stages
    rows=[dict(stage="stage_1",elapsed_hours=0,peak_area=100,peak_ppm=5.79,area_uncertainty=.01,peak_clear=True,measurement_valid=True,acquired_at="2026-01-01T00:00:00+00:00")]
    manifest=write_stage_reports(SimpleNamespace(run_dir=tmp_path),rows,build_stages(nominal_config()["workflow"]),dataset="DRIFT TEST")
    plots=[p for p in manifest if "peak_ppm_time_series" in p["path"]]
    assert len(plots)==3
    assert all(p["visible_title"].startswith("DRIFT TEST ") and p["dataset_display_name"]=="DRIFT TEST" for p in plots)
    assert len({p["visible_title"] for p in plots})==1


def test_profile_cli_retains_unrounded_candidates_and_disables_offline_replay(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    from chemyx_lab import config
    from chemyx_lab.workflows.three_instrument_si6 import MOCK_NMR_FIXTURE
    profile = tmp_path / "tracking.json"
    profile.write_text(json.dumps(DEFAULTS), encoding="utf-8")
    result = subprocess.run([sys.executable,"-B",str(config.REPO_ROOT/"scripts/nmr/process_fid.py"),
                             str(MOCK_NMR_FIXTURE),"--output-dir",str(tmp_path),"--run-name","tracked_cli",
                             "--dataset-display-name","TRACKING CLI", "--peak-tracking-config",str(profile)],
                            cwd=config.REPO_ROOT,capture_output=True,text=True,timeout=90)
    assert result.returncode == 0, result.stderr
    output = tmp_path / "tracked_cli"
    files = list(output.glob("phase_audit/*/*processing_metadata.json"))
    assert len(files)==1
    audit = json.loads(files[0].read_text(encoding="utf-8"))
    assert audit["peak_candidates"] and audit["parameters"]["min_peak_width_ppm"]==1.e-12
    assert all("integration_left_ppm" in p and "positive_area" in p for p in audit["peak_candidates"])
    assert audit["phase"]["method"]=="stored"
    assert not (output/"target_peak").exists()


def test_existing_neighbor_cannot_replace_vanished_target_in_processed_spectrum(tmp_path):
    import hashlib
    from chemyx_lab.analysis.nmr import pick_spectrum_region
    from chemyx_lab.analysis.peak_tracking import candidate_records
    from chemyx_lab.analysis.stage_measurement import tracked_peak_measurement, TrackingFailure
    source = tmp_path / "simulation_source.dx"
    source.write_bytes(b"SIMULATION ONLY: already-phased test arrays, not an acquired FID")
    x = np.linspace(5.0,6.5,2001)
    noise = np.random.default_rng(19).normal(0,.001,len(x))
    neighbor = 150*np.exp(-.5*((x-5.86)/.014)**2)
    analysis = nominal_config()["analysis"]
    def measure(name, trace, prior):
        output = tmp_path/name
        audit = output/"phase_audit/sample"
        audit.mkdir(parents=True)
        picked = pick_spectrum_region(x,trace,quantitative_intensity=trace,min_prominence_snr=0,min_width_ppm=1.e-12)
        np.savez(audit/"spectral_evidence.npz",region_ppm=picked.ppm_axis,regional_quantitative=picked.quantitative_corrected)
        meta = dict(raw_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),peak_candidates=candidate_records(picked),
                    phase=dict(method="synthetic_already_phased",p0_deg=0,p1_deg=0),parameters=dict(normalization="none"))
        (audit/"processing_metadata.json").write_text(json.dumps(meta),encoding="utf-8")
        return tracked_peak_measurement(source,output,dict(target_ppm=5.8),analysis,prior,reference=prior[0] if prior else None)
    first = measure("first",100*np.exp(-.5*((x-5.8)/.014)**2)+neighbor+noise,[])
    assert first["peak_ppm"] < 5.82
    with pytest.raises(TrackingFailure,match="TRACKED_PEAK_LOST"):
        measure("missing_target",neighbor+noise,[first])
    audit=json.loads((tmp_path/"missing_target/tracking_evidence.json").read_text())
    assert any("previous_neighbor_identity" in p["rejection_reasons"] for p in audit["candidates"])


def test_missing_production_evidence_persists_processing_failure(tmp_path):
    from chemyx_lab.analysis.stage_measurement import tracked_peak_measurement, TrackingFailure
    source=tmp_path/"source.dx"
    source.write_bytes(b"simulation only")
    with pytest.raises(TrackingFailure,match="PROCESSING_FAILED"):
        tracked_peak_measurement(source,tmp_path,dict(target_ppm=5.8),nominal_config()["analysis"],[])
    audit=json.loads((tmp_path/"tracking_evidence.json").read_text())
    assert not audit["measurement_valid"] and audit["candidates"]==[]
