"""Focused one-peak metrics, QC, normalization, rates, and completion report."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Sequence

from .completion import CompletionResult, detect_completion
from .target_peak_config import TargetPeakConfig
from .timing_comparison import (
    TIMING_COMPARISON_COLUMNS,
    build_timing_comparison_rows,
)
from .time_series import (
    FixedRegion,
    elapsed_hours,
    integrate_fixed_region,
    series_rates,
    white_noise_area_standard_error,
)


def _np():
    import numpy as np

    return np


def _z_value(confidence: float) -> float:
    from scipy import stats

    return float(stats.norm.ppf(0.5 + 0.5 * confidence))


@dataclass
class TargetPeakAnalysis:
    """Tables and arrays needed by target-peak plot rendering."""

    config: TargetPeakConfig
    measurements: list[dict]
    rates: list[dict]
    normalized: list[dict]
    decision_trace: list[dict]
    completion: CompletionResult
    spectra_long: list[dict]
    spectrum_grid_ppm: object
    spectrum_grid_intensity: object
    timing_comparison: list[dict] = field(default_factory=list)
    tables: dict[str, tuple[list[str], list[dict]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


_MEASUREMENT_COLUMNS = [
    "file", "source_file", "spectrum_index", "timestamp", "timestamp_source",
    "elapsed_time_hours", "elapsed_time_source", "integration_left_ppm",
    "integration_right_ppm", "area", "signed_area", "area_standard_error",
    "area_ci_low", "area_ci_high", "uncertainty_method", "peak_center_ppm",
    "peak_drift_ppm", "peak_height", "snr", "prominence", "prominence_snr",
    "fwhm_ppm", "fwhm_hz", "local_baseline_noise", "integration_edge_contact",
    "nearby_peak_overlap", "tracked_peak_found", "peak_quality_pass",
    "analysis_quality_pass", "quality_warnings",
]

_RATE_COLUMNS = [
    "spectrum_index", "elapsed_time_hours", "area", "delta_area",
    "delta_time_hours", "area_rate_per_hour", "percent_change_per_interval",
    "relative_rate_percent_per_hour", "rolling_slope_per_hour",
    "rolling_slope_standard_error", "rolling_slope_ci_low",
    "rolling_slope_ci_high", "completion_threshold_low",
    "completion_threshold_high",
]

_NORMALIZED_COLUMNS = [
    "spectrum_index", "elapsed_time_hours", "normalization_mode", "normalized_area",
]

_DECISION_COLUMNS = [
    "spectrum_index", "elapsed_time_hours", "timestamp", "status",
    "trend_direction", "complete", "completion_index", "completion_elapsed_hours",
    "reason", "evidence_level", "recent_slope_per_hour", "stable_interval_count",
    "low_signal", "quality_warnings",
]

_SPECTRA_COLUMNS = [
    "spectrum_index", "file", "timestamp", "elapsed_time_hours", "ppm", "intensity",
]


def _select_peak(spectrum, config: TargetPeakConfig):
    lo, hi = config.search_window_ppm
    candidates = [p for p in spectrum.peaks if lo <= p.center_ppm <= hi]
    bounded = [
        p
        for p in candidates
        if abs(p.center_ppm - config.expected_center_ppm) <= config.tracking_max_drift_ppm
    ]
    if not bounded:
        return None, candidates
    return min(bounded, key=lambda p: abs(p.center_ppm - config.expected_center_ppm)), candidates


def _normalizations(areas, modes: Sequence[str]) -> dict[str, object]:
    np = _np()
    values = np.asarray(areas, dtype=float)
    finite = values[np.isfinite(values)]
    result: dict[str, object] = {}
    for mode in modes:
        out = np.full(values.shape, np.nan, dtype=float)
        if finite.size == 0:
            result[mode] = out
            continue
        if mode == "fraction_of_max":
            denominator = float(np.nanmax(values))
            if abs(denominator) > 1e-12:
                out = values / denominator
        elif mode == "relative_to_first":
            first_index = int(np.flatnonzero(np.isfinite(values))[0])
            denominator = float(values[first_index])
            if abs(denominator) > 1e-12:
                out = values / denominator
        elif mode == "zero_to_one":
            lo, hi = float(np.nanmin(values)), float(np.nanmax(values))
            if hi - lo > 1e-12:
                out = (values - lo) / (hi - lo)
        result[mode] = out
    return result


def build_target_peak_analysis(
    spectra: Sequence,
    config: TargetPeakConfig,
) -> TargetPeakAnalysis:
    """Calculate a complete focused report from already processed spectra."""

    np = _np()
    spectra = list(spectra)
    elapsed = elapsed_hours([s.timestamp for s in spectra])
    region = FixedRegion("target_peak", *config.integration_window_ppm)
    z = _z_value(config.completion.confidence_level)
    measurements: list[dict] = []
    warnings: list[str] = []

    for index, spectrum in enumerate(spectra):
        integral = integrate_fixed_region(spectrum.full_ppm, spectrum.full_intensity, region)
        se = white_noise_area_standard_error(
            spectrum.full_ppm,
            region.left_ppm,
            region.right_ppm,
            integral.region_noise,
        )
        peak, candidates = _select_peak(spectrum, config)
        peak_warnings: list[str] = []
        center = height = snr = prominence = prominence_snr = width = width_hz = float("nan")
        edge = overlap = False
        peak_quality = False
        if peak is None:
            peak_warnings.append("no bounded target-peak candidate")
        else:
            center = float(peak.center_ppm)
            height = float(peak.height)
            snr = float(peak.snr)
            prominence = float(peak.prominence)
            prominence_snr = float(peak.prominence_snr)
            width = float(peak.width_ppm)
            width_hz = float(peak.width_hz)
            half_width = 0.5 * width
            edge = bool(
                center - half_width <= region.left_ppm + config.integration_edge_margin_ppm
                or center + half_width >= region.right_ppm - config.integration_edge_margin_ppm
            )
            others = [other for other in candidates if other is not peak]
            overlap = any(
                abs(other.center_ppm - center) <= 0.5 * (other.width_ppm + width) * 1.5
                for other in others
            )
            if snr < config.minimum_snr:
                peak_warnings.append(f"SNR {snr:.3g} below {config.minimum_snr:g}")
            if prominence_snr < config.minimum_prominence_snr:
                peak_warnings.append(
                    f"prominence SNR {prominence_snr:.3g} below {config.minimum_prominence_snr:g}"
                )
            if edge:
                peak_warnings.append("peak width contacts integration-window edge")
            if overlap:
                peak_warnings.append("nearby candidate may overlap target peak")
            peak_quality = bool(
                snr >= config.minimum_snr
                and prominence_snr >= config.minimum_prominence_snr
                and not edge
                and not overlap
            )
        analysis_quality = bool(integral.qc_pass and np.isfinite(integral.positive_area))
        if not integral.qc_pass:
            peak_warnings.append(integral.qc_failure_reasons)
        timestamp_text = spectrum.timestamp.isoformat() if spectrum.timestamp else ""
        measurements.append(
            {
                "file": spectrum.file,
                "source_file": spectrum.source_path,
                "spectrum_index": spectrum.spectrum_index,
                "timestamp": timestamp_text,
                "timestamp_source": spectrum.timestamp_source or "none",
                "elapsed_time_hours": elapsed[index],
                "elapsed_time_source": (
                    f"difference from first timestamp ({spectrum.timestamp_source})"
                    if spectrum.timestamp
                    else "measurement index fallback"
                ),
                "integration_left_ppm": region.left_ppm,
                "integration_right_ppm": region.right_ppm,
                "area": integral.positive_area,
                "signed_area": integral.signed_area,
                "area_standard_error": se,
                "area_ci_low": integral.positive_area - z * se,
                "area_ci_high": integral.positive_area + z * se,
                "uncertainty_method": "white-noise propagation; approximate lower-bound normal interval",
                "peak_center_ppm": center,
                "peak_drift_ppm": center - config.expected_center_ppm,
                "peak_height": height,
                "snr": snr,
                "prominence": prominence,
                "prominence_snr": prominence_snr,
                "fwhm_ppm": width,
                "fwhm_hz": width_hz,
                "local_baseline_noise": integral.region_noise,
                "integration_edge_contact": edge,
                "nearby_peak_overlap": overlap,
                "tracked_peak_found": peak is not None,
                "peak_quality_pass": peak_quality,
                "analysis_quality_pass": analysis_quality,
                "quality_warnings": "; ".join(filter(None, peak_warnings)),
            }
        )

    areas = [float(row["area"]) for row in measurements]
    completion_quality = [
        bool(row["analysis_quality_pass"] and row["peak_quality_pass"])
        for row in measurements
    ]
    # A genuinely disappearing peak should not fail merely because it becomes
    # undetectable.  Low fixed-window area with a valid integration is acceptable.
    max_area = max(areas) if areas else float("nan")
    low_limit = (
        config.completion.low_area_absolute
        if config.completion.low_area_absolute is not None
        else config.completion.low_area_fraction_of_max * max_area
    )
    for i, row in enumerate(measurements):
        if row["analysis_quality_pass"] and row["area"] <= low_limit:
            completion_quality[i] = True

    timestamps = [row["timestamp"] or None for row in measurements]
    completion = detect_completion(
        elapsed,
        areas,
        quality_pass=completion_quality,
        timestamps=timestamps,
        config=config.completion,
    )

    computed_rates = series_rates(elapsed, areas, rolling_window=config.rolling_window)
    denominator_floor = config.completion.percent_denominator_floor_fraction * max(max_area, 1e-12)
    rates: list[dict] = []
    for index, rate in enumerate(computed_rates):
        percent = float("nan")
        if index > 0 and abs(areas[index - 1]) >= denominator_floor:
            percent = 100.0 * (areas[index] - areas[index - 1]) / areas[index - 1]
        rates.append(
            {
                "spectrum_index": index,
                "elapsed_time_hours": elapsed[index],
                "area": areas[index],
                "delta_area": rate.delta_area,
                "delta_time_hours": rate.delta_time_hours,
                "area_rate_per_hour": rate.absolute_rate_per_hour,
                "percent_change_per_interval": percent,
                "relative_rate_percent_per_hour": rate.relative_rate_percent_per_hour,
                "rolling_slope_per_hour": rate.rolling_slope,
                "rolling_slope_standard_error": rate.rolling_slope_se,
                "rolling_slope_ci_low": rate.rolling_slope_ci95_low,
                "rolling_slope_ci_high": rate.rolling_slope_ci95_high,
                "completion_threshold_low": -config.completion.absolute_slope_threshold_per_hour,
                "completion_threshold_high": config.completion.absolute_slope_threshold_per_hour,
            }
        )

    normalized: list[dict] = []
    for mode, values in _normalizations(areas, config.normalization_modes).items():
        for index, value in enumerate(values):
            normalized.append(
                {
                    "spectrum_index": index,
                    "elapsed_time_hours": elapsed[index],
                    "normalization_mode": mode,
                    "normalized_area": float(value),
                }
            )

    decision_trace: list[dict] = []
    for end in range(1, len(areas) + 1):
        decision = detect_completion(
            elapsed[:end], areas[:end], quality_pass=completion_quality[:end],
            timestamps=timestamps[:end], config=config.completion,
        )
        decision_trace.append(
            {
                "spectrum_index": end - 1,
                "elapsed_time_hours": elapsed[end - 1],
                "timestamp": timestamps[end - 1] or "",
                "status": decision.status,
                "trend_direction": decision.trend_direction,
                "complete": decision.complete,
                "completion_index": decision.completion_index,
                "completion_elapsed_hours": decision.completion_elapsed_hours,
                "reason": decision.reason,
                "evidence_level": decision.evidence_level,
                "recent_slope_per_hour": decision.metrics.get("recent_slope_per_hour"),
                "stable_interval_count": decision.metrics.get("stable_interval_count"),
                "low_signal": decision.metrics.get("low_signal"),
                "quality_warnings": "; ".join(decision.quality_warnings),
            }
        )

    grid_ppm = np.linspace(config.plot_window_ppm[0], config.plot_window_ppm[1], 1000)
    grid_rows: list[object] = []
    spectra_long: list[dict] = []
    for index, spectrum in enumerate(spectra):
        ppm = np.asarray(spectrum.full_ppm, dtype=float)
        intensity = np.asarray(spectrum.full_intensity, dtype=float)
        order = np.argsort(ppm)
        interpolated = np.interp(grid_ppm, ppm[order], intensity[order])
        grid_rows.append(interpolated)
        spectra_long.extend(
            {
                "spectrum_index": index,
                "file": spectrum.file,
                "timestamp": timestamps[index] or "",
                "elapsed_time_hours": elapsed[index],
                "ppm": float(ppm_value),
                "intensity": float(intensity_value),
            }
            for ppm_value, intensity_value in zip(grid_ppm, interpolated)
        )

    completion_row = completion.as_dict()
    completion_row["metrics"] = str(completion_row["metrics"])
    completion_row["thresholds"] = str(completion_row["thresholds"])
    completion_row["quality_warnings"] = "; ".join(completion.quality_warnings)
    completion_columns = list(completion_row)
    stage_rows = [asdict(stage) for stage in config.stages]
    timing_comparison = build_timing_comparison_rows(measurements)
    invalid_timing = [
        row for row in timing_comparison if not row["comparison_qc_pass"]
    ]
    if invalid_timing:
        warnings.append(
            f"{len(invalid_timing)} acquisition(s) excluded from timing-comparison plots"
        )
    tables = {
        "target_peak_measurements.csv": (_MEASUREMENT_COLUMNS, measurements),
        "target_peak_rates.csv": (_RATE_COLUMNS, rates),
        "target_peak_normalized.csv": (_NORMALIZED_COLUMNS, normalized),
        "target_peak_completion_trace.csv": (_DECISION_COLUMNS, decision_trace),
        "target_peak_completion.csv": (completion_columns, [completion_row]),
        "target_peak_spectra_long.csv": (_SPECTRA_COLUMNS, spectra_long),
        "target_peak_timing_comparison.csv": (
            TIMING_COMPARISON_COLUMNS,
            timing_comparison,
        ),
        "target_peak_stages.csv": (
            ["label", "start_hours", "end_hours", "expected_direction"], stage_rows
        ),
    }
    return TargetPeakAnalysis(
        config=config,
        measurements=measurements,
        rates=rates,
        normalized=normalized,
        decision_trace=decision_trace,
        completion=completion,
        spectra_long=spectra_long,
        spectrum_grid_ppm=grid_ppm,
        spectrum_grid_intensity=np.asarray(grid_rows, dtype=float),
        timing_comparison=timing_comparison,
        tables=tables,
        warnings=warnings,
    )
