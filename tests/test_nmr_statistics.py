"""Unit and integration tests for the NMR statistics/uncertainty modules.

These use synthetic spectra and synthetic time series only -- no instrument
data is required -- so they run anywhere the scientific stack is installed.
The numbered tests map to the acceptance criteria in the upgrade brief.
"""

from __future__ import annotations

import numpy as np
import pytest

from chemyx_lab.analysis import statistics as st
from chemyx_lab.analysis import lineshapes as ls
from chemyx_lab.analysis import uncertainty as un
from chemyx_lab.analysis import time_series as tsmod
from chemyx_lab.analysis import kinetics as kin
from chemyx_lab.analysis import multivariate as mv
from chemyx_lab.analysis import normalization as norm
from chemyx_lab.analysis import qc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lorentzian_window(height, center, fwhm, noise_sd, *, n=400, seed=0):
    x = np.linspace(center - 0.5, center + 0.5, n)
    clean = ls.lorentzian(x, height, center, fwhm)
    rng = np.random.default_rng(seed)
    y = clean + rng.normal(0.0, noise_sd, size=x.size)
    return x, y


# ---------------------------------------------------------------------------
# 1. Deterministic bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_is_deterministic_with_fixed_seed():
    x, y = _lorentzian_window(100.0, 6.0, 0.05, 3.0, seed=1)
    a = un.bootstrap_peak_fit(x, y, model="lorentzian", noise=3.0, iterations=120, seed=777)
    b = un.bootstrap_peak_fit(x, y, model="lorentzian", noise=3.0, iterations=120, seed=777)
    assert a.center_ppm.se == b.center_ppm.se
    assert a.area.ci95_low == b.area.ci95_low
    assert a.height.ci95_high == b.height.ci95_high
    # A different seed should generally give a (slightly) different SE.
    c = un.bootstrap_peak_fit(x, y, model="lorentzian", noise=3.0, iterations=120, seed=778)
    assert c.center_ppm.se != a.center_ppm.se


# ---------------------------------------------------------------------------
# 2. Center uncertainty decreases as SNR increases
# ---------------------------------------------------------------------------


def test_center_uncertainty_decreases_with_snr():
    x, y_low = _lorentzian_window(100.0, 6.0, 0.05, 8.0, seed=2)
    _, y_high = _lorentzian_window(100.0, 6.0, 0.05, 1.0, seed=2)
    low = un.bootstrap_peak_fit(x, y_low, model="lorentzian", noise=8.0, iterations=200, seed=5)
    high = un.bootstrap_peak_fit(x, y_high, model="lorentzian", noise=1.0, iterations=200, seed=5)
    assert high.center_ppm.se < low.center_ppm.se


# ---------------------------------------------------------------------------
# 3. Area uncertainty increases when noise increases
# ---------------------------------------------------------------------------


def test_area_uncertainty_increases_with_noise():
    x, y_low = _lorentzian_window(100.0, 6.0, 0.05, 1.0, seed=3)
    _, y_high = _lorentzian_window(100.0, 6.0, 0.05, 6.0, seed=3)
    low = un.bootstrap_peak_fit(x, y_low, model="lorentzian", noise=1.0, iterations=200, seed=9)
    high = un.bootstrap_peak_fit(x, y_high, model="lorentzian", noise=6.0, iterations=200, seed=9)
    assert high.area.se > low.area.se


def test_unidentifiable_fit_falls_back_without_fabricating():
    # Pure noise: no real peak -> center/height/fwhm uncertainty must be NaN,
    # but an area uncertainty is still available from the parametric bootstrap.
    rng = np.random.default_rng(11)
    x = np.linspace(5.5, 6.5, 200)
    y = rng.normal(0.0, 1.0, size=x.size)
    result = un.bootstrap_peak_fit(x, y, model="gaussian", noise=1.0, iterations=50, seed=1)
    # It may or may not "fit" noise, but if it falls back the contract holds:
    if result.method == "parametric_area_only":
        assert np.isnan(result.center_ppm.se)
        assert np.isfinite(result.area.se)
        assert "area_from_parametric_noise_bootstrap" in result.reason


