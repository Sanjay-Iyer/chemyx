"""Independent final HOME endpoint counterexamples; no live transports."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.analysis.stage_completion import completion_evidence


def nominal_stage(name):
    raw = yaml.safe_load((config.REPO_ROOT / "chemyx_lab/testing/fixtures/si6_historical_statistical.yaml").read_text())
    return raw["workflow"]["initial_stage" if name == "stage_1" else "first_addition_stage"]


def evidence_prefixes(name, values):
    stage = nominal_stage(name)
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index, area in enumerate(values):
        rows.append(dict(peak_area=area, peak_clear=True,
                         measurement_valid=True, area_uncertainty=0.01,
                         acquired_at=(origin + timedelta(minutes=index * stage["interval_minutes"])).isoformat()))
        yield completion_evidence(rows, stage["completion"])


@pytest.mark.parametrize("name,values,iteration", [
    ("stage_1", [100, 82, 64, 45, 28, 15, 7, 3, 1.8, 1.4, 1.3, 1.2, 1.2, 1.2], 14),
    ("stage_2", [1, 8, 20, 38, 56, 71, 82, 90, 95, 98, 99, 99.4, 99.2, 99.5, 99.4, 99.4], 16),
])
def test_independent_conceptual_success_requires_three_complete_windows(name, values, iteration):
    evidence = list(evidence_prefixes(name, values))
    assert not any(item["complete"] for item in evidence[:-1])
    assert evidence[-1]["complete"]
    assert len(evidence) == iteration
    assert len(evidence[-1]["checks"]) == 3
    assert all(item["passed"] for item in evidence[-1]["checks"])
    assert not evidence[-2]["checks"][0]["passed"]


@pytest.mark.parametrize("values", [
    [100, 75, 52, 4, 35, 25, 18],
    [100, 55, 25, 10, 6, 4.2, 3.0, 2.1],
    [100, 70, 50, 40, 39.5, 39.3, 39.4, 39.2] + [39.2] * 15,
])
def test_independent_stage1_counterexamples_never_complete(values):
    assert not any(e["complete"] for e in evidence_prefixes("stage_1", values))


@pytest.mark.parametrize("values", [
    [1, 10, 25, 42, 58, 70, 80, 88, 94],
    [1, 15, 35, 55, 70, 72, 72.2, 72.1, 82, 90, 96],
    [2, 2.1, 2.0, 2.1, 2.0, 2.1] + [2.1] * 15,
    [1, 20, 50, 80, 95, 70, 60, 59, 59] + [59] * 15,
])
def test_independent_stage2_counterexamples_never_complete(values):
    assert not any(e["complete"] for e in evidence_prefixes("stage_2", values))


def test_independent_temporal_plateau_limit_is_explicit():
    # A sustained plateau can establish current stability; no algorithm can
    # infer hypothetical later growth from future data that has not arrived.
    evidence = list(evidence_prefixes("stage_2", [1, 15, 35, 55, 70] + [72] * 12))
    assert evidence[-1]["complete"]
    assert all(c["passed"] for c in evidence[-1]["checks"])


@pytest.fixture(scope="module")
def spectral_engine():
    from chemyx_lab.testing.si6_synthetic_analysis import SyntheticSpectra
    return SyntheticSpectra()


def spectral_input(tmp_path):
    from chemyx_lab.testing.si6_synthetic_analysis import DATASET, metadata_carrier, historical_nominal as nominal
    source = tmp_path / "synthetic_metadata_carrier.dx"
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    metadata_carrier(source, stamp)
    metadata = dict(iteration=1, stage="stage_1", elapsed_hours=0,
                    acquired_at=stamp.isoformat(), target_ppm=5.8,
                    dataset_display_name=DATASET)
    return source, metadata, nominal()["analysis"]


def test_independent_spectrum_amplitude_drives_measured_integral(tmp_path, spectral_engine):
    import numpy as np
    source, metadata, analysis = spectral_input(tmp_path)
    high, _ = spectral_engine.measure(source, tmp_path / "high", metadata, analysis, [], area=100)
    low, _ = spectral_engine.measure(source, tmp_path / "low", metadata, analysis, [high], area=25)
    assert high["measurement_valid"] and low["measurement_valid"]
    assert low["peak_area"] / high["peak_area"] == pytest.approx(.25, abs=.001)
    assert high["peak_area"] != 100  # Computed, not copied from requested area.
    with np.load(tmp_path / "low/phase_audit/synthetic_processed/spectral_evidence.npz") as arrays:
        assert np.max(arrays["generated_real"]) < 1000
        assert len(arrays["region_ppm"]) == len(arrays["regional_quantitative"])


def test_independent_spectrum_seed_is_reproducible_and_drift_is_measured(tmp_path, spectral_engine):
    source, metadata, analysis = spectral_input(tmp_path)
    a, _ = spectral_engine.measure(source, tmp_path / "a", metadata, analysis, [], area=100, seed=500)
    b, _ = spectral_engine.measure(source, tmp_path / "b", metadata, analysis, [], area=100, seed=500)
    c, _ = spectral_engine.measure(source, tmp_path / "c", metadata, analysis, [a], area=100, seed=500, drift_ppm=.01)
    assert a["peak_area"] == b["peak_area"]
    assert a["area_uncertainty"] == b["area_uncertainty"]
    assert c["peak_ppm"] - a["peak_ppm"] == pytest.approx(.01, abs=.001)
    assert c["peak_area"] / a["peak_area"] == pytest.approx(1, abs=.01)


def test_independent_production_peak_qc_rejection_cannot_become_absence(tmp_path, spectral_engine, monkeypatch):
    source, metadata, analysis = spectral_input(tmp_path)
    analysis["peak_tracking"]["width"]["minimum_ppm"] = .15
    with pytest.raises(ValueError, match="TRACKED_PEAK_LOST"):
        spectral_engine.measure(source, tmp_path / "rejected", metadata, analysis, [], area=100)


def test_independent_high_noise_fails_production_tracked_area_qc(tmp_path, spectral_engine):
    source, metadata, analysis = spectral_input(tmp_path)
    reference, _ = spectral_engine.measure(source, tmp_path / "reference", metadata, analysis, [], area=100)
    with pytest.raises(ValueError, match="noise|uncertainty|Negative|Rejected"):
        spectral_engine.measure(source, tmp_path / "bad_noise", metadata, analysis, [reference], area=25, noise_sd=20)


def test_independent_actual_spectral_workflow_transition_and_order(tmp_path, spectral_engine):
    from chemyx_lab.testing.si6_synthetic_analysis import validate_workflow
    result = validate_workflow(tmp_path, spectral_engine)
    events = result["events"]
    completions = [e for e in events if e["event_type"] == "stage_completion_evidence"]
    first = [e for e in completions if e["workflow_phase"] == "stage_1"]
    second = [e for e in completions if e["workflow_phase"] == "stage_2"]
    assert len(first) == 11 and len(second) == 13
    assert not any(e["evidence"]["complete"] for e in first[:-1] + second[:-1])
    assert first[-1]["evidence"]["complete"] and second[-1]["evidence"]["complete"]
    intent = next(e for e in events if e["event_type"] == "dose_dispatch_intent")
    confirmed = next(e for e in events if e["event_type"] == "dose_confirmed")
    assert first[-1]["sequence"] < intent["sequence"] < confirmed["sequence"] < second[0]["sequence"]
    moves = [e for e in events if e.get("lifecycle_state") == "completed" and e.get("operation_type") == "infuse" and e.get("channel") == 2]
    assert len(moves) == 1
    assert moves[0]["requested_parameters"]["volume"] == 1.8
    assert moves[0]["requested_parameters"]["rate"] == 1.0
    assert moves[0]["sequence"] == confirmed["pump_completion_sequence"]


def test_independent_synthetic_saved_plot_dataset_title_contract(tmp_path, spectral_engine, monkeypatch):
    from chemyx_lab.testing import si6_synthetic_analysis as synthetic
    cases = {name: synthetic.validate_case(name, 2, tmp_path / "level2", spectral_engine)
             for name in ("stage1_success", "stage2_success")}
    # Plot testing has no instrument dependency. Use the real measured cases;
    # the actual dose ordering is independently tested above in the runner.
    rows = cases["stage1_success"]["rows"] + cases["stage2_success"]["rows"]
    observed = []
    original = synthetic.save_figure
    def capture_title(fig, stem, dataset, description, manifest):
        title = original(fig, stem, dataset, description, manifest)
        observed.append(fig._suptitle.get_text())
        return title
    monkeypatch.setattr(synthetic, "save_figure", capture_title)
    manifest = synthetic.plots(tmp_path / "figures", cases,
                              dict(rows=rows, transition={"elapsed_hours": 26.25}))
    assert len(manifest) == 12 and len(observed) == 4
    for item in manifest:
        path = Path(item["path"])
        assert path.is_file() and path.stat().st_size > 1000
        assert item["dataset_display_name"] == synthetic.DATASET
        assert item["visible_title"].startswith(synthetic.DATASET + " ")
        assert item["visible_title"] in observed
        if path.suffix == ".svg":
            assert item["visible_title"] in path.read_text(encoding="utf-8")
    for stem in {Path(i["path"]).stem for i in manifest}:
        group = [i for i in manifest if Path(i["path"]).stem == stem]
        assert len({i["visible_title"] for i in group}) == 1
        assert {Path(i["path"]).suffix for i in group} == {".png", ".svg", ".pdf"}


def test_independent_guides_cli_flags_match_actual_help():
    """Validate commands by parser help only; never execute documented live runs."""
    import re
    import subprocess
    import sys
    scripts = {}
    for name in ("SI6_START_HERE.md", "SI6_SCRIPT_USER_GUIDE.md", "SI6_CONFIG_USER_GUIDE.md"):
        text = (config.REPO_ROOT / "docs" / name).read_text(encoding="utf-8")
        for code in re.findall(r"```powershell\n(.*?)```", text, re.S):
            for line in code.splitlines():
                if not (line.startswith("python ") or line.startswith(".venv\\Scripts\\python.exe ")):
                    continue
                tokens = line.split()
                if "-m" in tokens:
                    continue
                script = next(t for t in tokens[1:] if t.endswith(".py"))
                scripts.setdefault(script, set()).update(t for t in tokens if t.startswith("--"))
    assert scripts
    for script, flags in scripts.items():
        path = config.REPO_ROOT / script.replace("\\", "/")
        assert path.is_file(), f"Stale script in guide: {script}"
        help_result = subprocess.run([sys.executable, "-B", str(path), "--help"],
                                     cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=30)
        assert help_result.returncode == 0, help_result.stderr
        for flag in flags:
            assert flag in help_result.stdout, f"Invented CLI flag in guide: {script} {flag}"


def test_independent_nominal_guide_yaml_matches_actual_template():
    import re
    from chemyx_lab.testing.si6_synthetic_analysis import nominal
    raw = nominal()
    machine = yaml.safe_load((config.REPO_ROOT / "config_templates/machines/si6_instrument_settings.example.yaml").read_text())
    text = (config.REPO_ROOT / "docs/SI6_CONFIG_USER_GUIDE.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.S)
    assert len(blocks) >= 7
    def assert_subset(value, expected):
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                assert subkey in expected
                assert_subset(subvalue, expected[subkey])
        else:
            assert value == expected
    for code in blocks:
        mapping = yaml.safe_load(code)
        for key, value in mapping.items():
            if key in {"initial_stage", "first_addition_stage", "cycle", "pump_extra_seconds"}:
                assert_subset(value, raw["workflow"][key])
            elif key == "after_monitoring":
                assert value == raw["workflow"]["initial_stage"][key]
            elif key == "chemyx":
                assert value == machine[key]
            elif key == "nmr" and "host" in value:
                assert value == machine[key]
            else:
                assert value == raw[key], f"Stale YAML block for {key}"
