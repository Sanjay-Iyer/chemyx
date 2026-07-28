"""Assemble the optional publication-statistics tables for a spectrum series.

``process_fid.py`` collects one :class:`SpectrumStat` per processed spectrum and
hands the list, plus a :class:`~chemyx_lab.analysis.analysis_config.StatisticsConfig`,
to :func:`build_statistics_report`.  All numerical work lives here (not in the
CLI script); the script only writes the returned tables and calls
:func:`render_plots`.

Every table is additive and joins back to ``peaks.csv`` / ``results.csv`` via the
stable keys ``file``, ``spectrum_index``, ``peak_number``, ``peak_id`` and
``peak_family_id``.  Un-computable metrics are emitted blank with a QC reason;
a single failed peak, fit, or plot never aborts the report (failures are caught
and recorded in ``report.warnings``).

Analysis targets for rates / plateau / kinetics are the **fixed integration
regions** (stable ppm boundaries) and any **reproducible peak families**.  Fixed
regions are preferred for kinetics because their boundaries do not move from
spectrum to spectrum, so a change in area reflects chemistry rather than the
peak picker relocating its own limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from . import statistics as st
from . import kinetics as kin
from . import multivariate as mv
from . import normalization as norm
from .analysis_config import StatisticsConfig, FixedRegionConfig
from .lineshapes import nearest_peak_overlap
from .time_series import (
    FixedRegion,
    integrate_fixed_region,
    elapsed_hours,
    series_rates,
    statistical_plateau,
)
from .uncertainty import bootstrap_peak_fit


def _np():
    import numpy as np

    return np


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


@dataclass
class PeakObservation:
    """One peak in one spectrum, as reported in ``peaks.csv``."""

    peak_number: int
    peak_family_id: str
    center_ppm: float
    height: float
    width_ppm: float
    width_hz: float
    signed_area: float
    positive_area: float
    snr: float
    prominence_snr: float
    classification: str

    @property
    def peak_id(self) -> str:
        return f"{self.peak_number}"


@dataclass
class SpectrumStat:
    """Everything the statistics report needs about one processed spectrum."""

    file: str
    source_path: str
    spectrum_index: int
    timestamp: datetime | None
    observe_frequency_mhz: float
    region_noise: float
    reference_shift_ppm: float
    reference_qc_pass: bool
    phase0_deg: float | None
    phase1_deg: float | None
    peaks: list[PeakObservation]
    region_ppm: object            # local region ppm axis (numpy)
    region_quant_corrected: object  # baseline-corrected quantitative trace (numpy)
    full_ppm: object              # whole quantitative spectrum ppm (numpy)
    full_intensity: object        # whole quantitative spectrum intensity (numpy)


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


@dataclass
class StatisticsReport:
    """Named CSV tables (columns + rows) plus plot data and provenance."""

    tables: dict[str, tuple[list[str], list[dict]]] = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Retained for plotting:
    target_series: dict[str, dict] = field(default_factory=dict)
    similarity_series: dict = field(default_factory=dict)
    qc_series: dict = field(default_factory=dict)
    kinetic_best: dict[str, dict] = field(default_factory=dict)


_BLANK = ""


def _f(value) -> float:
    return float(value) if value is not None else float("nan")


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_statistics_report(
    spectra: Sequence[SpectrumStat],
    config: StatisticsConfig,
) -> StatisticsReport:
    """Compute every enabled statistics table for a series of spectra."""
    np = _np()
    report = StatisticsReport()
    spectra = list(spectra)
    n = len(spectra)
    elapsed = elapsed_hours([s.timestamp for s in spectra])

    report.provenance = {
        "statistics_enabled": config.enabled,
        "bootstrap_enabled": config.bootstrap.enabled,
        "bootstrap_iterations": config.bootstrap.iterations,
        "bootstrap_model": config.bootstrap.model,
        "bootstrap_confidence_level": config.bootstrap.confidence_level,
        "bootstrap_random_seed": config.bootstrap.random_seed,
        "internal_standard_enabled": config.internal_standard.enabled,
        "fixed_regions": [r.name for r in config.fixed_regions],
        "plateau_method": config.plateau.method,
        "kinetic_models_tested": list(config.kinetics.models) if config.kinetics.enabled else [],
        "spectral_similarity_enabled": config.multivariate.spectral_similarity,
        "pca_enabled": config.multivariate.pca,
        "n_spectra": n,
    }

    _peak_uncertainty(report, spectra, config)
    _overlap(report, spectra)
    _family_statistics(report, spectra, config)
    similarity = _spectral_similarity(report, spectra, elapsed, config)
    region_series = _time_series_regions(report, spectra, elapsed, config)
    _run_qc(report, spectra, elapsed, similarity)
    targets = _assemble_targets(spectra, elapsed, region_series, config)
    _rates(report, targets)
    _plateau(report, targets, config)
    _kinetics(report, targets, config)

    return report


# ---------------------------------------------------------------------------
# 1. Peak-level uncertainty (residual bootstrap)
# ---------------------------------------------------------------------------

_PEAK_UNCERTAINTY_COLUMNS = [
    "file", "spectrum_index", "peak_number", "peak_id", "peak_family_id",
    "center_ppm", "height", "area",
    "center_ppm_se", "center_ppm_ci95_low", "center_ppm_ci95_high",
    "height_se", "height_ci95_low", "height_ci95_high",
    "area_se", "area_ci95_low", "area_ci95_high",
    "width_ppm_se", "width_ppm_ci95_low", "width_ppm_ci95_high",
    "bootstrap_method", "bootstrap_iterations_requested",
    "bootstrap_iterations_succeeded", "bootstrap_qc_pass", "bootstrap_qc_reason",
]


def _peak_uncertainty(report, spectra, config) -> None:
    np = _np()
    rows: list[dict] = []
    if not config.bootstrap.enabled:
        report.tables["peak_uncertainty.csv"] = (_PEAK_UNCERTAINTY_COLUMNS, rows)
        return
    boot = config.bootstrap
    for s in spectra:
        ppm = np.asarray(s.region_ppm, dtype=float)
        corrected = (
            np.asarray(s.region_quant_corrected, dtype=float)
            if s.region_quant_corrected is not None
            else None
        )
        for peak in s.peaks:
            row = {
                "file": s.file, "spectrum_index": s.spectrum_index,
                "peak_number": peak.peak_number, "peak_id": peak.peak_id,
                "peak_family_id": peak.peak_family_id,
                "center_ppm": peak.center_ppm, "height": peak.height,
                "area": peak.positive_area,
            }
            try:
                if corrected is None or ppm.size == 0:
                    raise ValueError("no quantitative trace available")
                half = max(4.0 * float(peak.width_ppm), 0.03)
                mask = np.abs(ppm - float(peak.center_ppm)) <= half
                if int(np.count_nonzero(mask)) < 7:
                    # widen once
                    mask = np.abs(ppm - float(peak.center_ppm)) <= 2.0 * half
                wx = ppm[mask]
                wy = corrected[mask]
                # Deterministic, independent seed per peak.
                seed = int(boot.random_seed) + s.spectrum_index * 100003 + peak.peak_number
                result = bootstrap_peak_fit(
                    wx, wy, model=boot.model, noise=s.region_noise,
                    iterations=boot.iterations, confidence=boot.confidence_level,
                    seed=seed,
                )
                row.update({
                    "center_ppm_se": result.center_ppm.se,
                    "center_ppm_ci95_low": result.center_ppm.ci95_low,
                    "center_ppm_ci95_high": result.center_ppm.ci95_high,
                    "height_se": result.height.se,
                    "height_ci95_low": result.height.ci95_low,
                    "height_ci95_high": result.height.ci95_high,
                    "area_se": result.area.se,
                    "area_ci95_low": result.area.ci95_low,
                    "area_ci95_high": result.area.ci95_high,
                    "width_ppm_se": result.fwhm_ppm.se,
                    "width_ppm_ci95_low": result.fwhm_ppm.ci95_low,
                    "width_ppm_ci95_high": result.fwhm_ppm.ci95_high,
                    "bootstrap_method": result.method,
                    "bootstrap_iterations_requested": result.iterations_requested,
                    "bootstrap_iterations_succeeded": result.iterations_succeeded,
                    "bootstrap_qc_pass": result.qc_pass,
                    "bootstrap_qc_reason": result.reason,
                })
            except Exception as exc:  # never let one peak abort the run
                report.warnings.append(
                    f"peak_uncertainty failed for {s.file} peak {peak.peak_number}: {exc}"
                )
                for col in _PEAK_UNCERTAINTY_COLUMNS:
                    row.setdefault(col, _BLANK)
                row["bootstrap_qc_pass"] = False
                row["bootstrap_qc_reason"] = f"exception: {exc}"
            rows.append(row)
    report.tables["peak_uncertainty.csv"] = (_PEAK_UNCERTAINTY_COLUMNS, rows)


# ---------------------------------------------------------------------------
# Peak overlap / resolution diagnostics
# ---------------------------------------------------------------------------

_OVERLAP_COLUMNS = [
    "file", "spectrum_index", "peak_number", "peak_id", "peak_family_id",
    "center_ppm", "nearest_peak_distance_ppm", "nearest_peak_distance_hz",
    "resolution", "overlap_fraction", "deconvolution_stable", "overlap_warning",
]


def _overlap(report, spectra) -> None:
    rows: list[dict] = []
    for s in spectra:
        diags = nearest_peak_overlap(
            [p.center_ppm for p in s.peaks],
            [p.width_ppm for p in s.peaks],
            observe_frequency_mhz=s.observe_frequency_mhz,
        )
        for peak, diag in zip(s.peaks, diags):
            rows.append({
                "file": s.file, "spectrum_index": s.spectrum_index,
                "peak_number": peak.peak_number, "peak_id": peak.peak_id,
                "peak_family_id": peak.peak_family_id, "center_ppm": peak.center_ppm,
                "nearest_peak_distance_ppm": diag.nearest_peak_distance_ppm,
                "nearest_peak_distance_hz": diag.nearest_peak_distance_hz,
                "resolution": diag.resolution,
                "overlap_fraction": diag.overlap_fraction,
                "deconvolution_stable": diag.deconvolution_stable,
                "overlap_warning": diag.overlap_warning,
            })
    report.tables["peak_overlap.csv"] = (_OVERLAP_COLUMNS, rows)


# ---------------------------------------------------------------------------
# Peak-family statistics + outliers
# ---------------------------------------------------------------------------

_FAMILY_COLUMNS = [
    "peak_family_id", "detection_frequency", "observations", "expected_observations",
    "mean_ppm", "median_ppm", "ppm_sd", "ppm_mad", "ppm_iqr",
    "mean_area", "median_area", "area_sd", "area_mad", "area_iqr", "area_cv_percent",
    "mean_height", "height_cv_percent", "mean_width_hz", "width_cv_percent",
    "first_spectrum_index", "last_spectrum_index", "reproducible",
]

_OUTLIER_COLUMNS = [
    "file", "spectrum_index", "peak_number", "peak_id", "peak_family_id",
    "area_robust_z", "ppm_robust_z", "width_robust_z", "snr_robust_z",
    "is_statistical_outlier", "is_processing_outlier", "outlier_reason",
]


def _family_statistics(report, spectra, config) -> None:
    np = _np()
    n = len(spectra)
    families: dict[str, list[tuple[SpectrumStat, PeakObservation]]] = {}
    for s in spectra:
        for peak in s.peaks:
            fid = peak.peak_family_id or ""
            if fid:
                families.setdefault(fid, []).append((s, peak))

    family_rows: list[dict] = []
    outlier_rows: list[dict] = []
    for fid, members in sorted(families.items()):
        ppm = [p.center_ppm for _, p in members]
        area = [p.positive_area for _, p in members]
        height = [p.height for _, p in members]
        width_hz = [p.width_hz for _, p in members]
        indices = [s.spectrum_index for s, _ in members]
        obs = len(members)

        ppm_s = st.summarize(ppm)
        area_s = st.summarize(area)
        height_s = st.summarize(height)
        width_s = st.summarize(width_hz)
        family_rows.append({
            "peak_family_id": fid,
            "detection_frequency": obs / n if n else float("nan"),
            "observations": obs,
            "expected_observations": n,
            "mean_ppm": ppm_s.mean, "median_ppm": ppm_s.median,
            "ppm_sd": ppm_s.sd, "ppm_mad": ppm_s.mad, "ppm_iqr": ppm_s.iqr,
            "mean_area": area_s.mean, "median_area": area_s.median,
            "area_sd": area_s.sd, "area_mad": area_s.mad, "area_iqr": area_s.iqr,
            "area_cv_percent": area_s.cv_percent,
            "mean_height": height_s.mean, "height_cv_percent": height_s.cv_percent,
            "mean_width_hz": width_s.mean, "width_cv_percent": width_s.cv_percent,
            "first_spectrum_index": min(indices), "last_spectrum_index": max(indices),
            "reproducible": obs >= 2,
        })

        if config.outliers.enabled:
            thr = config.outliers.robust_z_threshold
            area_z, _, _ = st.flag_outliers(area, threshold=thr)
            ppm_z, _, _ = st.flag_outliers(ppm, threshold=thr)
            width_z, _, _ = st.flag_outliers(width_hz, threshold=thr)
            snr_z, _, _ = st.flag_outliers([p.snr for _, p in members], threshold=thr)
            for i, (s, peak) in enumerate(members):
                reasons = []
                stat_outlier = False
                for label, z in (("area", area_z[i]), ("ppm", ppm_z[i]),
                                 ("width", width_z[i]), ("snr", snr_z[i])):
                    if np.isfinite(z) and abs(z) > thr:
                        stat_outlier = True
                        reasons.append(f"{label}_robust_z={z:.2f}")
                proc_outlier = peak.classification != "resolved_peak"
                if proc_outlier:
                    reasons.append(f"classification={peak.classification}")
                outlier_rows.append({
                    "file": s.file, "spectrum_index": s.spectrum_index,
                    "peak_number": peak.peak_number, "peak_id": peak.peak_id,
                    "peak_family_id": fid,
                    "area_robust_z": area_z[i], "ppm_robust_z": ppm_z[i],
                    "width_robust_z": width_z[i], "snr_robust_z": snr_z[i],
                    "is_statistical_outlier": stat_outlier,
                    "is_processing_outlier": proc_outlier,
                    "outlier_reason": "; ".join(reasons),
                })

    report.tables["peak_family_statistics.csv"] = (_FAMILY_COLUMNS, family_rows)
    report.tables["peak_outliers.csv"] = (_OUTLIER_COLUMNS, outlier_rows)


# ---------------------------------------------------------------------------
# Spectral similarity (multivariate)
# ---------------------------------------------------------------------------

_SIMILARITY_COLUMNS = [
    "file", "spectrum_index", "elapsed_time_hours",
    "correlation_to_first", "correlation_to_previous",
    "cosine_similarity_to_first", "spectral_rmse_to_first",
    "integrated_absolute_difference", "spectral_angle",
]


def _spectral_similarity(report, spectra, elapsed, config) -> list[dict]:
    rows: list[dict] = []
    if not config.multivariate.spectral_similarity or len(spectra) < 2:
        report.tables["spectral_similarity.csv"] = (_SIMILARITY_COLUMNS, rows)
        return rows
    try:
        grid = mv.common_grid([(s.full_ppm, s.full_intensity) for s in spectra])
        sims = mv.spectral_similarity(grid)
        for i, s in enumerate(spectra):
            rows.append({
                "file": s.file, "spectrum_index": s.spectrum_index,
                "elapsed_time_hours": elapsed[i], **sims[i],
            })
        report.similarity_series = {
            "elapsed": elapsed,
            "correlation_to_first": [r["correlation_to_first"] for r in rows],
            "cosine_similarity_to_first": [r["cosine_similarity_to_first"] for r in rows],
        }
    except Exception as exc:
        report.warnings.append(f"spectral_similarity failed: {exc}")
    report.tables["spectral_similarity.csv"] = (_SIMILARITY_COLUMNS, rows)
    return rows


# ---------------------------------------------------------------------------
# Fixed-window time-series integration
# ---------------------------------------------------------------------------

_REGION_COLUMNS = [
    "region_name", "file", "source_file", "spectrum_index", "timestamp",
    "elapsed_time_hours", "left_ppm", "right_ppm",
    "fixed_window_signed_area", "fixed_window_positive_area",
    "fixed_window_area_uncertainty", "fitted_peak_area",
    "area_difference", "area_difference_percent", "region_noise", "region_snr",
    "qc_pass", "qc_failure_reasons",
]


def _white_noise_area_uncertainty(ppm, lo: float, hi: float, noise: float) -> float:
    """Propagate white intensity noise through a trapezoid integral.

    For ``m`` roughly independent samples of spacing ``d`` with per-point noise
    ``sigma``, ``Var(area) ~ sigma^2 d^2 m`` so ``SE(area) ~ sigma d sqrt(m)``.
    Zero-filled spectra oversample, so ``m`` overstates the independent count and
    this is a *lower bound* on the true area uncertainty (documented).
    """
    np = _np()
    p = np.asarray(ppm, dtype=float)
    mask = (p >= lo) & (p <= hi)
    m = int(np.count_nonzero(mask))
    if m < 2 or not np.isfinite(noise) or noise <= 0:
        return float("nan")
    d = float(np.median(np.abs(np.diff(p[mask]))))
    return float(noise) * d * float(np.sqrt(m))


def _time_series_regions(report, spectra, elapsed, config) -> dict[str, list[dict]]:
    np = _np()
    rows: list[dict] = []
    per_region: dict[str, list[dict]] = {}
    for region_cfg in config.fixed_regions:
        region = FixedRegion(region_cfg.name, region_cfg.left_ppm, region_cfg.right_ppm)
        lo, hi = sorted((region.left_ppm, region.right_ppm))
        for i, s in enumerate(spectra):
            integral = integrate_fixed_region(s.full_ppm, s.full_intensity, region)
            # Sum of detected-peak positive areas whose center falls in the region.
            fitted = sum(
                p.positive_area for p in s.peaks if lo <= p.center_ppm <= hi
            )
            fitted = fitted if fitted > 0 else float("nan")
            diff = (
                integral.positive_area - fitted
                if np.isfinite(fitted) and np.isfinite(integral.positive_area)
                else float("nan")
            )
            diff_pct = (
                100.0 * diff / fitted if np.isfinite(diff) and fitted not in (0, float("nan")) else float("nan")
            )
            area_unc = _white_noise_area_uncertainty(
                s.full_ppm, lo, hi, integral.region_noise
            )
            row = {
                "region_name": region.name, "file": s.file, "source_file": s.source_path,
                "spectrum_index": s.spectrum_index,
                "timestamp": s.timestamp.isoformat() if s.timestamp else "",
                "elapsed_time_hours": elapsed[i],
                "left_ppm": integral.left_ppm, "right_ppm": integral.right_ppm,
                "fixed_window_signed_area": integral.signed_area,
                "fixed_window_positive_area": integral.positive_area,
                "fixed_window_area_uncertainty": area_unc,
                "fitted_peak_area": fitted,
                "area_difference": diff, "area_difference_percent": diff_pct,
                "region_noise": integral.region_noise, "region_snr": integral.region_snr,
                "qc_pass": integral.qc_pass, "qc_failure_reasons": integral.qc_failure_reasons,
            }
            rows.append(row)
            per_region.setdefault(region.name, []).append(row)
    report.tables["time_series_regions.csv"] = (_REGION_COLUMNS, rows)
    return per_region


# ---------------------------------------------------------------------------
# Run-level QC scorecard
# ---------------------------------------------------------------------------

_RUN_QC_COLUMNS = [
    "file", "spectrum_index", "timestamp", "elapsed_time_hours",
    "reference_shift_ppm", "region_noise", "phase0_deg", "phase1_deg",
    "peak_count", "qc_peak_count", "qc_pass_fraction",
    "median_snr", "median_width_hz", "correlation_to_first",
    "cosine_similarity_to_first", "spectral_rmse_to_first",
    "run_qc_pass", "run_qc_failure_reasons",
]


def _run_qc(report, spectra, elapsed, similarity) -> None:
    from . import qc as qcmod

    np = _np()
    sim_by_index = {r["spectrum_index"]: r for r in similarity}
    rows: list[dict] = []
    noise_series, width_series, ref_series, passfrac_series = [], [], [], []
    phase0_series, phase1_series = [], []
    for i, s in enumerate(spectra):
        peak_count = len(s.peaks)
        qc_peaks = sum(1 for p in s.peaks if p.classification == "resolved_peak")
        pass_fraction = qc_peaks / peak_count if peak_count else float("nan")
        median_snr = qcmod.median_ignoring_nan([p.snr for p in s.peaks])
        median_width = qcmod.median_ignoring_nan([p.width_hz for p in s.peaks])
        sim = sim_by_index.get(s.spectrum_index, {})
        verdict = qcmod.evaluate_spectrum_qc(
            region_noise=s.region_noise, peak_count=peak_count,
            qc_pass_fraction=pass_fraction, reference_qc_pass=s.reference_qc_pass,
        )
        rows.append({
            "file": s.file, "spectrum_index": s.spectrum_index,
            "timestamp": s.timestamp.isoformat() if s.timestamp else "",
            "elapsed_time_hours": elapsed[i],
            "reference_shift_ppm": s.reference_shift_ppm, "region_noise": s.region_noise,
            "phase0_deg": _f(s.phase0_deg), "phase1_deg": _f(s.phase1_deg),
            "peak_count": peak_count, "qc_peak_count": qc_peaks,
            "qc_pass_fraction": pass_fraction,
            "median_snr": median_snr, "median_width_hz": median_width,
            "correlation_to_first": sim.get("correlation_to_first", float("nan")),
            "cosine_similarity_to_first": sim.get("cosine_similarity_to_first", float("nan")),
            "spectral_rmse_to_first": sim.get("spectral_rmse_to_first", float("nan")),
            "run_qc_pass": verdict.run_qc_pass,
            "run_qc_failure_reasons": verdict.run_qc_failure_reasons,
        })
        noise_series.append(s.region_noise)
        width_series.append(median_width)
        ref_series.append(s.reference_shift_ppm)
        passfrac_series.append(pass_fraction)
        phase0_series.append(_f(s.phase0_deg))
        phase1_series.append(_f(s.phase1_deg))
    report.tables["run_qc.csv"] = (_RUN_QC_COLUMNS, rows)
    report.qc_series = {
        "elapsed": elapsed, "region_noise": noise_series,
        "median_width_hz": width_series, "reference_shift_ppm": ref_series,
        "qc_pass_fraction": passfrac_series,
        "phase0_deg": phase0_series, "phase1_deg": phase1_series,
    }


# ---------------------------------------------------------------------------
# Analysis targets (fixed regions + reproducible families) for rates/kinetics
# ---------------------------------------------------------------------------


def _assemble_targets(spectra, elapsed, region_series, config) -> dict[str, dict]:
    np = _np()
    targets: dict[str, dict] = {}
    # Fixed regions (positive area, stable boundaries -> preferred for kinetics).
    for name, rows in region_series.items():
        by_index = {r["spectrum_index"]: r for r in rows}
        times, areas, uncertainties = [], [], []
        for i, s in enumerate(spectra):
            r = by_index.get(s.spectrum_index)
            times.append(elapsed[i])
            areas.append(r["fixed_window_positive_area"] if r else float("nan"))
            uncertainties.append(r["fixed_window_area_uncertainty"] if r else float("nan"))
        targets[f"region:{name}"] = {"kind": "fixed_region", "name": name,
                                     "elapsed": times, "area": areas,
                                     "area_uncertainty": uncertainties}
    # Reproducible families (detected boundaries; labelled as such).
    fam_obs: dict[str, dict[int, float]] = {}
    for s in spectra:
        for p in s.peaks:
            if p.peak_family_id:
                fam_obs.setdefault(p.peak_family_id, {})[s.spectrum_index] = p.positive_area
    for fid, obs in sorted(fam_obs.items()):
        if len(obs) < 2:
            continue
        times, areas = [], []
        for i, s in enumerate(spectra):
            times.append(elapsed[i])
            areas.append(obs.get(s.spectrum_index, float("nan")))
        targets[f"family:{fid}"] = {"kind": "peak_family", "name": fid,
                                    "elapsed": times, "area": areas}
    return targets


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------

_RATE_COLUMNS = [
    "analysis_target", "target_kind", "spectrum_index", "elapsed_time_hours", "area",
    "delta_area", "delta_time_hours", "absolute_rate_per_hour",
    "relative_rate_percent_per_hour", "rolling_slope", "rolling_slope_se",
    "rolling_slope_ci95_low", "rolling_slope_ci95_high",
]


def _rates(report, targets) -> None:
    rows: list[dict] = []
    for key, target in targets.items():
        elapsed = target["elapsed"]
        areas = target["area"]
        rate_rows = series_rates(elapsed, areas, rolling_window=4)
        for i, rr in enumerate(rate_rows):
            rows.append({
                "analysis_target": target["name"], "target_kind": target["kind"],
                "spectrum_index": i, "elapsed_time_hours": elapsed[i], "area": areas[i],
                "delta_area": rr.delta_area, "delta_time_hours": rr.delta_time_hours,
                "absolute_rate_per_hour": rr.absolute_rate_per_hour,
                "relative_rate_percent_per_hour": rr.relative_rate_percent_per_hour,
                "rolling_slope": rr.rolling_slope, "rolling_slope_se": rr.rolling_slope_se,
                "rolling_slope_ci95_low": rr.rolling_slope_ci95_low,
                "rolling_slope_ci95_high": rr.rolling_slope_ci95_high,
            })
        report.target_series[key] = target
    report.tables["time_series_rates.csv"] = (_RATE_COLUMNS, rows)


# ---------------------------------------------------------------------------
# Statistical plateau
# ---------------------------------------------------------------------------

_PLATEAU_COLUMNS = [
    "analysis_target", "target_kind", "method", "window_start_time", "window_end_time",
    "points_in_window", "rolling_slope", "rolling_slope_se",
    "rolling_slope_ci95_low", "rolling_slope_ci95_high",
    "equivalence_margin_low", "equivalence_margin_high",
    "plateau_statistical_pass", "decline_detected", "plateau_duration_hours",
    "plateau_failure_reason",
]


def _plateau(report, targets, config) -> None:
    rows: list[dict] = []
    p = config.plateau
    for target in targets.values():
        result = statistical_plateau(
            target["elapsed"], target["area"],
            minimum_points=p.minimum_points,
            equivalence_abs=p.equivalence_abs_per_hour,
            equivalence_percent_per_hour=p.equivalence_percent_per_hour,
            persistence_points=p.persistence_points,
            allow_declining=p.allow_declining_plateau,
        )
        rows.append({
            "analysis_target": target["name"], "target_kind": target["kind"],
            "method": p.method,
            "window_start_time": result.window_start_time,
            "window_end_time": result.window_end_time,
            "points_in_window": result.points_in_window,
            "rolling_slope": result.rolling_slope, "rolling_slope_se": result.rolling_slope_se,
            "rolling_slope_ci95_low": result.rolling_slope_ci95_low,
            "rolling_slope_ci95_high": result.rolling_slope_ci95_high,
            "equivalence_margin_low": result.equivalence_margin_low,
            "equivalence_margin_high": result.equivalence_margin_high,
            "plateau_statistical_pass": result.plateau_statistical_pass,
            "decline_detected": result.decline_detected,
            "plateau_duration_hours": result.plateau_duration_hours,
            "plateau_failure_reason": result.plateau_failure_reason,
        })
    report.tables["plateau_analysis.csv"] = (_PLATEAU_COLUMNS, rows)


# ---------------------------------------------------------------------------
# Kinetics
# ---------------------------------------------------------------------------

_KINETIC_COLUMNS = [
    "analysis_target", "target_kind", "kinetic_model", "is_best_by_aicc",
    "fit_success", "n_observations", "rate_constant", "rate_constant_se",
    "rate_constant_ci95_low", "rate_constant_ci95_high", "half_life", "t90", "t95",
    "plateau_area", "lag_time", "fit_rmse", "r_squared", "aic", "aicc", "bic",
    "durbin_watson", "residual_lag1_autocorrelation", "ljung_box_pvalue",
    "fit_qc_pass", "fit_qc_failure_reasons",
]


def _kinetics(report, targets, config) -> None:
    rows: list[dict] = []
    if not config.kinetics.enabled:
        report.tables["kinetic_fits.csv"] = (_KINETIC_COLUMNS, rows)
        return
    for key, target in targets.items():
        fits, best = kin.fit_all_models(
            target["elapsed"], target["area"],
            models=config.kinetics.models, analysis_target=target["name"],
        )
        for fit in fits:
            rows.append({
                "analysis_target": target["name"], "target_kind": target["kind"],
                "kinetic_model": fit.kinetic_model,
                "is_best_by_aicc": fit.kinetic_model == best,
                "fit_success": fit.fit_success, "n_observations": fit.n_observations,
                "rate_constant": fit.rate_constant, "rate_constant_se": fit.rate_constant_se,
                "rate_constant_ci95_low": fit.rate_constant_ci95_low,
                "rate_constant_ci95_high": fit.rate_constant_ci95_high,
                "half_life": fit.half_life, "t90": fit.t90, "t95": fit.t95,
                "plateau_area": fit.plateau_area, "lag_time": fit.lag_time,
                "fit_rmse": fit.fit_rmse, "r_squared": fit.r_squared,
                "aic": fit.aic, "aicc": fit.aicc, "bic": fit.bic,
                "durbin_watson": fit.durbin_watson,
                "residual_lag1_autocorrelation": fit.residual_lag1_autocorrelation,
                "ljung_box_pvalue": fit.ljung_box_pvalue,
                "fit_qc_pass": fit.fit_qc_pass,
                "fit_qc_failure_reasons": fit.fit_qc_failure_reasons,
            })
        if best:
            best_fit = next(f for f in fits if f.kinetic_model == best)
            report.kinetic_best[key] = {
                "target": target, "model": best, "params": best_fit.params,
            }
    report.tables["kinetic_fits.csv"] = (_KINETIC_COLUMNS, rows)
