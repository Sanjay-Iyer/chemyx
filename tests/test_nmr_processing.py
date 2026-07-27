"""Processing-level tests for NMReady JCAMP-DX FIDs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from chemyx_lab.analysis.nmr import (
    NmrProcessingError,
    analyze_dx_peak,
    align_validated_reference,
    align_solvent_axis,
    apply_reference_shift,
    automatic_baseline_points,
    build_ppm_axis,
    build_magnitude_spectrum,
    build_phased_spectrum,
    estimate_local_baseline,
    evaluate_reference_model,
    exponential_window,
    fourier_transform_fid,
    half_cosine_truncation_window,
    integrate_above_local_baseline,
    pick_dx_region_peaks,
    pick_spectrum_region,
    read_jcamp_fid,
    read_jcamp_fid_custom,
    subtract_abd_polynomial_baseline,
    track_peak_families,
    validate_axis_metadata,
    validate_jcamp_decoders,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = REPO_ROOT / "results" / "raw" / "nmr" / "06-08-26"
TEMPLATE_DIR = REPO_ROOT / "results" / "raw" / "nmr" / "06-09-26"


def _first_dx(directory: Path) -> Path:
    files = sorted(directory.glob("*.dx"))
    if not files:
        pytest.skip(f"No JCAMP-DX test data in {directory}")
    return files[0]


def test_jcamp_reader_combines_scaled_real_and_imaginary_pages():
    fid = read_jcamp_fid(_first_dx(REFERENCE_DIR))

    assert len(fid.real) == 8192
    assert len(fid.imag) == 8192
    assert np.iscomplexobj(np.asarray(fid.complex_points))

    # The encoded page contains int32-like values near 2.1e9. nmrglue must
    # apply the JCAMP FACTOR, yielding the physical FIRST value near 1.2e3.
    first_values = [float(value) for value in fid.metadata["FIRST"].split(",")]
    assert fid.real[0] == pytest.approx(first_values[1], abs=1e-6)
    assert fid.imag[0] == pytest.approx(first_values[2], abs=1e-6)


def test_independent_jcamp_decoder_matches_nmrglue_exactly():
    path = _first_dx(TEMPLATE_DIR)
    custom = read_jcamp_fid_custom(path)
    standard = read_jcamp_fid(path)

    assert validate_jcamp_decoders(path) == pytest.approx(0.0, abs=1e-12)
    assert custom.real == pytest.approx(standard.real, abs=1e-12)
    assert custom.imag == pytest.approx(standard.imag, abs=1e-12)


def test_axis_metadata_uses_complex_dwell_not_internal_digitizer_dwell():
    validation = validate_axis_metadata(_first_dx(TEMPLATE_DIR))

    assert validation.complex_points == 8192
    assert validation.dwell_time_s == pytest.approx(0.0008, rel=1e-7)
    assert validation.spectral_width_hz == pytest.approx(
        1.0 / validation.dwell_time_s, rel=1e-7
    )
    assert validation.acquisition_time_s == pytest.approx(
        validation.metadata_acquisition_time_s, abs=1e-6
    )
    assert validation.fft_points == 65536
    assert validation.frequency_spacing_hz == pytest.approx(0.0190735, rel=1e-5)
    assert validation.ppm_spacing == pytest.approx(0.0003150, rel=1e-4)
    assert validation.left_limit_ppm > validation.center_ppm
    assert validation.right_limit_ppm < validation.center_ppm


def test_ppm_axis_is_ascending_and_centered():
    axis = build_ppm_axis(
        65536,
        spectral_width_hz=1250.000031577657,
        observe_frequency_mhz=60.55065073950193,
        center_ppm=5.0,
    )

    assert np.all(np.diff(axis) > 0)
    assert axis[32768] == pytest.approx(5.0, abs=1e-12)
    assert axis[-1] > 15.32
    assert axis[0] == pytest.approx(-5.32194, abs=2e-5)


def test_explicit_reference_shift_recovers_known_0p31_ppm_offset():
    original = np.asarray([5.79, 6.10, 6.99])
    referenced, shift = apply_reference_shift(
        original,
        observed_reference_ppm=6.69,
        expected_reference_ppm=7.00,
    )

    assert shift == pytest.approx(0.31)
    assert referenced == pytest.approx([6.10, 6.41, 7.30])


def _synthetic_toluene_reference_spectrum(
    *,
    methyl_ppm=2.03,
    aromatic_ppm=6.94,
):
    ppm = np.linspace(1.5, 7.5, 30001)
    target = 120 * np.exp(-0.5 * ((ppm - 5.78) / 0.025) ** 2)
    methyl = 1000 * np.exp(-0.5 * ((ppm - methyl_ppm) / 0.018) ** 2)
    aromatic = 700 * np.exp(-0.5 * ((ppm - aromatic_ppm) / 0.030) ** 2)
    rng = np.random.default_rng(812)
    return ppm, target + methyl + aromatic + rng.normal(0.0, 0.05, ppm.size)


def test_multi_region_reference_model_recovers_known_uniform_offset():
    ppm, intensity = _synthetic_toluene_reference_spectrum()

    result = evaluate_reference_model(
        ppm,
        intensity,
        reference_model="protonated_toluene_low_field_neat",
        observe_frequency_mhz=60.550825,
        solvent_identity="protonated_toluene",
        solvent_isotopic_form="h8",
    )

    assert result.reference_qc_pass
    assert result.reference_region_count == 2
    assert result.reference_region_agreement < 0.001
    assert result.proposed_shift_ppm == pytest.approx(0.06, abs=0.001)
    assert result.applied_shift_ppm == pytest.approx(0.06, abs=0.001)
    assert result.applied_shift_hz == pytest.approx(
        0.06 * 60.550825, abs=0.07
    )


def test_reference_model_fails_closed_for_unknown_or_wrong_identity():
    ppm, intensity = _synthetic_toluene_reference_spectrum()

    result = evaluate_reference_model(
        ppm,
        intensity,
        reference_model="protonated_toluene_low_field_neat",
        observe_frequency_mhz=60.550825,
    )

    assert not result.reference_qc_pass
    assert result.proposed_shift_ppm == pytest.approx(0.06, abs=0.001)
    assert result.applied_shift_ppm == 0.0
    assert result.referenced_ppm == pytest.approx(ppm)
    assert "solvent identity" in "; ".join(result.reference_qc_failure_reasons)


def test_reference_model_rejects_missing_or_disagreeing_regions():
    ppm, full = _synthetic_toluene_reference_spectrum(
        methyl_ppm=2.00,
        aromatic_ppm=7.00,
    )
    methyl_only = 1000 * np.exp(-0.5 * ((ppm - 2.03) / 0.018) ** 2)
    identity = {
        "reference_model": "protonated_toluene_low_field_neat",
        "observe_frequency_mhz": 60.550825,
        "solvent_identity": "protonated_toluene",
        "solvent_isotopic_form": "h8",
    }

    missing = evaluate_reference_model(ppm, methyl_only, **identity)
    disagreeing = evaluate_reference_model(ppm, full, **identity)

    assert not missing.reference_qc_pass
    assert missing.reference_region_count == 1
    assert missing.applied_shift_ppm == 0.0
    assert not disagreeing.reference_qc_pass
    assert disagreeing.reference_region_agreement > 0.05
    assert disagreeing.applied_shift_ppm == 0.0


def test_uniform_reference_shift_preserves_intensity_and_peak_separations():
    ppm, intensity = _synthetic_toluene_reference_spectrum()
    original_intensity = intensity.copy()
    shifted, shift = apply_reference_shift(
        ppm,
        observed_reference_ppm=5.78,
        expected_reference_ppm=6.10,
    )
    original_peaks = np.asarray([2.08, 5.78, 6.99])
    shifted_peaks = original_peaks + shift

    assert shift == pytest.approx(0.32)
    assert shifted[np.argmax(intensity)] - ppm[np.argmax(intensity)] == (
        pytest.approx(0.32)
    )
    assert np.diff(shifted_peaks) == pytest.approx(np.diff(original_peaks))
    assert intensity == pytest.approx(original_intensity)


def test_wrong_reference_conditions_are_rejected_by_region_disagreement():
    ppm, intensity = _synthetic_toluene_reference_spectrum(
        methyl_ppm=2.08,
        aromatic_ppm=6.99,
    )

    result = evaluate_reference_model(
        ppm,
        intensity,
        reference_model="protonated_toluene_dilute_cdcl3",
        observe_frequency_mhz=60.550825,
        solvent_identity="protonated_toluene",
        solvent_isotopic_form="h8",
        maximum_reference_disagreement_ppm=0.05,
    )

    assert not result.reference_qc_pass
    assert result.reference_region_agreement > 0.05
    assert result.applied_shift_ppm == 0.0


def test_fft_and_real_imaginary_channel_order_have_expected_sign():
    points = 1024
    sweep_hz = 1000.0
    frequency_hz = 125.0
    time_s = np.arange(points) / sweep_hz
    fid = np.exp(2j * np.pi * frequency_hz * time_s)
    frequency_axis = np.fft.fftshift(np.fft.fftfreq(points, d=1 / sweep_hz))

    correct_frequency = frequency_axis[np.argmax(np.abs(fourier_transform_fid(fid)))]
    swapped = fid.imag + 1j * fid.real
    swapped_frequency = frequency_axis[
        np.argmax(np.abs(fourier_transform_fid(swapped)))
    ]

    assert correct_frequency == pytest.approx(125.0)
    assert swapped_frequency == pytest.approx(-125.0)


def test_exponential_apodization_formula_uses_seconds_and_hertz():
    window = exponential_window(
        3,
        dwell_time_s=0.0008,
        line_broadening_hz=0.5,
    )

    assert window == pytest.approx(
        np.exp(-np.pi * 0.5 * np.asarray([0.0, 0.0008, 0.0016]))
    )


def test_half_cosine_truncation_window_ends_at_zero():
    window = half_cosine_truncation_window(8192)

    assert window[0] == pytest.approx(1.0, abs=1e-7)
    assert window[-1] == pytest.approx(0.0, abs=1e-15)
    assert np.all(np.diff(window) < 0)


def test_abd_linear_baseline_removes_slope_without_erasing_peak():
    x = np.arange(8192)
    baseline = 100 + 0.02 * x
    peak = 300 * np.exp(-0.5 * ((x - 4200) / 35) ** 2)
    rng = np.random.default_rng(91)
    values = baseline + peak + rng.normal(0, 1, x.size)

    coordinates, _, noise = automatic_baseline_points(values)
    corrected, fitted, _, _ = subtract_abd_polynomial_baseline(values)

    assert coordinates.size > 100
    assert noise > 0
    assert np.corrcoef(fitted, baseline)[0, 1] > 0.999
    assert corrected[4200] > 290


def test_variable_width_local_feet_integration_is_orientation_safe():
    ppm = np.linspace(5.6, 6.0, 4001)
    baseline = 50 + 30 * (ppm - 5.6)
    peak = 200 * np.exp(-0.5 * ((ppm - 5.79) / 0.02) ** 2)

    forward = integrate_above_local_baseline(
        ppm,
        baseline + peak,
        left_ppm=5.69,
        right_ppm=5.89,
    )
    reverse = integrate_above_local_baseline(
        ppm[::-1],
        (baseline + peak)[::-1],
        left_ppm=5.89,
        right_ppm=5.69,
    )

    assert forward.signed_area > 0
    assert reverse.signed_area == pytest.approx(forward.signed_area, rel=1e-12)
    assert forward.positive_area == pytest.approx(forward.signed_area, rel=1e-5)


def test_windowed_solvent_alignment_recovers_offset_and_cross_checks_reference():
    ppm = np.linspace(1.5, 7.5, 24000)
    observed_methyl = 1.78
    observed_aromatic = 6.69
    intensity = (
        1000 * np.exp(-0.5 * ((ppm - observed_methyl) / 0.015) ** 2)
        + 700 * np.exp(-0.5 * ((ppm - observed_aromatic) / 0.025) ** 2)
    )

    alignment = align_solvent_axis(
        ppm,
        intensity,
        solvent="toluene",
        resonance="methyl",
        validation_resonance="aromatic",
    )

    assert alignment.applied_shift_ppm == pytest.approx(0.31, abs=0.001)
    assert alignment.validation_shift_ppm == pytest.approx(0.31, abs=0.001)
    assert alignment.shift_disagreement_ppm < 0.002
    assert alignment.reference_confidence == "high"
    assert alignment.referenced_ppm[
        np.argmin(np.abs(ppm - observed_methyl))
    ] == pytest.approx(2.09, abs=0.001)


def test_validated_reference_rejects_absent_and_implausibly_shifted_peaks():
    ppm = np.linspace(1.5, 2.5, 10001)
    noise_only = np.zeros_like(ppm)
    common = {
        "expected_ppm": 1.97,
        "search_window_ppm": 0.40,
        "minimum_snr": 5.0,
        "minimum_prominence_snr": 5.0,
        "minimum_width_ppm": 0.002,
        "maximum_width_ppm": 0.10,
        "maximum_shift_ppm": 0.05,
    }

    with pytest.raises(NmrProcessingError, match="reference_qc failed"):
        align_validated_reference(ppm, noise_only, **common)

    shifted_peak = 100 * np.exp(-0.5 * ((ppm - 2.08) / 0.01) ** 2)
    with pytest.raises(NmrProcessingError, match="required shift"):
        align_validated_reference(ppm, shifted_peak, **common)


def test_validated_reference_accepts_restricted_high_quality_peak():
    ppm = np.linspace(1.5, 2.5, 10001)
    rng = np.random.default_rng(22)
    intensity = (
        100 * np.exp(-0.5 * ((ppm - 1.95) / 0.01) ** 2)
        + rng.normal(0.0, 0.1, ppm.size)
    )
    result = align_validated_reference(
        ppm,
        intensity,
        expected_ppm=1.97,
        search_window_ppm=0.20,
        minimum_snr=20.0,
        minimum_prominence_snr=20.0,
        minimum_width_ppm=0.005,
        maximum_width_ppm=0.05,
        maximum_shift_ppm=0.05,
        observe_frequency_mhz=60.550825,
    )

    assert result.reference_qc
    assert result.applied_shift_ppm == pytest.approx(0.02, abs=5e-4)
    assert result.applied_shift_hz == pytest.approx(1.2110165, abs=0.02)
    assert result.reference_peak_snr > 20


def test_processing_uses_file_zero_fill_line_broadening_and_ppm_metadata():
    path = _first_dx(REFERENCE_DIR)
    spectrum = build_magnitude_spectrum(path)

    assert spectrum.processed_points == int(float(spectrum.metadata["$SI"]))
    assert spectrum.line_broadening_hz == pytest.approx(
        float(spectrum.metadata["$LB"])
    )
    assert len(spectrum.ppm_axis) == spectrum.processed_points
    assert len(spectrum.magnitude) == spectrum.processed_points

    # Bruker's OFFSET is the high-ppm edge; one digital point of tolerance
    # accounts for the endpoint-excluded FFT grid.
    ppm_step = float(np.median(np.diff(spectrum.ppm_axis)))
    assert float(np.max(spectrum.ppm_axis)) == pytest.approx(
        float(spectrum.metadata["$OFFSET"]),
        abs=abs(ppm_step) * 2,
    )


def test_phased_spectrum_uses_stored_phc_values():
    spectrum = build_phased_spectrum(_first_dx(REFERENCE_DIR))

    assert spectrum.phase0_deg == pytest.approx(float(spectrum.metadata["$PHC0"]))
    assert spectrum.phase1_deg == pytest.approx(float(spectrum.metadata["$PHC1"]))
    assert spectrum.real is not None
    assert spectrum.imaginary is not None
    assert len(spectrum.real) == spectrum.processed_points


def test_local_baseline_recovers_peak_on_curved_background():
    ppm = np.linspace(5.4, 6.8, 8000)
    background = 400.0 + 120.0 * (ppm - 6.1) + 55.0 * (ppm - 6.1) ** 2
    peak = 250.0 * np.exp(-0.5 * ((ppm - 6.1) / 0.012) ** 2)
    rng = np.random.default_rng(7)
    intensity = background + peak + rng.normal(0.0, 2.0, ppm.size)

    baseline, noise = estimate_local_baseline(
        ppm,
        intensity,
        target_ppm=6.1,
        detection_window_ppm=0.12,
        baseline_window_ppm=0.5,
        polynomial_order=2,
    )
    target_index = int(np.argmin(np.abs(ppm - 6.1)))

    assert noise == pytest.approx(2.0, rel=0.2)
    assert intensity[target_index] - baseline[target_index] > 200.0


def test_region_picker_finds_both_peaks_and_rejects_curved_baseline():
    ppm = np.linspace(5.0, 6.5, 12000)
    background = 500 + 300 * (ppm - 5.7) + 180 * (ppm - 5.7) ** 2
    peak_579 = 240 * np.exp(-0.5 * ((ppm - 5.79) / 0.014) ** 2)
    peak_610 = 190 * np.exp(-0.5 * ((ppm - 6.10) / 0.018) ** 2)
    rng = np.random.default_rng(31)
    intensity = background + peak_579 + peak_610 + rng.normal(0, 2, ppm.size)

    result = pick_spectrum_region(ppm, intensity)

    assert len(result.peaks) == 2
    assert [peak.peak_ppm for peak in result.peaks] == pytest.approx(
        [6.10, 5.79], abs=0.002
    )
    assert all(peak.signed_area > 0 for peak in result.peaks)
    assert all(peak.classification == "resolved_peak" for peak in result.peaks)


def test_region_picker_does_not_create_peak_from_solvent_tail():
    ppm = np.linspace(5.0, 6.5, 12000)
    tail = 2000 * np.exp(1.1 * (ppm - 6.5))
    rng = np.random.default_rng(11)

    result = pick_spectrum_region(ppm, tail + rng.normal(0, 2, ppm.size))

    assert result.peaks == ()


def test_peak_tracking_uses_position_and_width_continuity():
    def picked(center, width):
        ppm = np.linspace(5.0, 6.5, 12000)
        signal = 300 * np.exp(-0.5 * ((ppm - center) / width) ** 2)
        return pick_spectrum_region(ppm, signal + 1e-3).peaks

    peak_sets = [(), picked(5.790, 0.014), picked(5.795, 0.014), picked(6.10, 0.02)]
    assignments, families = track_peak_families(peak_sets)

    assert assignments[1][0] == assignments[2][0]
    assert assignments[3][0] != assignments[2][0]
    assert families[0].observations == 2
    assert families[0].reproducible


def test_process_fid_cli_writes_phase_corrected_artifacts(tmp_path):
    script = REPO_ROOT / "scripts" / "nmr" / "process_fid.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(_first_dx(REFERENCE_DIR)),
            "--output-dir",
            str(tmp_path),
            "--run-name",
            "smoke",
            "--zero-fill-points",
            "8192",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    run_dir = tmp_path / "smoke"
    assert (run_dir / "results.csv").is_file()
    assert (run_dir / "peaks.csv").is_file()
    assert (run_dir / "peak_families.csv").is_file()
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "plots" / "overlay_region_corrected.png").is_file()
    assert (run_dir / "plots" / "stacked_region_corrected.png").is_file()
    assert len(list((run_dir / "plots" / "full").glob("*.png"))) == 1
    assert len(list((run_dir / "plots" / "region").glob("*.png"))) == 1


@pytest.mark.skipif(
    not TEMPLATE_DIR.exists(),
    reason="06-09-26 template dataset is local validation data",
)
def test_06_09_template_rejects_6p1_noise_and_finds_5p79_feature():
    files = sorted(TEMPLATE_DIR.glob("*.dx"))
    assert len(files) == 8

    target_6p1 = []
    candidate_5p79 = []
    for path in files:
        try:
            target_6p1.append(
                analyze_dx_peak(path, target_ppm=6.1, min_prominence_snr=3.0)
            )
        except NmrProcessingError:
            pass
        try:
            candidate_5p79.append(
                analyze_dx_peak(path, target_ppm=5.79, min_prominence_snr=3.0)
            )
        except NmrProcessingError:
            pass

    assert target_6p1 == []
    assert len(candidate_5p79) >= 6
    assert all(abs(result.peak_ppm - 5.79) <= 0.03 for result in candidate_5p79)


@pytest.mark.skipif(
    not TEMPLATE_DIR.exists(),
    reason="06-09-26 template dataset is local validation data",
)
def test_06_09_regional_picker_finds_one_reproducible_family():
    files = sorted(TEMPLATE_DIR.glob("*.dx"))
    peak_sets = [pick_dx_region_peaks(path).peaks for path in files]
    assignments, families = track_peak_families(peak_sets)

    assert [len(peaks) for peaks in peak_sets] == [0, 1, 1, 1, 1, 1, 1, 1]
    assert len(families) == 1
    assert families[0].observations == 7
    assert families[0].median_ppm == pytest.approx(5.7853, abs=0.003)
    assert len({family for row in assignments for family in row}) == 1


@pytest.mark.skipif(
    not TEMPLATE_DIR.exists(),
    reason="06-09-26 template dataset is local validation data",
)
def test_peak_area_is_stable_across_reasonable_zero_fill_sizes():
    path = sorted(TEMPLATE_DIR.glob("*.dx"))[3]
    results = [
        pick_dx_region_peaks(path, zero_fill_points=points).peaks[0]
        for points in (8192, 16384, 65536)
    ]
    areas = np.asarray([peak.signed_area for peak in results])

    assert np.ptp(areas) / np.mean(areas) < 0.01
    assert max(peak.peak_ppm for peak in results) - min(
        peak.peak_ppm for peak in results
    ) < 0.002