# ---------------------------------------------------------------------------
# 4. Overlapping peaks produce deconvolution warnings
# ---------------------------------------------------------------------------


def test_overlapping_peaks_warn_and_separated_peaks_do_not():
    # Two peaks 0.02 ppm apart, each FWHM 0.05 ppm -> Rs < 1 -> overlap.
    close = ls.nearest_peak_overlap([6.00, 6.02], [0.05, 0.05])
    assert close[0].resolution < 1.0
    assert not close[0].deconvolution_stable
    assert "peak_overlap_low_resolution" in close[0].overlap_warning
    # Two peaks 0.5 ppm apart -> baseline separated -> stable, no warning.
    far = ls.nearest_peak_overlap([6.0, 6.5], [0.05, 0.05])
    assert far[0].resolution >= 1.5
    assert far[0].deconvolution_stable
    assert far[0].overlap_warning == ""


# ---------------------------------------------------------------------------
# 5. Fixed integration regions stay constant across spectra
# ---------------------------------------------------------------------------


def test_fixed_region_boundaries_constant_across_shifted_spectra():
    region = tsmod.FixedRegion("candidate", 5.70, 5.90)
    boundaries = []
    for shift in (0.0, 0.01, -0.015):
        x = np.linspace(5.0, 6.5, 600)
        y = ls.gaussian(x, 50.0, 5.80 + shift, 0.03)
        integral = tsmod.integrate_fixed_region(x, y, region)
        boundaries.append((integral.left_ppm, integral.right_ppm))
        assert integral.positive_area > 0
    # Every spectrum integrated the identical ppm window despite the peak moving.
    assert len(set(boundaries)) == 1
    assert boundaries[0] == (5.70, 5.90)


# ---------------------------------------------------------------------------
# 6. Rates use actual irregular time intervals
# ---------------------------------------------------------------------------


def test_rates_use_actual_irregular_intervals():
    times = [0.0, 0.5, 2.5]          # 0.5 h then 2.0 h gaps
    areas = [0.0, 10.0, 30.0]        # +10 over 0.5h=20/h; +20 over 2h=10/h
    rows = tsmod.series_rates(times, areas, rolling_window=2)
    assert rows[1].absolute_rate_per_hour == pytest.approx(20.0)
    assert rows[2].absolute_rate_per_hour == pytest.approx(10.0)
    # If intervals were wrongly assumed equal, both would be identical.
    assert rows[1].absolute_rate_per_hour != rows[2].absolute_rate_per_hour


# ---------------------------------------------------------------------------
# 7. First-order rate constant recovery
# ---------------------------------------------------------------------------


def test_first_order_rate_constant_recovered_within_tolerance():
    rng = np.random.default_rng(7)
    t = np.linspace(0.0, 6.0, 30)
    k_true = 0.75
    a = 100.0 * (1.0 - np.exp(-k_true * t)) + rng.normal(0.0, 0.4, t.size)
    fit = kin.fit_kinetic_model(t, a, model="first_order_formation", noise=0.4)
    assert fit.fit_success
    assert fit.rate_constant == pytest.approx(k_true, rel=0.1)
    assert fit.rate_constant_ci95_low < k_true < fit.rate_constant_ci95_high
    fits, best = kin.fit_all_models(
        t, a,
        models=["zero_order", "first_order_decay", "first_order_formation"],
        noise=0.4,
    )
    assert best == "first_order_formation"


# ---------------------------------------------------------------------------
# 8-10. Plateau detection
# ---------------------------------------------------------------------------


def test_plateau_passes_true_flat_series():
    rng = np.random.default_rng(8)
    t = np.linspace(0.0, 5.0, 12)
    a = 100.0 + rng.normal(0.0, 0.05, t.size)
    result = tsmod.statistical_plateau(
        t, a, minimum_points=4, equivalence_percent_per_hour=1.0, persistence_points=3
    )
    assert result.plateau_statistical_pass
    assert not result.decline_detected


