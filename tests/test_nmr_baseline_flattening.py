"""Scientific safeguards for the additive flattened regional NMR overlay."""

import numpy as np
import pytest

from chemyx_lab.analysis.baseline_flattening import (
    FlattenedOverlayConfigError,
    ResidualBaselineConfig,
    flatten_residual_baseline,
    load_flattened_overlay_config,
    regional_authoritative_trace,
)


def _gaussian(x, center, amplitude=100.0, width=0.025):
    return amplitude * np.exp(-0.5 * ((x - center) / width) ** 2)


def test_regional_overlay_slice_is_the_authoritative_real_array():
    ppm = np.linspace(7.0, 4.0, 1000)
    authoritative_real = np.sin(ppm) - 2.0
    magnitude_diagnostic = np.abs(authoritative_real) + 50.0
    regional_ppm, regional_real = regional_authoritative_trace(
        ppm, authoritative_real, 5.0, 6.5
    )
    mask = (ppm >= 5.0) & (ppm <= 6.5)
    assert np.array_equal(regional_ppm, ppm[mask])
    assert np.array_equal(regional_real, authoritative_real[mask])
    assert not np.array_equal(regional_real, magnitude_diagnostic[mask])


def test_synthetic_sloped_baseline_is_substantially_flattened():
    rng = np.random.default_rng(20260728)
    ppm = np.linspace(5.0, 6.5, 1600)
    true_baseline = 18.0 + 7.5 * (ppm - 5.75)
    y = true_baseline + _gaussian(ppm, 5.785, 500.0) + rng.normal(0.0, 0.4, ppm.size)
    result = flatten_residual_baseline(
        ppm, y, detected_peak_ppm=[5.785]
    )
    assert abs(result.metrics["baseline_slope_after"]) < (
        0.1 * abs(result.metrics["baseline_slope_before"])
    )
    assert abs(result.metrics["baseline_median_after"]) < 0.2
    assert result.metrics["baseline_flattening_qc_pass"]


def test_strong_peak_and_protected_region_do_not_pull_the_linear_fit():
    ppm = np.linspace(5.0, 6.5, 1800)
    true_baseline = 25.0 - 4.0 * (ppm - 5.75)
    y = (
        true_baseline
        + _gaussian(ppm, 5.785, 2500.0, 0.018)
        + _gaussian(ppm, 6.10, 300.0, 0.018)
    )
    result = flatten_residual_baseline(
        ppm, y, detected_peak_ppm=[5.785, 6.10]
    )
    assert np.max(np.abs(result.estimated_baseline - true_baseline)) < 1e-6
    protected = (ppm >= 5.70) & (ppm <= 5.90)
    assert not np.any(result.baseline_mask[protected])


def test_negative_noise_is_preserved_not_clipped_or_absoluted():
    rng = np.random.default_rng(11)
    ppm = np.linspace(5.0, 6.5, 1200)
    noise = rng.normal(0.0, 0.8, ppm.size)
    y = 12.0 + 2.0 * ppm + noise + _gaussian(ppm, 5.785, 200.0)
    result = flatten_residual_baseline(ppm, y, detected_peak_ppm=[5.785])
    assert np.any(result.after < 0.0)
    assert np.any(result.after > 0.0)
    assert not np.array_equal(result.after, np.maximum(result.after, 0.0))
    assert not np.array_equal(result.after, np.abs(result.after))


def test_plot_only_linear_correction_preserves_peak_height_and_local_area():
    ppm = np.linspace(5.70, 5.90, 801)
    y = 30.0 + 5.0 * ppm + _gaussian(ppm, 5.785, 400.0, 0.012)
    cfg = ResidualBaselineConfig(
        excluded_regions_ppm=((5.76, 5.81),),
        peak_guard_width_ppm=0.03,
    )
    result = flatten_residual_baseline(
        ppm, y, config=cfg, detected_peak_ppm=[5.785]
    )
    peak_index = int(np.argmax(y))

    def relative_height(values):
        local_line = np.interp(
            ppm[peak_index],
            [ppm[0], ppm[-1]],
            [values[0], values[-1]],
        )
        return values[peak_index] - local_line

    def area_above_feet(values):
        feet = np.interp(
            ppm,
            [ppm[0], ppm[-1]],
            [values[0], values[-1]],
        )
        return np.trapezoid(values - feet, ppm)

    assert relative_height(result.after) == pytest.approx(
        relative_height(result.before), abs=1e-10
    )
    assert area_above_feet(result.after) == pytest.approx(
        area_above_feet(result.before), abs=1e-10
    )


def test_flattening_is_deterministic():
    ppm = np.linspace(5.0, 6.5, 1000)
    y = 14.0 + 3.0 * ppm + _gaussian(ppm, 5.785)
    first = flatten_residual_baseline(ppm, y, detected_peak_ppm=[5.785])
    second = flatten_residual_baseline(ppm, y, detected_peak_ppm=[5.785])
    assert np.array_equal(first.estimated_baseline, second.estimated_baseline)
    assert np.array_equal(first.after, second.after)
    assert np.array_equal(first.baseline_mask, second.baseline_mask)
    assert first.metrics == second.metrics


def test_flattened_overlay_config_defaults_and_validation():
    cfg = load_flattened_overlay_config(None)
    assert cfg.enabled
    assert cfg.use_authoritative_real_trace
    assert cfg.residual_baseline.polynomial_order == 1
    assert cfg.residual_baseline.excluded_regions_ppm == ((5.70, 5.90),)
    with pytest.raises(FlattenedOverlayConfigError):
        load_flattened_overlay_config({"use_authoritative_real_trace": False})
    with pytest.raises(FlattenedOverlayConfigError):
        load_flattened_overlay_config(
            {
                "residual_baseline": {
                    "method": "robust_linear",
                    "polynomial_order": 3,
                }
            }
        )
