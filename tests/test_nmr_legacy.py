"""Regression tests for the isolated historical NMR compatibility path."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from chemyx_lab.analysis.nmr import (
    build_phased_spectrum,
    pick_spectrum_region,
)
from chemyx_lab.analysis.nmr_legacy import (
    legacy_local_integral_sum,
    legacy_select_reference,
    process_legacy,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
JUNE_9 = REPO_ROOT / "results" / "raw" / "nmr" / "06-09-26"


def _june_file(index=1):
    files = sorted(JUNE_9.glob("*.dx"))
    if len(files) <= index:
        pytest.skip("June 9 JCAMP regression data are unavailable")
    return files[index]


def test_legacy_reference_uses_first_array_order_peak_not_tallest_peak():
    ppm = np.linspace(0.0, 10.0, 101)
    spectrum = np.zeros(101, dtype=complex)
    spectrum[20] = 0.95
    spectrum[80] = 1.00

    result = legacy_select_reference(
        ppm,
        spectrum,
        assumed_reference_ppm=1.97,
    )

    assert list(result["peak_indices"]) == [20, 80]
    assert result["selected_index"] == 20
    assert result["candidate_ppm"] == pytest.approx(2.0)
    assert result["shift_ppm"] == pytest.approx(-0.03)


def test_legacy_reference_reports_no_candidate_below_threshold():
    ppm = np.linspace(0.0, 10.0, 101)
    spectrum = np.zeros(101, dtype=complex)
    spectrum[50] = 0.89

    result = legacy_select_reference(
        ppm,
        spectrum,
        assumed_reference_ppm=1.97,
    )

    assert result["selected_index"] is None
    assert result["candidate_ppm"] is None
    assert result["shift_ppm"] == 0.0


def test_legacy_point_sum_changes_with_sampling_but_true_integral_does_not():
    results = []
    for points in (8192, 16384, 32768, 65536):
        ppm = np.linspace(5.6, 6.0, points)
        peak = np.exp(-0.5 * ((ppm - 5.79) / 0.02) ** 2)
        results.append(
            legacy_local_integral_sum(
                ppm,
                peak,
                5.69,
                5.89,
                observe_frequency_mhz=60.550825,
            )
        )

    assert results[-1]["area_sum_points"] > 7.9 * results[0]["area_sum_points"]
    ppm_areas = [item["area_trapezoid_ppm"] for item in results]
    assert max(ppm_areas) == pytest.approx(min(ppm_areas), rel=3e-5)
    assert results[-1]["area_trapezoid_hz"] == pytest.approx(
        results[-1]["area_trapezoid_ppm"] * 60.550825
    )


def test_exact_and_intended_legacy_fft_paths_are_not_identical():
    exact = process_legacy(_june_file(), intended_fft=False)
    intended = process_legacy(_june_file(), intended_fft=True)

    assert exact.fft_input == "original_fid"
    assert intended.fft_input == "windowed_and_exponential_fid"
    assert not np.allclose(exact.spectrum, intended.spectrum)


def test_june_9_legacy_shift_regression_does_not_move_5p79_to_6p1():
    path = _june_file()
    exact = process_legacy(path, assumed_reference_ppm=1.97)
    legacy_peaks = pick_spectrum_region(
        exact.original_ppm,
        np.abs(exact.spectrum),
        quantitative_intensity=exact.spectrum.real,
        region_min_ppm=5.0,
        region_max_ppm=6.5,
        min_prominence_snr=3.0,
    )
    legacy_candidate = max(
        (
            peak
            for peak in legacy_peaks.peaks
            if abs(peak.interpolated_ppm - 5.79) < 0.20
        ),
        key=lambda peak: peak.prominence,
    )
    current = build_phased_spectrum(path)
    current_peaks = pick_spectrum_region(
        current.ppm_axis,
        current.magnitude,
        quantitative_intensity=current.real,
        region_min_ppm=5.0,
        region_max_ppm=6.5,
        min_prominence_snr=5.0,
    )
    current_candidate = max(
        current_peaks.peaks,
        key=lambda peak: peak.prominence,
    )

    assert exact.raw_reference_candidate_ppm == pytest.approx(
        2.080226530, abs=2e-6
    )
    assert exact.applied_reference_shift_ppm == pytest.approx(
        -0.110226530, abs=2e-6
    )
    assert exact.applied_reference_shift_hz == pytest.approx(-6.674306, abs=2e-4)
    assert legacy_candidate.interpolated_ppm == pytest.approx(5.77519, abs=0.002)
    assert (
        legacy_candidate.interpolated_ppm + exact.applied_reference_shift_ppm
    ) == pytest.approx(5.66496, abs=0.002)
    assert current_candidate.interpolated_ppm == pytest.approx(5.77695, abs=0.002)
