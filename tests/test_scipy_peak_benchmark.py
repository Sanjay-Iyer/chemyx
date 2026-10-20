"""Numerical and plotting invariants for the isolated offline boundary study."""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from chemyx_lab.analysis.nmr import integrate_above_local_baseline
from chemyx_lab.analysis.scipy_peak_comparison import (METHODS, DetectionSettings, parameter_sweep,
    detection_context, choose_target, evaluate, integrate_interval, integration_trace)
from chemyx_lab.analysis.scipy_peak_benchmark import (inventory_dataset, fixed_parity, fresh_output,
    export_trace_and_audit)
from chemyx_lab.analysis.scipy_peak_benchmark_plots import FigureWriter, individual, observation_comparison, dataset_figures
from chemyx_lab.analysis.target_peak_config import load_target_peak_config


@pytest.fixture
def spectrum():
    x = np.linspace(5.3, 6.3, 4001)
    y = 200 / (1 + ((x - 5.79) / .015) ** 2) + 2 * (x - 5.3)
    return {"ppm": x, "intensity": y, "regional_ppm": x, "regional_intensity": y,
            "dataset": "TEST DATA", "observation": "obs_01", "acquisition_time": "2026-06-08T11:22:33-04:00",
            "timestamp_source": "LONG DATE header", "source_file": "test.dx", "elapsed_hours": 0., "noise": 1.}


def test_fixed_exact_production_and_reversed_axis(spectrum):
    x, y = spectrum["ppm"], spectrum["intensity"]
    expected = integrate_above_local_baseline(x, y, left_ppm=5.7, right_ppm=5.9)
    assert integrate_interval(x, y, 5.7, 5.9, interpolate=False) == (expected.positive_area, expected.signed_area)
    assert integrate_interval(x[::-1], y[::-1], 5.7, 5.9, interpolate=False) == (expected.positive_area, expected.signed_area)


def test_interpolated_foot_line_is_linear_in_ppm(spectrum):
    x, y, baseline = integration_trace(spectrum["ppm"], spectrum["intensity"], 5.71713, 5.88791)
    assert x[0] == 5.71713 and x[-1] == 5.88791
    assert y[0] == np.interp(x[0], spectrum["ppm"], spectrum["intensity"])
    assert np.max(np.abs(baseline - np.interp(x, x[[0, -1]], y[[0, -1]]))) < 1e-10
    assert integrate_interval(spectrum["ppm"][::-1], spectrum["intensity"][::-1], x[0], x[-1]) == integrate_interval(spectrum["ppm"], spectrum["intensity"], x[0], x[-1])


def test_known_lorentzian_with_linear_baseline():
    x = np.linspace(5.3, 6.3, 100001)
    low, high, center, gamma, height = 5.69, 5.89, 5.79, .015, 100.
    signal = height / (1 + ((x - center) / gamma) ** 2)
    y = signal + 12 * x + 3
    analytic = height * gamma * (np.arctan((high - center) / gamma) - np.arctan((low - center) / gamma))
    # Endpoint-foot subtraction removes a known rectangle for symmetric feet.
    analytic -= (high - low) * height / (1 + ((low - center) / gamma) ** 2)
    actual, signed = integrate_interval(x, y, low, high)
    assert actual == pytest.approx(analytic, rel=1e-7)
    assert actual == pytest.approx(signed, abs=1e-10)


def test_failed_detection_never_zero_or_substitute(spectrum):
    cfg = replace(DetectionSettings(), expected_ppm=6.1, maximum_shift_ppm=.02)
    context = detection_context(spectrum["ppm"], spectrum["intensity"], 1., cfg)
    peak, status, flags = choose_target(context, cfg)
    assert peak is None and status == "missing"
    result = evaluate(context, peak, status, flags, "adaptive_noise", cfg, fixed_area=5.)
    assert np.isnan(result["area_dynamic"]) and not result["integration_valid"]


def test_target_tracks_nearest_not_tallest(spectrum):
    x = spectrum["ppm"]
    y = spectrum["intensity"] + 800 / (1 + ((x - 5.96) / .012) ** 2)
    cfg = replace(DetectionSettings(), maximum_shift_ppm=.2)
    context = detection_context(x, y, 1., cfg)
    peak, _, _ = choose_target(context, cfg)
    assert peak["center"] == pytest.approx(5.79, abs=.003)
    row = evaluate(context, peak, "success", [], "prominence_bases", cfg, fixed_area=1.)
    if row["boundary_high_ppm"] > 5.96:
        assert row["detection_status"] == "ambiguous"


