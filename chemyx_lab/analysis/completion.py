"""Configurable, auditable completion decisions for one NMR peak time series.

This is a decision-support framework, not a scientifically validated reaction
endpoint model.  It deliberately requires several observations and several
stable intervals; one point can never declare completion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from .target_peak_config import CompletionConfig
from .time_series import ols_slope


STATUSES = {
    "growing",
    "decreasing",
    "growth_plateau",
    "low_signal_plateau",
    "stable",
    "reversal",
    "insufficient_data",
    "poor_quality",
    "unresolved",
}


@dataclass(frozen=True)
class CompletionResult:
    """Serializable result of evaluating a peak time series."""

    status: str
    trend_direction: str
    complete: bool
    completion_index: int | None
    completion_elapsed_hours: float | None
    completion_timestamp: str | None
    reason: str
    evidence_level: str
    metrics: dict[str, float | int | bool | str | None]
    thresholds: dict[str, float | int | bool | str | None]
    quality_warnings: tuple[str, ...]

    def as_dict(self) -> dict:
        """Return a JSON-friendly mapping."""

        value = asdict(self)
        value["quality_warnings"] = list(self.quality_warnings)
        return value


def _np():
    import numpy as np

    return np


def _current_decision(
    times: Sequence[float],
    areas: Sequence[float],
    quality_pass: Sequence[bool],
    config: CompletionConfig,
) -> tuple[str, str, bool, str, str, dict, list[str]]:
    """Evaluate only the current prefix; used repeatedly for online replay."""

    np = _np()
    t = np.asarray(times, dtype=float)
    a = np.asarray(areas, dtype=float)
    q = np.asarray(quality_pass, dtype=bool)
    finite = np.isfinite(t) & np.isfinite(a)
    t, a, q = t[finite], a[finite], q[finite]
    n = int(a.size)
    metrics: dict[str, float | int | bool | str | None] = {"observations": n}
    warnings: list[str] = []
    if n < config.minimum_observations:
        return (
            "insufficient_data", "unresolved", False,
            f"{n} observations; need {config.minimum_observations}", "none",
            metrics, warnings,
        )
    elapsed = float(t[-1] - t[0])
    metrics["elapsed_hours"] = elapsed
    if elapsed < config.minimum_elapsed_hours:
        return (
            "insufficient_data", "unresolved", False,
            f"{elapsed:.3g} h elapsed; need {config.minimum_elapsed_hours:g} h",
            "none", metrics, warnings,
        )

    window = min(config.recent_window, n)
    if int(np.count_nonzero(q[-window:])) < window:
        warnings.append("one or more recent measurements failed peak-quality checks")
        return (
            "poor_quality", "unresolved", False,
            "recent data do not meet configured peak-quality criteria", "low",
            metrics, warnings,
        )

    scale = max(float(np.nanmax(np.abs(a))), 1e-12)
    floor = config.percent_denominator_floor_fraction * scale
    delta = np.diff(a)
    pct = np.full(delta.shape, np.nan, dtype=float)
    denominators = np.abs(a[:-1])
    valid_pct = denominators >= floor
    pct[valid_pct] = 100.0 * delta[valid_pct] / a[:-1][valid_pct]
    recent_count = min(config.consecutive_stable_measurements, len(delta))
    recent_pct = pct[-recent_count:]
    recent_delta = delta[-recent_count:]
    stable_each = np.isfinite(recent_pct) & (
        np.abs(recent_pct) <= config.percent_change_threshold
    )
    # Near zero values cannot produce stable percentages.  For low-signal
    # disappearance, use an absolute area increment scaled to the observed run.
    low_delta_limit = config.percent_change_threshold / 100.0 * scale
    stable_each = stable_each | (
        ~np.isfinite(recent_pct) & (np.abs(recent_delta) <= low_delta_limit)
    )
    stable_changes = bool(recent_count >= config.consecutive_stable_measurements and np.all(stable_each))

    fit = ols_slope(t[-window:], a[-window:], confidence=config.confidence_level)
    mean_recent = max(abs(float(np.nanmean(a[-window:]))), floor, 1e-12)
    relative_slope = 100.0 * fit.slope / mean_recent
    slope_near_zero = bool(
        np.isfinite(fit.slope)
        and abs(fit.slope) <= config.absolute_slope_threshold_per_hour
        and abs(relative_slope) <= config.relative_slope_threshold_percent_per_hour
    )
    ci_inside = bool(
        np.isfinite(fit.slope_ci95_low)
        and np.isfinite(fit.slope_ci95_high)
        and fit.slope_ci95_low >= -config.absolute_slope_threshold_per_hour
        and fit.slope_ci95_high <= config.absolute_slope_threshold_per_hour
    )

    early_end = max(2, n - window + 1)
    early = a[:early_end]
    growth_strength = float((np.nanmax(early) - early[0]) / scale)
    decrease_strength = float((early[0] - np.nanmin(early)) / scale)
    had_growth = growth_strength >= config.meaningful_trend_fraction
    had_decrease = decrease_strength >= config.meaningful_trend_fraction
    if had_growth and not had_decrease:
        historical = "growth"
    elif had_decrease and not had_growth:
        historical = "disappearance"
    elif had_growth and had_decrease:
        historical = "mixed"
    else:
        historical = "unresolved"

    low_limit = (
        config.low_area_absolute
        if config.low_area_absolute is not None
        else config.low_area_fraction_of_max * scale
    )
    low_signal = bool(a[-1] <= low_limit)
    reversal_n = min(config.reversal_consecutive_measurements, len(pct))
    recent_reversal_pct = pct[-reversal_n:]
    reversal = False
    if reversal_n >= config.reversal_consecutive_measurements and np.all(np.isfinite(recent_reversal_pct)):
        if historical == "growth":
            reversal = bool(np.all(recent_reversal_pct < -config.percent_change_threshold))
        elif historical == "disappearance":
            reversal = bool(np.all(recent_reversal_pct > config.percent_change_threshold))

    metrics.update(
        {
            "recent_window": window,
            "recent_slope_per_hour": float(fit.slope),
            "recent_slope_se": float(fit.slope_se),
            "recent_slope_ci_low": float(fit.slope_ci95_low),
            "recent_slope_ci_high": float(fit.slope_ci95_high),
            "recent_relative_slope_percent_per_hour": float(relative_slope),
            "stable_interval_count": int(np.count_nonzero(stable_each)),
            "stable_changes": stable_changes,
            "slope_near_zero": slope_near_zero,
            "slope_ci_inside_threshold": ci_inside,
            "historical_growth_strength": growth_strength,
            "historical_decrease_strength": decrease_strength,
            "low_signal_limit": float(low_limit),
            "low_signal": low_signal,
            "last_area": float(a[-1]),
            "maximum_area": scale,
        }
    )

    if reversal:
        return (
            "reversal", "mixed", False,
            "recent changes consistently oppose the earlier trend", "moderate",
            metrics, warnings,
        )
    if historical == "growth" and stable_changes and slope_near_zero:
        evidence = "high" if ci_inside else "moderate"
        if not ci_inside:
            warnings.append("recent slope CI is wider than the equivalence band")
        return (
            "growth_plateau", "growth", True,
            "earlier growth followed by consecutive small changes and a near-zero recent slope",
            evidence, metrics, warnings,
        )
    if historical == "disappearance" and low_signal and stable_changes and slope_near_zero:
        evidence = "high" if ci_inside else "moderate"
        if not ci_inside:
            warnings.append("recent slope CI is wider than the equivalence band")
        return (
            "low_signal_plateau", "disappearance", True,
            "earlier decrease followed by persistently low area and a near-zero recent slope",
            evidence, metrics, warnings,
        )
    if historical == "unresolved" and stable_changes and slope_near_zero:
        return (
            "stable", "unresolved", False,
            "series is stable but has no earlier meaningful growth or decrease",
            "moderate" if ci_inside else "low", metrics, warnings,
        )
    if fit.slope > config.absolute_slope_threshold_per_hour:
        return "growing", "growth", False, "recent slope remains positive", "moderate", metrics, warnings
    if fit.slope < -config.absolute_slope_threshold_per_hour:
        return "decreasing", "disappearance", False, "recent slope remains negative", "moderate", metrics, warnings
    return (
        "unresolved", historical, False,
        "completion criteria are not simultaneously satisfied", "low",
        metrics, warnings,
    )


def detect_completion(
    times_hours: Sequence[float],
    areas: Sequence[float],
    *,
    quality_pass: Sequence[bool] | None = None,
    timestamps: Sequence[str | None] | None = None,
    config: CompletionConfig | None = None,
) -> CompletionResult:
    """Replay the run and return the first defensible completion decision.

    Replaying prefixes matches online use: a completion at point *i* depends
    only on observations available through *i*.  Later points are retained for
    audit and produce a warning if they materially depart from the endpoint.
    """

    cfg = config or CompletionConfig()
    n = len(areas)
    if len(times_hours) != n:
        raise ValueError("times_hours and areas must have equal length")
    quality = list(quality_pass) if quality_pass is not None else [True] * n
    stamps = list(timestamps) if timestamps is not None else [None] * n
    if len(quality) != n or len(stamps) != n:
        raise ValueError("quality_pass and timestamps must match areas")
    thresholds = asdict(cfg)
    if n == 0:
        return CompletionResult(
            status="insufficient_data",
            trend_direction="unresolved",
            complete=False,
            completion_index=None,
            completion_elapsed_hours=None,
            completion_timestamp=None,
            reason=f"0 observations; need {cfg.minimum_observations}",
            evidence_level="none",
            metrics={"observations": 0},
            thresholds=thresholds,
            quality_warnings=("no spectra available",),
        )
    latest = None
    for end in range(1, n + 1):
        latest = _current_decision(
            times_hours[:end], areas[:end], quality[:end], cfg
        )
        status, direction, complete, reason, evidence, metrics, warnings = latest
        if complete:
            post_warnings = list(warnings)
            if end < n:
                np = _np()
                endpoint = float(areas[end - 1])
                later = np.asarray(areas[end:], dtype=float)
                scale = max(float(np.nanmax(np.abs(areas))), 1e-12)
                if np.any(np.abs(later - endpoint) > cfg.percent_change_threshold / 100.0 * scale):
                    post_warnings.append(
                        "post-completion measurements depart from the detected plateau; retain for review"
                    )
            return CompletionResult(
                status=status,
                trend_direction=direction,
                complete=True,
                completion_index=end - 1,
                completion_elapsed_hours=float(times_hours[end - 1]),
                completion_timestamp=stamps[end - 1],
                reason=reason,
                evidence_level=evidence,
                metrics=metrics,
                thresholds=thresholds,
                quality_warnings=tuple(post_warnings),
            )

    assert latest is not None
    status, direction, complete, reason, evidence, metrics, warnings = latest
    return CompletionResult(
        status=status,
        trend_direction=direction,
        complete=complete,
        completion_index=None,
        completion_elapsed_hours=None,
        completion_timestamp=None,
        reason=reason,
        evidence_level=evidence,
        metrics=metrics,
        thresholds=thresholds,
        quality_warnings=tuple(warnings),
    )