def test_plateau_rejects_positive_growth():
    t = np.linspace(0.0, 5.0, 12)
    a = 10.0 + 15.0 * t  # strong steady growth
    result = tsmod.statistical_plateau(
        t, a, minimum_points=4, equivalence_percent_per_hour=1.0, persistence_points=3
    )
    assert not result.plateau_statistical_pass


def test_plateau_rejects_sustained_decline_by_default():
    t = np.linspace(0.0, 5.0, 12)
    a = 100.0 - 8.0 * t  # steady decline
    result = tsmod.statistical_plateau(
        t, a, minimum_points=4, equivalence_percent_per_hour=1.0, persistence_points=3
    )
    assert not result.plateau_statistical_pass
    assert result.decline_detected
    # Opting in should stop the decline itself from failing the equivalence test,
    # though a steep decline still fails on the margin.
    allowed = tsmod.statistical_plateau(
        t, a, minimum_points=4, equivalence_abs=100.0, persistence_points=3,
        allow_declining=True,
    )
    assert allowed.plateau_statistical_pass  # wide margin + decline allowed


# ---------------------------------------------------------------------------
# 11. Internal-standard ratio uncertainty propagation
# ---------------------------------------------------------------------------


def test_internal_standard_ratio_uncertainty_propagates():
    result = norm.ratio_with_uncertainty(
        analyte_area=200.0, standard_area=100.0,
        analyte_uncertainty=10.0, standard_uncertainty=4.0,
    )
    assert result.analyte_to_standard_ratio == pytest.approx(2.0)
    # (u/r)^2 = (10/200)^2 + (4/100)^2 -> u = 2.0 * sqrt(0.0025 + 0.0016)
    expected = 2.0 * np.sqrt((10.0 / 200.0) ** 2 + (4.0 / 100.0) ** 2)
    assert result.ratio_uncertainty == pytest.approx(expected)
    assert result.normalization_qc_pass


def test_internal_standard_near_zero_standard_fails_closed():
    result = norm.ratio_with_uncertainty(
        analyte_area=200.0, standard_area=0.0,
        analyte_uncertainty=10.0, standard_uncertainty=1.0,
    )
    assert not result.normalization_qc_pass
    assert np.isnan(result.analyte_to_standard_ratio)
    assert result.normalization_failure_reason == "standard_area_near_zero"


# ---------------------------------------------------------------------------
# 12. MAD outlier detection with zero MAD
# ---------------------------------------------------------------------------


def test_mad_outlier_handles_zero_mad():
    # More than half identical -> MAD == 0; must not divide by zero.
    scores, flags, method = st.flag_outliers([5.0, 5.0, 5.0, 5.0, 9.0])
    assert method == "meanad_fallback"
    assert all(np.isfinite(s) for s in scores)
    assert flags[-1] is True  # the 9.0 is the outlier
    # All identical -> zero spread, nothing flagged, no crash.
    scores2, flags2, method2 = st.flag_outliers([3.0, 3.0, 3.0])
    assert method2 == "zero_spread"
    assert not any(flags2)


# ---------------------------------------------------------------------------
# 13. CV with zero / near-zero mean
# ---------------------------------------------------------------------------


def test_cv_handles_zero_and_near_zero_mean():
    value, reason = st.coefficient_of_variation([-1.0, 1.0, -1.0, 1.0])
    assert np.isnan(value)
    assert reason == "mean_near_zero"
    single, reason2 = st.coefficient_of_variation([5.0])
    assert np.isnan(single)
    assert reason2 == "insufficient_n"
    good, reason3 = st.coefficient_of_variation([10.0, 11.0, 9.0, 10.5])
    assert np.isfinite(good) and reason3 == ""


# ---------------------------------------------------------------------------
# 14. PCA and similarity use a common aligned axis
# ---------------------------------------------------------------------------


