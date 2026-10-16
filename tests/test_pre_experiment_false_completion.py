"""Adversarial endpoint regressions; all instruments and state are test-local."""
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from chemyx_lab import config
from chemyx_lab.analysis.stage_completion import completion_evidence
from chemyx_lab.analysis.stage_measurement import tracked_peak_area_observation, PeakEvidenceError
from chemyx_lab.workflows import si6_automated_nmr as base


def real_recipe():
    # Preserve the pre-gate counterexamples and legacy count-limit regressions.
    # The current recipe's mandatory timing is covered in test_si6_minimum_duration.
    raw = base.load_si6_config(config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml")
    for key in ("initial_stage", "first_addition_stage"):
        raw["workflow"][key].pop("min_completion_hours", None)
        raw["workflow"][key]["completion"]["max_iterations"] = 20
    raw["workflow"]["first_addition_stage"]["max_hours"] = 4
    return raw


def retained_fixture(folder, phase):
    """Artificial retained evidence for corruption tests, not an instrument FID."""
    source = folder / "source.dx"
    source.write_bytes(b"TEST artificial retained evidence metadata carrier")
    evidence = folder / "processed/phase_audit/test"
    evidence.mkdir(parents=True)
    x = np.linspace(5, 6.5, 1001)
    np.savez(evidence / "spectral_evidence.npz", region_ppm=x,
             regional_quantitative=1000*np.exp(-((x-5.8)/.02)**2))
    candidate = dict(interpolated_ppm=5.8, ppm_resolution=.0015, peak_height=1000.,
        snr=100., prominence_snr=100., prominence=1000., width_ppm=.04,
        positive_area=30., signed_area=30., integration_left_ppm=5.76,
        integration_right_ppm=5.84, baseline=0., negative_area=0., asymmetry=0.,
        flank_fraction=1., interpolation_quality=1.)
    (evidence / "processing_metadata.json").write_text(json.dumps({
        "raw_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "phase": phase,
        "peak_candidates": [candidate], "parameters": {"phase_method": "acme"}}))
    return source, folder / "processed"


def test_invalid_monitor_observation_cannot_authorize_completion():
    stage = replace(base.build_stages(real_recipe()["workflow"])[0], max_measurements=1)
    result = base.run_monitoring_stage(stage,
        lambda schedule: base.MeasurementObservation(False, True, "invalid", "invalid", "invalid"),
        monotonic_fn=lambda: 0., wall_now_fn=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        sleep_fn=lambda *a: None)
    assert result.outcome.stage_outcome is not base.StageOutcome.PLATEAU_REACHED


@pytest.mark.parametrize("stage,values", [(0, [100, 1, 1, 1, 1]), (1, [1, 10, 10, 10, 10])])
def test_explicitly_ineligible_observations_cannot_complete(stage, values):
    rules = base.build_stages(real_recipe()["workflow"])[stage].completion
    rows = [dict(peak_area=value, solvent_present=True, endpoint_eligible=i == 0)
            for i, value in enumerate(values)]
    assert not completion_evidence(rows, rules)["complete"]


@pytest.mark.parametrize("phase", [
    dict(method="acme", p0_deg=float("nan"), p1_deg=0., success=True),
    dict(method="acme", p0_deg=0., p1_deg=float("inf"), success=True),
    dict(method="acme", p0_deg=0., p1_deg=0., success=False),
    dict(method="unphased", p0_deg=0., p1_deg=0., success=True),
])
def test_invalid_retained_phase_cannot_supply_area(tmp_path, phase):
    source, processed = retained_fixture(tmp_path, phase)
    with pytest.raises(PeakEvidenceError):
        tracked_peak_area_observation(source, processed, {"target_ppm": 5.8}, real_recipe()["analysis"], [])


@pytest.mark.parametrize("stage,values,endpoint", [
    (0, [100, 80, 55, 30, 12, 4, 1, 1, 1, 1], 10),
    (1, [1, 30, 100, 100, 100, 100], 6),
])
def test_genuine_endpoint_is_preserved(stage, values, endpoint):
    rules = base.build_stages(real_recipe()["workflow"])[stage].completion
    rows = []
    for index, value in enumerate(values, 1):
        rows.append(dict(peak_area=value, solvent_present=True, endpoint_eligible=True))
        assert completion_evidence(rows, rules)["complete"] is (index >= endpoint)


@pytest.mark.parametrize("mutation", ["missing_time", "duplicate_time", "backwards_time", "duplicate_raw"])
def test_bad_acquisition_identity_cannot_authorize_dose(tmp_path, monkeypatch, mutation):
    from chemyx_lab.testing.false_completion_validation import run_mock
    result = run_mock(tmp_path / mutation, "timing_" + mutation,
        [100, 1, 1, 1, 1] + [20]*30, [1, 10, 10, 10, 10] + [10]*20,
        timing=mutation)
    assert not result["chemical_completion_stages"]
    assert result["dose_count"] == 0


def test_intermittently_corrupt_acquisitions_do_not_become_zero_or_complete(tmp_path):
    from chemyx_lab.testing.false_completion_validation import run_mock
    result = run_mock(tmp_path / "corrupt", "INTERMITTENT_CORRUPTION",
        [100] + [50] * 30, [1] * 30, corrupt_at=(2, 5, 8))
    report = json.loads((Path(result["run_dir"]) / "final_qc/qc_summary.json").read_text())
    rows = report["observations"]
    assert not result["chemical_completion_stages"]
    assert not result["timed_fallback_stages"]
    assert result["dose_count"] == 0
    assert len(rows) == 24  # Current real recipe monitors to 48 h, not the old cap.
    for index in (1, 4, 7):
        assert rows[index]["peak_area"] is None
        assert not rows[index]["endpoint_eligible"]
        assert not rows[index]["solvent_present"]


@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("identity", ["raw", "time"])
def test_duplicate_history_cannot_supply_four_confirmations(stage, identity):
    values = [100, 1, 1, 1, 1] if stage == 0 else [1, 10, 10, 10, 10]
    rows = [dict(peak_area=value, acquired_at=f"2026-01-01T0{i}:00:00+00:00", raw_sha256=str(i))
            for i,value in enumerate(values)]
    for row in rows[1:]:
        row["raw_sha256" if identity == "raw" else "acquired_at"] = rows[1]["raw_sha256" if identity == "raw" else "acquired_at"]
    assert not completion_evidence(rows,base.build_stages(real_recipe()["workflow"])[stage].completion)["complete"]


@pytest.mark.parametrize("mutation", ["nonconvergence", "nonfinite_parameters", "nonfinite_spectrum"])
def test_actual_acme_failure_cannot_produce_measurement(monkeypatch, mutation):
    import nmrglue as ng
    from scipy import optimize
    from chemyx_lab.analysis.nmr import build_phased_spectrum
    phase = np.array([float("nan"),0.]) if mutation == "nonfinite_parameters" else np.array([0.,0.])
    monkeypatch.setattr(optimize,"fmin",lambda *a,**k:(phase,1.,1,1,1 if mutation == "nonconvergence" else 0))
    if mutation == "nonfinite_spectrum":
        monkeypatch.setattr(ng.proc_base,"ps",lambda z,**k:np.full_like(z,complex(float("nan"),0)))
    with pytest.raises(ValueError):
        build_phased_spectrum(config.REPO_ROOT/"chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx",phase_method="acme")


def test_nonfinite_retained_complex_spectrum_is_rejected(tmp_path):
    source,processed=retained_fixture(tmp_path,dict(method="acme",p0_deg=0.,p1_deg=0.,success=True))
    path=next(processed.glob("phase_audit/*/*spectral_evidence.npz"))
    with np.load(path) as data:arrays={k:data[k] for k in data.files}
    arrays["phased"]=np.full(len(arrays["region_ppm"]),complex(float("nan"),0))
    np.savez(path,**arrays)
    with pytest.raises(PeakEvidenceError):
        tracked_peak_area_observation(source,processed,{"target_ppm":5.8},real_recipe()["analysis"],[])


@pytest.mark.parametrize("duration", [0.,1800.,1800.+1.e-6])
@pytest.mark.parametrize("wall_jump", [0.,-86400.,86400.])
def test_stage2_deadline_uses_monotonic_clock_and_starts_no_late_acquisition(duration, wall_jump):
    stage=base.build_stages(real_recipe()["workflow"])[1]
    seconds=[0.];starts=[]
    def wall():
        return datetime(2026,1,1,tzinfo=timezone.utc)+__import__('datetime').timedelta(seconds=seconds[0]+(wall_jump if starts else 0))
    def measure(schedule):
        starts.append(seconds[0]);seconds[0]+=duration
        return base.MeasurementObservation(True,False,"TEST virtual","TEST virtual","TEST virtual")
    result=base.run_monitoring_stage(stage,measure,monotonic_fn=lambda:seconds[0],wall_now_fn=wall,
        sleep_fn=lambda label,delay:seconds.__setitem__(0,seconds[0]+delay))
    assert result.outcome.stage_outcome is base.StageOutcome.RUNTIME_LIMIT_REACHED
    assert starts and all(t < 4*3600 for t in starts)
    assert seconds[0] >= 4*3600
    assert seconds[0] <= 4*3600+duration

