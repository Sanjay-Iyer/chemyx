from __future__ import annotations

import csv
import hashlib
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

from chemyx_lab.analysis.nmr import (
    build_phased_spectrum,
    build_processing_inspection,
    pick_spectrum_region,
)


ROOT = Path(__file__).resolve().parents[1]
DX = (
    ROOT
    / "results/runs/automated/chemyx_demo_081026_v3"
    / "20260810_154505_si6/raw_nmr"
    / "20260810_154822_081626_phsi4_0001_8scan_gain12.dx"
)
SAVED = (
    ROOT
    / "results/runs/automated/chemyx_demo_081026_v3"
    / "20260810_154505_si6/processed_nmr"
    / "081626_phsi4_20260810_154822_full_spectrum"
    / "081626_phsi4_20260810_154822_full_spectrum_peaks_simple.csv"
)


def _script_module():
    path = ROOT / "scripts/nmr/inspect_processing.py"
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("inspect_processing", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inspection_reuses_production_phase_and_baseline_subtraction():
    inspection = build_processing_inspection(
        DX, line_broadening_hz=0.03, zero_fill_points=65536
    )
    production = build_phased_spectrum(
        DX,
        line_broadening_hz=0.03,
        zero_fill_points=65536,
        phase_method="stored",
        inverse_phase=True,
    )
    assert inspection.processed_points == 65536
    assert inspection.line_broadening_hz == 0.03
    assert inspection.phase0_deg == production.phase0_deg
    assert inspection.phase1_deg == production.phase1_deg
    assert inspection.inverse_phase is True
    assert inspection.als_smoothness == 1e6
    assert inspection.als_asymmetry == 0.001
    assert inspection.als_iterations == 10
    np.testing.assert_allclose(inspection.phased_spectrum, production.real + 1j * production.imaginary)
    np.testing.assert_allclose(
        np.real(inspection.phased_spectrum) - inspection.als_baseline,
        inspection.corrected_real,
        rtol=0,
        atol=1e-10,
    )


def test_known_file_reconstructs_saved_quantitative_result_without_mutation():
    before = _sha256(SAVED)
    module = _script_module()
    acq = module._load_acquisition(DX.parents[1])
    assert acq.timestamp.isoformat() == "2026-08-10T15:57:37"
    assert acq.timestamp_source == "LONG DATE header"
    assert acq.target is not None
    with SAVED.open(newline="", encoding="utf-8") as handle:
        saved = next(csv.DictReader(handle))
    assert acq.target.interpolated_ppm == pytest.approx(float(saved["peak_ppm"]), abs=0.0005)
    assert acq.target.positive_area == pytest.approx(float(saved["integrated_area"]), abs=0.005)
    assert acq.target.peak_height == pytest.approx(float(saved["intensity"]), abs=0.01)
    assert acq.target.snr == pytest.approx(float(saved["snr"]), abs=0.01)
    assert acq.target.width_ppm * module._observe_frequency(acq.inspection.metadata) == pytest.approx(float(saved["width_hz"]), abs=0.01)
    assert acq.fixed_product.positive_area == pytest.approx(6.07, abs=0.01)
    assert acq.left_peak["area"] == pytest.approx(2.81, abs=0.01)
    assert _sha256(SAVED) == before


def test_diagnostic_peak_pick_does_not_change_arrays_or_metrics():
    inspection = build_processing_inspection(
        DX, line_broadening_hz=0.03, zero_fill_points=65536
    )
    before = np.asarray(inspection.corrected_real).copy()
    first = pick_spectrum_region(
        inspection.ppm_axis,
        inspection.corrected_real,
        region_min_ppm=5.0,
        region_max_ppm=6.5,
        min_prominence_snr=5.0,
        min_distance_ppm=0.04,
        min_width_ppm=0.015,
        baseline_polynomial_order=3,
        smoothing_window_ppm=0.006,
        quantitative_intensity=inspection.corrected_real,
    )
    second = pick_spectrum_region(
        inspection.ppm_axis,
        inspection.corrected_real,
        region_min_ppm=5.0,
        region_max_ppm=6.5,
        min_prominence_snr=5.0,
        min_distance_ppm=0.04,
        min_width_ppm=0.015,
        baseline_polynomial_order=3,
        smoothing_window_ppm=0.006,
        quantitative_intensity=inspection.corrected_real,
    )
    np.testing.assert_array_equal(inspection.corrected_real, before)
    assert first.peaks == second.peaks


def test_dataset_titles_are_visible_and_sensitivity_output_is_isolated(tmp_path):
    module = _script_module()
    title = module._title("081026 PhSi4 automated demo v3", "Peak Area Over Time")
    assert title.startswith("081026 PhSi4 automated demo v3 ")
    production_parent = DX.parents[2]
    assert tmp_path.resolve() != production_parent.resolve()


def test_reference_stage_preserves_intensity_and_metadata_axis():
    inspection = build_processing_inspection(
        DX, line_broadening_hz=0.03, zero_fill_points=65536
    )
    before_ppm = np.asarray(inspection.ppm_axis)
    before_intensity = np.real(inspection.phased_spectrum)
    # Production reference_method=metadata performs no shift for this dataset.
    after_ppm = before_ppm.copy()
    after_intensity = before_intensity.copy()
    np.testing.assert_array_equal(after_ppm, before_ppm)
    np.testing.assert_array_equal(after_intensity, before_intensity)


def test_left_line_fill_matches_reported_local_baseline_integral():
    module = _script_module()
    acq = module._load_acquisition(DX.parents[1])
    assert acq.left_peak is not None
    from build_timeseries_results import local_integration_fill
    x, _, _, positive = local_integration_fill(
        np.asarray(acq.inspection.ppm_axis),
        np.asarray(acq.inspection.corrected_real),
        acq.left_peak["from_ppm"],
        acq.left_peak["to_ppm"],
    )
    plotted_area = float(np.trapezoid(positive, x))
    assert plotted_area == pytest.approx(acq.left_peak["area"], abs=1e-10)