@pytest.mark.parametrize("method", METHODS)
def test_figure_title_and_exact_shading_audit(tmp_path, spectrum, method):
    cfg = DetectionSettings()
    context = detection_context(spectrum["ppm"], spectrum["intensity"], 1., cfg)
    peak, status, flags = choose_target(context, cfg)
    row = evaluate(context, peak, status, flags, method, cfg, fixed_area=1.)
    row.update(dataset="TEST DATA", observation="obs_01", elapsed_hours=0., historical_presence="unlabelled")
    writer = FigureWriter(tmp_path)
    path = individual(writer, "TEST DATA", spectrum, row)
    with Image.open(path) as im:
        assert im.info["Title"].startswith("TEST DATA ")
        assert "TEST DATA TEST DATA" not in im.info["Title"]
        assert im.size[0] > 1000
        assert im.convert("L").getextrema()[0] < 100
    assert writer.manifest[0]["visible_title"].startswith("TEST DATA ")
    if row["integration_valid"]:
        assert export_trace_and_audit(tmp_path, spectrum, row)["absolute_error"] < 1e-9


def test_missing_diagnostic_title(tmp_path, spectrum):
    cfg = DetectionSettings()
    context = detection_context(spectrum["ppm"], spectrum["intensity"], 1., cfg)
    row = evaluate(context, None, "missing", ["no target"], "width_95", cfg, fixed_area=1.)
    writer = FigureWriter(tmp_path)
    path = individual(writer, "TEST DATA", spectrum, row)
    assert Path(path).is_file() and writer.manifest[0]["dataset"] == "TEST DATA"


def test_multiplot_paths_identify_dataset(tmp_path, spectrum):
    cfg = DetectionSettings()
    context = detection_context(spectrum["ppm"], spectrum["intensity"], 1., cfg)
    peak, status, flags = choose_target(context, cfg)
    rows = []
    for method in METHODS:
        row = evaluate(context, peak, status, flags, method, cfg, fixed_area=1.)
        row.update(elapsed_hours=0., observation="obs_01")
        rows.append(row)
    writer = FigureWriter(tmp_path)
    observation_comparison(writer, "TEST DATA", spectrum, rows)
    dataset_figures(writer, "TEST DATA", [spectrum], rows)
    assert len(writer.manifest) == 14
    for record in writer.manifest:
        assert record["visible_title"].startswith("TEST DATA ")
        with Image.open(record["file"]) as im:
            assert im.info["Title"] == record["visible_title"]


def test_production_report_parity(spectrum):
    from datetime import datetime
    spectrum.update(dataset="TEST DATA", timestamp=datetime(2026, 6, 8, 11, 22, 33))
    spectrum["fixed_area"], spectrum["fixed_signed_area"] = integrate_interval(spectrum["ppm"], spectrum["intensity"], 5.7, 5.9, interpolate=False)
    settings = load_target_peak_config({"enabled": True, "integration_window_ppm": [5.7, 5.9]})
    assert fixed_parity([spectrum], settings)[0]["positive_error"] == 0


def test_duplicate_exports_and_corrupt_primary_recovery(tmp_path):
    from chemyx_lab.analysis.nmr_validation import pipeline
    pipeline()
    fixture = Path(__file__).resolve().parents[1] / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"
    payload = fixture.read_bytes()
    (tmp_path / "one.dx").write_bytes(payload)
    (tmp_path / "one.rpc.dx").write_bytes(payload)
    (tmp_path / "copy.dx").write_bytes(payload)
    (tmp_path / "bad.dx").write_text("bad export")
    inventory, selected = inventory_dataset(tmp_path, "TEST DATA")
    assert len(selected) == 1
    assert sum(r["status"] == "duplicate_skipped" for r in inventory) == 2
    assert sum(r["status"] == "failed_inventory" for r in inventory) == 1
    assert selected[0]["timestamp"].year == 2026


def test_non_overwriting_output_and_complete_sweep(tmp_path):
    previous = tmp_path / "old.txt"
    previous.write_text("preserve")
    assert fresh_output(tmp_path).parent == tmp_path
    assert previous.read_text() == "preserve"
    profiles = parameter_sweep()
    assert len(profiles) == 17 and len({p.name for p in profiles}) == 17
    assert {p.prominence_snr for p in profiles} == {3., 5., 8., 12.}


def test_corrupt_primary_recovers_valid_rpc(tmp_path):
    from chemyx_lab.analysis.nmr_validation import pipeline
    pipeline()
    fixture = Path(__file__).resolve().parents[1] / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"
    (tmp_path / "recover.dx").write_text("incomplete primary export")
    (tmp_path / "recover.rpc.dx").write_bytes(fixture.read_bytes())
    inventory, selected = inventory_dataset(tmp_path, "TEST DATA")
    assert len(selected) == 1 and selected[0]["path"].name == "recover.rpc.dx"
    assert sum(r["status"] == "failed_inventory" for r in inventory) == 1


def test_missing_metadata_cannot_use_filename_or_mtime(tmp_path):
    import re
    from chemyx_lab.analysis.nmr_validation import pipeline
    pipeline()
    fixture = Path(__file__).resolve().parents[1] / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"
    payload = re.sub(rb"^##(?:LONG DATE|\$DATE)=.*$", b"", fixture.read_bytes(), flags=re.MULTILINE)
    (tmp_path / "sequence-1234.dx").write_bytes(payload)
    inventory, selected = inventory_dataset(tmp_path, "TEST DATA")
    assert not selected
    assert "No authoritative acquisition" in inventory[0]["note"]