def test_similarity_and_pca_use_common_axis():
    x1 = np.linspace(5.0, 7.0, 300)
    x2 = np.linspace(5.0, 7.0, 271)  # different digital resolution
    s1 = ls.gaussian(x1, 100.0, 6.0, 0.05)
    s2 = 0.4 * ls.gaussian(x2, 100.0, 6.0, 0.05)
    grid = mv.common_grid([(x1, s1), (x2, s2)])
    # Both spectra now live on the identical grid.
    assert grid.matrix.shape[1] == grid.ppm.size
    sim = mv.spectral_similarity(grid)
    assert sim[0]["cosine_similarity_to_first"] == pytest.approx(1.0)
    assert sim[1]["cosine_similarity_to_first"] == pytest.approx(1.0, abs=1e-3)
    result = mv.pca(grid, n_components=2)
    assert result.scores.shape[0] == 2
    assert result.loadings.shape[1] == grid.ppm.size


def test_common_grid_rejects_non_overlapping_spectra():
    with pytest.raises(ValueError):
        mv.common_grid([(np.linspace(0, 1, 10), np.ones(10)),
                        (np.linspace(5, 6, 10), np.ones(10))])


# ---------------------------------------------------------------------------
# 15. Existing engine behavior remains available
# ---------------------------------------------------------------------------


def test_existing_nmr_engine_still_imports_and_runs():
    # The new modules must not perturb the existing engine's public surface.
    from chemyx_lab.analysis import nmr
    for name in ("pick_spectrum_region", "track_peak_families",
                 "integrate_above_local_baseline", "build_phased_spectrum"):
        assert hasattr(nmr, name)
    # And a core numerical routine still works on a synthetic spectrum.
    x = np.linspace(5.0, 6.5, 800)
    y = ls.gaussian(x, 100.0, 5.8, 0.03) + 5.0
    picked = nmr.pick_spectrum_region(x, y, region_min_ppm=5.0, region_max_ppm=6.5)
    assert len(picked.peaks) >= 1


# ---------------------------------------------------------------------------
# Fit diagnostics sanity
# ---------------------------------------------------------------------------


def test_fit_diagnostics_reward_correct_model():
    x = np.linspace(5.5, 6.5, 300)
    y = ls.gaussian(x, 100.0, 6.0, 0.05)
    rng = np.random.default_rng(4)
    y = y + rng.normal(0.0, 1.0, x.size)
    comparison = ls.compare_models(x, y, noise=1.0)
    # The generating shape is Gaussian; pseudo-Voigt can match it, Lorentzian
    # should be clearly worse (positive delta AICc).
    assert comparison.delta_aicc["lorentzian"] > 0
    assert np.isfinite(comparison.aicc["gaussian"])


# ---------------------------------------------------------------------------
# Integration: a full synthetic time series through the library pipeline
# ---------------------------------------------------------------------------


def test_integration_synthetic_series_pipeline():
    """A synthetic growing peak processed end-to-end: fixed region -> rates ->
    plateau -> kinetics -> family stats, exercising the modules together."""
    rng = np.random.default_rng(21)
    times = np.array([0.0, 0.4, 1.1, 2.0, 3.3, 4.5, 6.0, 8.0])
    k_true = 0.5
    plateau_true = 500.0
    region = tsmod.FixedRegion("product", 5.70, 5.90)

    areas = []
    family_ppm = []
    family_area = []
    for i, t in enumerate(times):
        amp = plateau_true * (1.0 - np.exp(-k_true * t))
        x = np.linspace(5.0, 6.5, 900)
        y = ls.gaussian(x, max(amp, 0.1) / 20.0, 5.80, 0.03) + rng.normal(0.0, 0.02, x.size)
        integral = tsmod.integrate_fixed_region(x, y, region)
        assert integral.left_ppm == 5.70 and integral.right_ppm == 5.90  # constant
        areas.append(integral.positive_area)
        family_ppm.append(5.80 + rng.normal(0.0, 0.001))
        family_area.append(integral.positive_area)

    elapsed = list(times)
    rates = tsmod.series_rates(elapsed, areas, rolling_window=4)
    assert len(rates) == len(times)

    # Kinetics should recover a first-order formation with the right sign.
    fit = kin.fit_kinetic_model(elapsed, areas, model="first_order_formation", noise=None)
    assert fit.fit_success
    assert fit.rate_constant > 0

    # Early growth must NOT be called a plateau.
    early = tsmod.statistical_plateau(
        elapsed[:5], areas[:5], minimum_points=4, equivalence_percent_per_hour=2.0,
        persistence_points=2,
    )
    assert not early.plateau_statistical_pass

    # Robust family statistics are finite and sensible.
    summary = st.summarize(family_area)
    assert summary.n == len(times)
    assert np.isfinite(summary.median)
    ppm_summary = st.summarize(family_ppm)
    assert ppm_summary.mad >= 0


