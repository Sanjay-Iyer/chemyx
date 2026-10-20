"""Primary dynamic contract, no fallback, interpolation and title invariants."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import csv
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
import yaml

from chemyx_lab.analysis import stage2_scipy_processing as processing
from chemyx_lab.analysis.nmr import _trapezoid
from chemyx_lab.analysis.scipy_peak_comparison import detection_context, choose_target, evaluate, integrate_interval


@pytest.fixture
def spectrum(tmp_path):
    x = np.linspace(5.3, 6.3, 4001)
    y = 200 / (1 + ((x - 5.79) / .015) ** 2)
    fixed = integrate_interval(x, y, 5.7, 5.9, interpolate=False)[0]
    return {"ppm": x, "intensity": y, "noise": 1., "fixed_area": fixed,
        "fixed_signed_area": fixed, "path": tmp_path / "synthetic_unit_test.dx", "raw_sha256": "unit-test",
        "timestamp": datetime(2026, 10, 19, tzinfo=timezone.utc), "timestamp_source": "LONG DATE header",
        "acquisition_time": "2026-10-19T00:00:00+00:00", "spectrum": SimpleNamespace(phase_audit={"method": "acme"})}


def measurements(tmp_path, spectra, settings=None):
    out = tmp_path / "processed"
    (out / "processed_spectra").mkdir(parents=True)
    settings = settings or processing.load_profile()[0]
    return processing.dynamic_measurements(spectra, settings, out), out


def test_primary_is_exact_benchmarked_width95_and_fixed_is_only_comparator(tmp_path, spectrum):
    settings, _ = processing.load_profile()
    context = detection_context(spectrum["ppm"], spectrum["intensity"], spectrum["noise"], settings)
    peak, status, flags = choose_target(context, settings)
    expected = evaluate(context, peak, status, flags, "width_95", settings, fixed_area=spectrum["fixed_area"])
    rows, out = measurements(tmp_path, [spectrum], settings)
    row = rows[0]
    assert row["area"] == expected["area_dynamic"]
    assert row["area"] != row["area_fixed"]
    assert row["boundary_low_ppm"] == expected["boundary_low_ppm"]
    coordinates = np.loadtxt(row["integration_coordinates_csv"], delimiter=",", skiprows=1)
    assert _trapezoid(np, coordinates[:, 3], coordinates[:, 0]) == pytest.approx(row["area"], abs=1e-10)
    assert row["primary_area_method"] == "scipy_width95_prominence8"


def test_supported_numpy_1_trapz_api_keeps_primary_area_and_coordinate_audit(tmp_path, spectrum, monkeypatch):
    settings, _ = processing.load_profile()
    context = detection_context(spectrum["ppm"], spectrum["intensity"], spectrum["noise"], settings)
    peak, status, flags = choose_target(context, settings)
    expected = evaluate(context, peak, status, flags, "width_95", settings, fixed_area=spectrum["fixed_area"])
    # NumPy 1.24, allowed by this repo, exposes trapz but not trapezoid.
    trapezoid = getattr(np, "trapezoid", np.__dict__.get("trapz"))
    monkeypatch.setattr(np, "trapz", trapezoid, raising=False)
    monkeypatch.delattr(np, "trapezoid", raising=False)
    rows, _ = measurements(tmp_path, [spectrum], settings)
    assert rows[0]["area"] == expected["area_dynamic"]
    assert rows[0]["signed_area"] == expected["area_signed"]
    assert Path(rows[0]["integration_coordinates_csv"]).is_file()


def test_missing_peak_has_missing_area_never_fixed_or_zero(tmp_path, spectrum):
    spectrum["intensity"] *= .001
    rows, _ = measurements(tmp_path, [spectrum])
    assert rows[0]["detection_status"] == "missing"
    assert math.isnan(rows[0]["area"]) and not rows[0]["analysis_quality_pass"]
    assert rows[0]["area_fixed"] > 0


def test_excessive_boundaries_are_diagnostic_and_invalid_not_silently_replaced(tmp_path, spectrum):
    settings = replace(processing.load_profile()[0], maximum_boundary_half_span_ppm=.001)
    rows, _ = measurements(tmp_path, [spectrum], settings)
    row = rows[0]
    assert row["detection_status"] == "unreliable_boundary"
    assert math.isnan(row["area"]) and row["candidate_area"] > 0
    assert not row["analysis_quality_pass"]


@pytest.mark.parametrize("field,value", [("prominence_snr", 3), ("rel_height", .90), ("distance_ppm", -1), ("smoothing_ppm", float("nan"))])
def test_invalid_profile_is_rejected_in_analysis_only(tmp_path, field, value):
    raw = yaml.safe_load(processing.DEFAULT_PROFILE.read_text())
    if field == "rel_height":
        raw[field] = value
    else:
        raw["detection"][field] = value
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        processing.load_profile(path)


def test_missing_metadata_cannot_use_filename_or_mtime():
    # Initialize the production parser path, then exercise metadata-only timing.
    processing.pipeline()
    with pytest.raises(ValueError, match="No authoritative"):
        processing.authoritative_time({})
    stamp, source = processing.authoritative_time({"LONG DATE": "2026/06/09 10:07:43-0400"})
    assert source == "LONG DATE header" and stamp.isoformat() == "2026-06-09T10:07:43-04:00"


def test_all_new_plot_paths_have_same_dataset_title_across_formats(tmp_path, spectrum):
    from chemyx_lab.analysis.stage2_scipy_plots import render
    rows, out = measurements(tmp_path, [spectrum])
    config = {"target_peak": {"figures": {"formats": ["png", "svg", "pdf"], "dpi": 80}}}
    manifest = render([spectrum], rows, [], [], " TEST  DATA ", config, out)
    assert len(manifest) == 27
    grouped = {}
    for row in manifest:
        assert row["visible_title"].startswith("TEST DATA ")
        assert "TEST DATA TEST DATA" not in row["visible_title"]
        grouped.setdefault(Path(row["figure"]).with_suffix(""), set()).add(row["visible_title"])
        if row["format"] == "png":
            with Image.open(row["figure"]) as im:
                assert im.info["Title"] == row["visible_title"]
        elif row["format"] == "svg":
            assert row["visible_title"] in Path(row["figure"]).read_text()
        elif row["format"] == "pdf":
            assert b"/Title (" + row["visible_title"].encode("ascii") + b")" in Path(row["figure"]).read_bytes()
    assert all(len(titles) == 1 for titles in grouped.values())


def test_primary_rates_use_dynamic_areas_and_quality_flags(tmp_path, spectrum):
    second = deepcopy(spectrum)
    second["intensity"] *= 2
    second["timestamp"] = datetime(2026, 10, 19, 1, tzinfo=timezone.utc)
    rows, out = measurements(tmp_path, [spectrum, second])
    rates, normalized, info = processing.dynamic_statistics(rows, {"statistics": {"kinetics": {"enabled": False}}}, out)
    assert rates[1]["absolute_rate_per_hour"] == pytest.approx(rows[1]["area"] - rows[0]["area"])
    assert all(r["primary_area_method"] == "scipy_width95_prominence8" for r in rates + normalized)
    payload = json.loads((out / "statistics/dynamic_plateau_review.json").read_text())
    assert payload["analysis_controls_run"] is False
