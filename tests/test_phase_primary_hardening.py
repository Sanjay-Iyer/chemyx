"""Selectable quantitative phasing and post-session isolation; no hardware."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import nmrglue as ng
import pytest
import yaml
from chemyx_lab import config
from chemyx_lab.analysis import phase_methods as pm
from chemyx_lab.analysis.nmr import build_phased_spectrum, _build_complex_spectrum
from chemyx_lab.analysis.nmr_validation import production_args
from chemyx_lab.analysis.phase_comparison import evaluate_shared, acquisition_comparison, summarize
from chemyx_lab.workflows import three_instrument_si6 as si6

ROOT = config.REPO_ROOT
FIXTURE = ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"


@pytest.mark.parametrize("method", pm.METHODS)
def test_config_selections(method):
    assert pm.settings({"processing": {"phase_method": method}})["phase_method"] == method


@pytest.mark.parametrize("value", ["unphased", "none", "typo", None, 4])
def test_reject_invalid_primary_before_transport(value, monkeypatch):
    monkeypatch.setattr(si6, "SerialTransport", lambda *a, **k: pytest.fail("hardware opened"))
    raw = {"nmr": {"processing": {"phase_method": value}}}
    with pytest.raises(ValueError, match="primary"):
        with si6.open_services(raw, {}, None, None, identity=si6.RunIdentity("si6", False)):
            pytest.fail("entered services")


def test_missing_models_preflight(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "DEEP_ROOT", tmp_path)
    with pytest.raises(ValueError, match="manifest missing"):
        pm.preflight({"processing": {"phase_method": "deep_phaser"}})


@pytest.mark.parametrize("method", pm.METHODS)
def test_standalone_paths_propagate_recipe_method(tmp_path, monkeypatch, method):
    raw, _, pump, nmr = si6.prepare(ROOT / "configs/experiments/si6_real_two_stage.yaml",
        ROOT / "configs/machines/si6_real_COM4.yaml", ROOT / "arduino/configs/arduino.example.yaml", mock=True)
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    raw["nmr"]["processing"]["phase_method"] = method
    calls, preflights = [], []
    monkeypatch.setattr(pm, "preflight", lambda nmr: preflights.append(pm.settings(nmr)["phase_method"]) or pm.settings(nmr))
    monkeypatch.setattr(si6, "run_diagnostic", lambda services, selection: calls.append(services.process.keywords))
    si6.run_nmr_only(raw, pump, nmr, mock=True)
    def process(source, paths, name, **options):
        calls.append(options)
        return paths.processed_dir
    monkeypatch.setattr(si6.base, "run_process_fid_postprocessing", process)
    raw.pop("solvent_monitor", None)
    monkeypatch.setattr(si6, "analyze_tracked_resonance", lambda *args: pytest.fail("legacy analysis used for area recipe"))
    monkeypatch.setattr(si6, "tracked_peak_area_observation", lambda *args: {"peak_clear": True, "phase_method": method})
    si6.run_processing_only(raw, nmr, FIXTURE, mock=True)
    assert preflights == [method, method]
    assert len(calls) == 2
    assert all(c["phase_method"] == method and c["peak_tracking"] == raw["analysis"]["peak_finding"] for c in calls)


@pytest.mark.parametrize("path", ["nmr", "processing"])
def test_standalone_bad_method_rejected_before_acquisition_or_outputs(tmp_path, monkeypatch, path):
    raw = {"nmr": {"processing": {"phase_method": "bad_method"}}}
    monkeypatch.setattr(si6, "create_identified_run", lambda *a: pytest.fail("output allocation before preflight"))
    with pytest.raises(ValueError, match="primary"):
        if path == "nmr":
            si6.run_nmr_only(raw, None, None, mock=True)
        else:
            si6.run_processing_only(raw, None, FIXTURE, mock=True)


@pytest.mark.parametrize("method", ["stored", "acme", "combined_objective", "ernst_p0"])
def test_same_fft_convention_and_production_measurement(tmp_path, method):
    recipe = yaml.safe_load((ROOT / "configs/experiments/si6_real_two_stage.yaml").read_text())
    args = production_args(FIXTURE)
    fid, x, z, _, _ = _build_complex_spectrum(FIXTURE, line_broadening_hz=args.line_broadening_hz,
            zero_fill_points=args.zero_fill_points, truncation_window=args.truncation_window)
    row, phased = evaluate_shared(FIXTURE, x, z, fid.metadata, args, recipe["analysis"], method, tmp_path / method)
    phase = row["phase"]
    assert phase["method"] == method and phase["success"]
    expected = ng.proc_base.ps(z, p0=phase["p0_deg"], p1=phase["p1_deg"], inv=phase["inverse_phase"])
    np.testing.assert_allclose(phased, expected, rtol=1.e-12, atol=1.e-9)
    production = build_phased_spectrum(FIXTURE, phase_method=method,
            line_broadening_hz=args.line_broadening_hz, zero_fill_points=args.zero_fill_points,
            truncation_window=args.truncation_window)
    np.testing.assert_allclose(production.real + 1j*production.imaginary, phased, rtol=1.e-12, atol=1.e-9)
    assert phased.shape == z.shape and np.isfinite(phased).all()
    assert row["phase_method"] == method and row["peak_area"] is not None


def test_acme_uses_validated_objective(monkeypatch):
    from scipy.optimize import fmin
    x = np.linspace(0, 12, 4096)
    z = 1 / (.025 + 1j*(x-5.8)) * np.exp(.3j)
    actual, audit = pm.apply_phase(x, z, {}, "acme")
    p = fmin(ng.proc_autophase._ps_acme_score, [0., 0.], args=(z,), disp=False,
             maxiter=1000, maxfun=2000)
    np.testing.assert_allclose(actual, ng.proc_base.ps(z, *p), rtol=1.e-12)
    assert audit["starting_values_deg"] == [0., 0.]
    assert not audit["inverse_phase"]


def test_acme_matches_pre_change_historical_validator():
    import shutil
    import subprocess
    import types
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        pytest.skip("Immutable pre-change validator is checked in the Git source checkout")
    old_source = subprocess.check_output(["git", "show", "HEAD:chemyx_lab/analysis/nmr_validation.py"], cwd=ROOT)
    reference = types.ModuleType("chemyx_lab.analysis.historical_validation_reference")
    reference.__file__ = str(ROOT / "chemyx_lab/analysis/nmr_validation.py")
    reference.__package__ = "chemyx_lab.analysis"
    exec(compile(old_source, reference.__file__, "exec"), reference.__dict__)
    stored = reference.analyze(FIXTURE)
    (historical, status), _ = reference.alternative_phases(stored)
    assert status["converged"]
    current = build_phased_spectrum(FIXTURE, phase_method="acme",
        line_broadening_hz=stored["args"].line_broadening_hz,
        zero_fill_points=stored["args"].zero_fill_points,
        truncation_window=stored["args"].truncation_window)
    np.testing.assert_allclose(current.real, historical["spectrum"].real, rtol=1.e-12, atol=1.e-9)
    np.testing.assert_allclose(current.imaginary, historical["spectrum"].imaginary, rtol=1.e-12, atol=1.e-9)
    assert current.phase_audit["starting_values_deg"] == status["initial_direct_phases_deg"]


def test_primary_optimizer_and_model_failures_never_zero(monkeypatch):
    from scipy import optimize
    z = np.ones(100, dtype=complex)
    monkeypatch.setattr(optimize, "fmin", lambda *a, **k: (np.array([0., 0.]), 1., 1, 1, 1))
    with pytest.raises(ValueError, match="without fallback"):
        pm.apply_phase(np.arange(100), z, {}, "acme")
    monkeypatch.setattr(pm, "deep_request", lambda *a, **k: {"success": True, "p0_deg": float('nan'), "p1_deg": 0., "inverse_phase": True})
    with pytest.raises(ValueError, match="Nonfinite"):
        pm.apply_phase(np.arange(100), z, {}, "deep_phaser")


@pytest.mark.parametrize("method", ["ernst_p0", "deep_phaser"])
def test_independent_methods_do_not_require_stored_phase_fields(monkeypatch, method):
    x = np.linspace(0, 12, 1024)
    z = 1 / (.03+1j*(x-5.8))
    monkeypatch.setattr(pm, "deep_request", lambda *a, **k: {
        "success": True, "p0_deg": 0., "p1_deg": 0., "inverse_phase": True})
    phased, audit = pm.apply_phase(x, z, {"$PHC0": "unavailable", "$PHC1": "unavailable"}, method)
    assert np.isfinite(phased).all() and audit["stored_source_fields"] == []


def test_optional_failure_isolated_and_same_fft(tmp_path, monkeypatch):
    from chemyx_lab.analysis import phase_comparison as pc
    source = tmp_path / "test.dx"
    source.write_text("metadata carrier")
    evidence = tmp_path / "spectral_evidence.npz"
    x = np.linspace(0, 12, 1024)
    z = 1 / (.03+1j*(x-5.8)) + 100 / (.03+1j*(x-2.08))
    np.savez(evidence, ppm=x, unphased=z)
    evidence.with_name("processing_metadata.json").write_text(json.dumps({"raw_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "jcamp_metadata": {}, "parameters": {}}))
    calls = []
    def evaluate(source, ppm, fft, metadata, args, analysis, method, output, prior):
        np.testing.assert_array_equal(fft, z)
        calls.append(method)
        if method == "deep_phaser":
            raise RuntimeError("bad optional model")
        return {"method": method, "success": True, "peak_area": 42}, fft
    monkeypatch.setattr(pc, "evaluate_shared", evaluate)
    original_save = pc.save_figure
    def checked_save(fig, *args, **kwargs):
        # Strong solvent peaks outside the target panel must not flatten its
        # y scale and hide the much smaller target resonance.
        assert fig.axes[2].get_ylim()[1] < fig.axes[0].get_ylim()[1] * .5
        return original_save(fig, *args, **kwargs)
    monkeypatch.setattr(pc, "save_figure", checked_save)
    rows = acquisition_comparison(source, evidence, {}, ["stored", "deep_phaser"], tmp_path / "comparison", dataset="TEST-RUN-001")
    assert calls == ["stored", "deep_phaser"]
    assert rows[0]["peak_area"] == 42
    assert rows[1]["peak_area"] is None and not rows[1]["success"]
    assert not (tmp_path / "comparison/deep_phaser").exists()
    manifests = list(__import__('csv').DictReader((tmp_path / "comparison/plot_manifest.csv").open(encoding="utf-8")))
    assert len(manifests) == 3
    assert {r["visible_title"] for r in manifests} == {"TEST-RUN-001 Phase Comparison — shared complex FFT"}
    assert {r["dataset_display_name"] for r in manifests} == {"TEST-RUN-001"}


def test_no_timing_metadata_no_time_plot(tmp_path):
    summarize([], tmp_path / "summary", "TEST-RUN-001")
    assert not (tmp_path / "summary/target_area_vs_time.png").exists()
    assert (tmp_path / "summary/target_area_vs_time_unavailable.json").exists()


def test_comparison_summary_titles_all_formats_and_shared_timing_table(tmp_path):
    import csv
    rows = [{"method": m, "success": True, "peak_area": area,
             "acquired_at": f"2026-06-09T10:{minute:02d}:00-04:00", "timestamp_source": "LONG DATE header",
             "raw_sha256": str(minute), "total_s": 1., "quality": {"negative_area_fraction": .01}}
            for m, area in (("stored", 10.), ("acme", 11.)) for minute in (0, 15)]
    summarize(rows, tmp_path / "summary", " TEST-RUN-001  ")
    manifest = list(csv.DictReader((tmp_path / "summary/plot_manifest.csv").open(encoding="utf-8")))
    assert len(manifest) == 12
    titles = {}
    for record in manifest:
        assert record["dataset_display_name"] == "TEST-RUN-001"
        assert record["visible_title"].startswith("TEST-RUN-001 ")
        titles.setdefault(Path(record["path"]).stem, set()).add(record["visible_title"])
    assert all(len(values) == 1 for values in titles.values())
    shared = list(csv.DictReader((tmp_path / "summary/comparison_summary.csv").open(encoding="utf-8")))
    assert len(shared) == 4 and all(r["timestamp_source"] == "LONG DATE header" for r in shared)


def test_disabled_comparison_never_runs_methods(tmp_path, monkeypatch):
    from chemyx_lab.analysis import phase_comparison as pc
    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata/config_snapshot.json").write_text(json.dumps({"nmr": {"processing": {
        "phase_method": "acme", "phase_comparison": {"enabled": False}}}}))
    monkeypatch.setattr(pc, "apply_phase", lambda *a, **k: pytest.fail("disabled comparison executed"))
    assert pc.compare_run(tmp_path) == {"status": "disabled"}
    assert not (tmp_path / "final_qc").exists()


def test_copied_run_comparison_uses_local_raw_file(tmp_path, monkeypatch):
    from chemyx_lab.analysis import phase_comparison as pc
    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata/config_snapshot.json").write_text(json.dumps({"analysis": {}, "nmr": {"processing": {
        "phase_method": "acme", "phase_comparison": {"enabled": True, "methods": ["stored"],
        "include_unphased_reference": False}}}}))
    evidence = tmp_path / "step_1/nmr_processed/acquisition/phase_audit/shared/spectral_evidence.npz"
    evidence.parent.mkdir(parents=True)
    evidence.touch()
    evidence.with_name("processing_metadata.json").write_text(json.dumps({"source_path": "C:/unavailable_old_machine/sample.dx"}))
    local = tmp_path / "step_1/nmr_raw/sample.dx"
    local.parent.mkdir(parents=True)
    local.write_text("local hash verification occurs in acquisition_comparison")
    seen = []
    def compare(source, *args, **kwargs):
        seen.append(source)
        return [{"method": "stored", "success": True}]
    monkeypatch.setattr(pc, "acquisition_comparison", compare)
    monkeypatch.setattr(pc, "summarize", lambda rows, output, dataset: output.mkdir(parents=True))
    assert pc.compare_run(tmp_path)["results"] == 1
    assert seen == [local]


def test_real_recipe_default_historical_recipes_unchanged():
    raw = si6.base.load_si6_config(ROOT / "configs/experiments/si6_real_two_stage.yaml")
    assert pm.settings(raw["nmr"])["phase_method"] == "acme"
    old = si6.base.load_si6_config(ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml")
    assert pm.settings(old["nmr"])["phase_method"] == "stored"