# ---------------------------------------------------------------------------
# Configuration: default disabled (backward compatible) + validation
# ---------------------------------------------------------------------------


def test_statistics_config_defaults_to_disabled():
    from chemyx_lab.analysis.analysis_config import load_statistics_config
    cfg = load_statistics_config(None)
    assert cfg.enabled is False           # absent section -> disabled
    assert cfg.bootstrap.iterations == 500
    assert cfg.bootstrap.random_seed == 12345


def test_statistics_config_validates_and_rejects_bad_values():
    from chemyx_lab.analysis.analysis_config import load_statistics_config, ConfigError
    with pytest.raises(ConfigError):
        load_statistics_config({"bootstrap": {"confidence_level": 1.5}})
    with pytest.raises(ConfigError):
        load_statistics_config({"kinetics": {"models": ["not_a_model"]}})
    with pytest.raises(ConfigError):
        load_statistics_config({"unknown_key": 1})
    good = load_statistics_config({
        "enabled": True,
        "fixed_regions": [{"name": "r", "left_ppm": 5.7, "right_ppm": 5.9}],
    })
    assert good.enabled and good.fixed_regions[0].name == "r"


def test_repository_analysis_yaml_parses():
    # The shipped config file must load. Statistics are enabled in it so that a
    # bare `process_fid.py <input>` run emits the full table set without needing
    # --statistics; the library default (no section) stays off, which
    # test_statistics_config_defaults_to_disabled covers.
    from pathlib import Path
    from chemyx_lab.config import read_mapping_config
    from chemyx_lab.analysis.analysis_config import load_statistics_config
    from chemyx_lab.analysis.target_peak_config import load_target_peak_config
    repo = Path(__file__).resolve().parents[1]
    raw = read_mapping_config(repo / "configs" / "nmr" / "analysis.yaml", "cfg")
    cfg = load_statistics_config(raw.get("statistics"))
    target_cfg = load_target_peak_config(raw.get("target_peak"))
    assert cfg.enabled is True
    assert [r.name for r in cfg.fixed_regions] == ["candidate_5p79", "candidate_6p10"]
    assert target_cfg.plot_window_ppm == (5.0, 6.5)
    assert target_cfg.search_window_ppm == (5.70, 5.90)
    assert target_cfg.integration_window_ppm == (5.70, 5.90)


# ---------------------------------------------------------------------------
# Report builder: table set + stable join keys (wiring logic without FFT)
# ---------------------------------------------------------------------------


