"""Conservative, plot-only residual baseline flattening for NMR overlays.

The primary processing pipeline remains authoritative.  This module accepts the
already phase-corrected, referenced/aligned, baseline-corrected real spectrum,
selects the requested regional window, and optionally removes only a constant
or straight-line residual for display.  It never clips, takes an absolute
value, normalizes, or mutates the quantitative input array.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


class FlattenedOverlayConfigError(ValueError):
    """Raised when the flattened-overlay configuration is unsafe or invalid."""


@dataclass(frozen=True)
class ResidualBaselineConfig:
    enabled: bool = True
    method: str = "robust_linear"
    polynomial_order: int = 1
    exclude_detected_peaks: bool = True
    peak_guard_width_ppm: float = 0.05
    excluded_regions_ppm: tuple[tuple[float, float], ...] = ((5.70, 5.90),)


@dataclass(frozen=True)
class FlattenedOverlayConfig:
    enabled: bool = True
    use_authoritative_real_trace: bool = True
    residual_baseline: ResidualBaselineConfig = ResidualBaselineConfig()


@dataclass(frozen=True)
class ResidualBaselineResult:
    """One authoritative regional trace and its plot-only flattened copy."""

    ppm: object
    before: object
    estimated_baseline: object
    after: object
    baseline_mask: object
    metrics: dict[str, object]


QC_COLUMNS = (
    "file",
    "sequence_label",
    "baseline_median_before",
    "baseline_median_after",
    "baseline_mean_after",
    "baseline_sd_after",
    "baseline_mad_after",
    "baseline_slope_before",
    "baseline_slope_after",
    "baseline_points_used",
    "baseline_flattening_method",
    "baseline_flattening_qc_pass",
    "baseline_flattening_qc_reason",
)


def load_flattened_overlay_config(raw: dict | None) -> FlattenedOverlayConfig:
    """Validate an optional ``plots.flattened_overlay`` YAML mapping."""

    section = _mapping(raw, "plots.flattened_overlay")
    _check_keys(
        section,
        {"enabled", "use_authoritative_real_trace", "residual_baseline"},
        "plots.flattened_overlay",
    )
    use_real = bool(section.get("use_authoritative_real_trace", True))
    if not use_real:
        raise FlattenedOverlayConfigError(
            "plots.flattened_overlay.use_authoritative_real_trace must be true"
        )

    residual_raw = _mapping(
        section.get("residual_baseline"),
        "plots.flattened_overlay.residual_baseline",
    )
    _check_keys(
        residual_raw,
        {
            "enabled",
            "method",
            "polynomial_order",
            "exclude_detected_peaks",
            "peak_guard_width_ppm",
            "excluded_regions_ppm",
        },
        "plots.flattened_overlay.residual_baseline",
    )
    method = str(residual_raw.get("method", "robust_linear"))
    if method not in {"robust_constant", "robust_linear"}:
        raise FlattenedOverlayConfigError(
            "residual baseline method must be robust_constant or robust_linear"
        )
    order = int(residual_raw.get("polynomial_order", 1))
    expected_order = 0 if method == "robust_constant" else 1
    if order != expected_order:
        raise FlattenedOverlayConfigError(
            f"{method} requires polynomial_order: {expected_order}"
        )
    guard = float(residual_raw.get("peak_guard_width_ppm", 0.05))
    if not guard > 0:
        raise FlattenedOverlayConfigError(
            "plots.flattened_overlay.residual_baseline.peak_guard_width_ppm "
            "must be positive"
        )
    excluded = _excluded_regions(
        residual_raw.get("excluded_regions_ppm", ((5.70, 5.90),))
    )
    return FlattenedOverlayConfig(
        enabled=bool(section.get("enabled", True)),
        use_authoritative_real_trace=True,
        residual_baseline=ResidualBaselineConfig(
            enabled=bool(residual_raw.get("enabled", True)),
            method=method,
            polynomial_order=order,
            exclude_detected_peaks=bool(
                residual_raw.get("exclude_detected_peaks", True)
            ),
            peak_guard_width_ppm=guard,
            excluded_regions_ppm=excluded,
        ),
    )


def regional_authoritative_trace(
    ppm_axis,
    authoritative_real_intensity,
    region_min_ppm: float,
    region_max_ppm: float,
) -> tuple[object, object]:
    """Copy a regional slice from the same real-intensity array used in plots."""

    import numpy as np

    ppm = np.asarray(ppm_axis, dtype=float)
    intensity = np.asarray(authoritative_real_intensity, dtype=float)
    if ppm.ndim != 1 or intensity.shape != ppm.shape:
        raise ValueError("ppm and authoritative real intensity must be matching 1-D arrays")
    lo, hi = sorted((float(region_min_ppm), float(region_max_ppm)))
    mask = (ppm >= lo) & (ppm <= hi) & np.isfinite(ppm) & np.isfinite(intensity)
    if np.count_nonzero(mask) < 8:
        raise ValueError(f"not enough authoritative points in {lo:g}..{hi:g} ppm")
    return ppm[mask].copy(), intensity[mask].copy()


def flatten_residual_baseline(
    ppm_axis,
    authoritative_real_intensity,
    *,
    config: ResidualBaselineConfig | None = None,
    detected_peak_ppm: Iterable[float] = (),
) -> ResidualBaselineResult:
    """Remove a robust constant/line from signal-free points for display only."""

    import numpy as np

    cfg = config or ResidualBaselineConfig()
    ppm = np.asarray(ppm_axis, dtype=float)
    before = np.asarray(authoritative_real_intensity, dtype=float)
    if ppm.ndim != 1 or before.shape != ppm.shape or ppm.size < 8:
        raise ValueError("ppm and intensity must be matching 1-D arrays with >= 8 points")
    if cfg.polynomial_order not in {0, 1}:
        raise ValueError("residual baseline polynomial order must be 0 or 1")

    finite = np.isfinite(ppm) & np.isfinite(before)
    baseline_mask = finite.copy()
    for left, right in cfg.excluded_regions_ppm:
        lo, hi = sorted((float(left), float(right)))
        baseline_mask &= ~((ppm >= lo) & (ppm <= hi))
    if cfg.exclude_detected_peaks:
        for center in detected_peak_ppm:
            if center is None or not np.isfinite(float(center)):
                continue
            baseline_mask &= (
                np.abs(ppm - float(center)) > float(cfg.peak_guard_width_ppm)
            )

    required = max(8, cfg.polynomial_order + 3)
    if np.count_nonzero(baseline_mask) < required:
        raise ValueError("too few signal-free points after configured exclusions")

    if cfg.enabled:
        # First robust fit identifies any additional signal-like excursions.
        coefficients, selected = _robust_polynomial(
            ppm, before, baseline_mask, cfg.polynomial_order
        )
        if cfg.exclude_detected_peaks:
            preliminary = before - np.polyval(coefficients, ppm - float(np.median(ppm)))
            residual_center = float(np.median(preliminary[selected]))
            residual_mad = float(
                np.median(np.abs(preliminary[selected] - residual_center))
            )
            residual_noise = max(1.4826 * residual_mad, _epsilon_scale(before))
            signal_points = baseline_mask & (
                np.abs(preliminary - residual_center) > 4.0 * residual_noise
            )
            for center in ppm[signal_points]:
                baseline_mask &= (
                    np.abs(ppm - float(center)) > float(cfg.peak_guard_width_ppm)
                )
        if np.count_nonzero(baseline_mask) < required:
            raise ValueError("automatic peak guards left too few baseline points")
        coefficients, selected = _robust_polynomial(
            ppm, before, baseline_mask, cfg.polynomial_order
        )
        baseline_mask = selected
        centered_ppm = ppm - float(np.median(ppm))
        estimated = np.polyval(coefficients, centered_ppm)
        after = before - estimated
        method = cfg.method
    else:
        estimated = np.zeros_like(before)
        after = before.copy()
        method = "disabled"

    baseline_before = before[baseline_mask]
    baseline_after = after[baseline_mask]
    slope_before = _slope(ppm[baseline_mask], baseline_before)
    slope_after = _slope(ppm[baseline_mask], baseline_after)
    median_before = float(np.median(baseline_before))
    median_after = float(np.median(baseline_after))
    mean_after = float(np.mean(baseline_after))
    sd_after = float(np.std(baseline_after, ddof=1)) if baseline_after.size > 1 else 0.0
    mad_after = float(np.median(np.abs(baseline_after - median_after)))
    span = max(float(np.ptp(ppm[baseline_mask])), _epsilon_scale(ppm))
    median_limit = max(0.25 * sd_after, _epsilon_scale(before))
    slope_limit = max(
        0.25 * abs(slope_before),
        0.05 * max(sd_after, _epsilon_scale(before)) / span,
    )
    median_pass = abs(median_after) <= median_limit
    slope_pass = abs(slope_after) <= slope_limit

    # A constant or line has no local extremum and therefore cannot create a
    # localized trough beside the protected 5.785 ppm feature.
    protected = (ppm >= 5.70) & (ppm <= 5.90)
    protected_baseline = estimated[protected]
    curvature = (
        float(np.max(np.abs(np.diff(protected_baseline, n=2))))
        if protected_baseline.size >= 3
        else 0.0
    )
    curvature_pass = curvature <= 1e-10 * max(
        float(np.max(np.abs(estimated))) if estimated.size else 0.0,
        1.0,
    )
    qc_pass = bool(median_pass and slope_pass and curvature_pass)
    reasons = []
    if not median_pass:
        reasons.append("post_median_not_close_to_zero")
    if not slope_pass:
        reasons.append("residual_slope_not_materially_reduced")
    if not curvature_pass:
        reasons.append("correction_curvature_near_5p785")
    if not reasons:
        reasons.append("median_centered;slope_reduced;no_local_trough_from_linear_fit")

    metrics = {
        "baseline_median_before": median_before,
        "baseline_median_after": median_after,
        "baseline_mean_after": mean_after,
        "baseline_sd_after": sd_after,
        "baseline_mad_after": mad_after,
        "baseline_slope_before": slope_before,
        "baseline_slope_after": slope_after,
        "baseline_points_used": int(np.count_nonzero(baseline_mask)),
        "baseline_flattening_method": method,
        "baseline_flattening_qc_pass": qc_pass,
        "baseline_flattening_qc_reason": ";".join(reasons),
    }
    return ResidualBaselineResult(
        ppm=ppm.copy(),
        before=before.copy(),
        estimated_baseline=np.asarray(estimated, dtype=float),
        after=np.asarray(after, dtype=float),
        baseline_mask=baseline_mask.copy(),
        metrics=metrics,
    )


def _robust_polynomial(ppm, values, candidate_mask, order: int):
    import numpy as np

    centered = ppm - float(np.median(ppm))
    selected = np.asarray(candidate_mask, dtype=bool).copy()
    required = max(8, order + 3)
    coefficients = np.polyfit(centered[selected], values[selected], order)
    for _ in range(12):
        coefficients = np.polyfit(centered[selected], values[selected], order)
        residual = values - np.polyval(coefficients, centered)
        center = float(np.median(residual[selected]))
        mad = float(np.median(np.abs(residual[selected] - center)))
        noise = max(1.4826 * mad, _epsilon_scale(values))
        updated = candidate_mask & (np.abs(residual - center) <= 4.0 * noise)
        if np.count_nonzero(updated) < required or np.array_equal(updated, selected):
            break
        selected = updated
    return coefficients, selected


def _slope(ppm, values) -> float:
    import numpy as np

    if len(ppm) < 2 or float(np.ptp(ppm)) == 0:
        return 0.0
    return float(np.polyfit(np.asarray(ppm, dtype=float), values, 1)[0])


def _epsilon_scale(values) -> float:
    import numpy as np

    array = np.asarray(values, dtype=float)
    scale = float(np.nanmax(np.abs(array))) if array.size else 1.0
    return max(scale, 1.0) * np.finfo(float).eps * 100.0


def _mapping(value: Any, label: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FlattenedOverlayConfigError(f"{label} must be a mapping")
    return value


def _check_keys(section: dict, allowed: set[str], label: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise FlattenedOverlayConfigError(
            f"Unknown key(s) in [{label}]: {', '.join(unknown)}"
        )


def _excluded_regions(raw: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw, (list, tuple)):
        raise FlattenedOverlayConfigError("excluded_regions_ppm must be a list")
    regions = []
    for index, pair in enumerate(raw):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise FlattenedOverlayConfigError(
                f"excluded_regions_ppm[{index}] must contain exactly two ppm values"
            )
        left, right = float(pair[0]), float(pair[1])
        if left == right:
            raise FlattenedOverlayConfigError(
                f"excluded_regions_ppm[{index}] has equal bounds"
            )
        regions.append((left, right))
    return tuple(regions)