def _synthetic_spectrum_stat(index, center, amp, seed):
    from chemyx_lab.analysis.statistics_report import PeakObservation, SpectrumStat
    from datetime import datetime, timedelta
    x = np.linspace(5.0, 6.5, 900)
    rng = np.random.default_rng(seed)
    y = ls.gaussian(x, amp, center, 0.03) + rng.normal(0.0, 0.5, x.size)
    peak = PeakObservation(
        peak_number=1, peak_family_id="P001", center_ppm=center, height=amp,
        width_ppm=0.03, width_hz=0.03 * 60.0, signed_area=amp * 0.03,
        positive_area=amp * 0.03, snr=amp / 0.5, prominence_snr=amp / 0.5,
        classification="resolved_peak",
    )
    return SpectrumStat(
        file=f"s{index}.dx", source_path=f"/data/s{index}.dx", spectrum_index=index,
        timestamp=datetime(2026, 6, 8, 11, 0) + timedelta(hours=index),
        observe_frequency_mhz=60.0, region_noise=0.5, reference_shift_ppm=0.0,
        reference_qc_pass=True, phase0_deg=0.0, phase1_deg=0.0, peaks=[peak],
        region_ppm=x, region_quant_corrected=y, full_ppm=x, full_intensity=y,
    )


def test_report_builder_emits_all_tables_with_join_keys():
    from chemyx_lab.analysis.statistics_report import build_statistics_report
    from chemyx_lab.analysis.analysis_config import (
        load_statistics_config,
    )
    cfg = load_statistics_config({
        "enabled": True,
        "bootstrap": {"iterations": 30},
        "fixed_regions": [{"name": "r5p8", "left_ppm": 5.70, "right_ppm": 5.90}],
    })
    spectra = [
        _synthetic_spectrum_stat(i, 5.80, 100.0 * (1 - np.exp(-0.5 * i)) + 1.0, seed=i)
        for i in range(6)
    ]
    report = build_statistics_report(spectra, cfg)
    expected = {
        "peak_uncertainty.csv", "peak_overlap.csv", "peak_family_statistics.csv",
        "peak_outliers.csv", "spectral_similarity.csv", "time_series_regions.csv",
        "run_qc.csv", "time_series_rates.csv", "plateau_analysis.csv",
        "kinetic_fits.csv",
    }
    assert expected.issubset(set(report.tables))
    # Every peak_uncertainty row carries the four stable join keys.
    cols, rows = report.tables["peak_uncertainty.csv"]
    for key in ("file", "spectrum_index", "peak_number", "peak_id", "peak_family_id"):
        assert key in cols
    assert all(r["peak_family_id"] == "P001" for r in rows)
    # Fixed region integrated with constant boundaries across all spectra.
    _, region_rows = report.tables["time_series_regions.csv"]
    assert {(r["left_ppm"], r["right_ppm"]) for r in region_rows} == {(5.70, 5.90)}
    assert report.provenance["n_spectra"] == 6


def test_statistics_plots_separate_each_peak_and_isolate_two_peak_comparison(tmp_path):
    from types import SimpleNamespace
    from chemyx_lab.analysis.statistics_plots import render_plots

    elapsed = [0.0, 1.0, 2.0, 3.0]
    report = SimpleNamespace(
        warnings=[],
        qc_series={},
        similarity_series={},
        kinetic_best={},
        target_series={
            "region:candidate_5p79": {
                "kind": "fixed_region",
                "name": "candidate_5p79",
                "elapsed": elapsed,
                "area": [1.0, 2.0, 3.0, 4.0],
                "area_uncertainty": [0.1] * 4,
            },
            "region:candidate_6p10": {
                "kind": "fixed_region",
                "name": "candidate_6p10",
                "elapsed": elapsed,
                "area": [4.0, 3.0, 2.0, 1.0],
                "area_uncertainty": [0.1] * 4,
            },
        },
    )
    render_plots(report, tmp_path)
    stats = tmp_path / "statistics"
    for name in ("candidate_5p79", "candidate_6p10"):
        assert (stats / "single_peak" / name / "area_with_ci_vs_time.png").is_file()
        assert (stats / "single_peak" / name / "rate_vs_time.png").is_file()
    assert (stats / "two_peaks" / "area_with_ci_vs_time.png").is_file()
    assert (stats / "two_peaks" / "rate_vs_time.png").is_file()
    assert not (stats / "area_with_ci_vs_time.png").exists()
