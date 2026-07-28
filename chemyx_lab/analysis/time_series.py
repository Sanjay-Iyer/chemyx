"""Time-series analysis for a spectrum series: fixed regions, rates, plateau.

Three responsibilities, all driven by **real elapsed time** (from acquisition
timestamps), never by assuming equal sampling intervals:

1. **Fixed-window integration** -- integrate identical, chemically-defined ppm
   boundaries across every (aligned) spectrum, so a change in area reflects
   chemistry rather than the peak picker moving its own integration limits from
   spectrum to spectrum.  Both signed and positive area are preserved.
2. **Rates** -- finite-difference rates using the true irregular time steps,
   plus a rolling ordinary-least-squares slope with a confidence interval.
3. **Statistical plateau detection** -- declare a plateau only when the whole
   95% confidence interval of the recent slope lies inside a configurable
   equivalence margin, and only when that holds for a configurable number of
   consecutive windows.  A sustained *decline* is not a plateau unless the
   caller opts in.

The equivalence-based plateau replaces the older "N consecutive sub-threshold
percentage changes" rule, which cannot tell a genuine flat line from a noisy
one: a large step followed by measurement noise can spuriously satisfy it, and
a slow real drift can hide inside it.  The old rule remains available in
``scripts/nmr`` for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .nmr import integrate_above_local_baseline


def _np():
    import numpy as np

    return np


def _stats():
    from scipy import stats

    return stats


# ---------------------------------------------------------------------------
# Elapsed time
# ---------------------------------------------------------------------------


def elapsed_hours(timestamps: Sequence[datetime | None]) -> list[float]:
    """Return hours elapsed since the first non-null timestamp.

    Entries with no timestamp map to NaN so they can be carried through the
    tables without corrupting rate calculations.  The zero point is the
    earliest available timestamp, matching how the existing pipeline orders a
    run.
    """
    np = _np()
    first: datetime | None = None
    for ts in timestamps:
        if ts is not None:
            first = ts if first is None or ts < first else first
    out: list[float] = []
    for ts in timestamps:
        if ts is None or first is None:
            out.append(float("nan"))
        else:
            out.append((ts - first).total_seconds() / 3600.0)
    return out


# ---------------------------------------------------------------------------
# Fixed-window integration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixedRegion:
    """A named, fixed ppm integration window applied across a whole series."""

    name: str
    left_ppm: float
    right_ppm: float


@dataclass(frozen=True)
class RegionIntegral:
    """Fixed-window integral of one spectrum over one region."""

    region_name: str
    left_ppm: float
    right_ppm: float
    signed_area: float
    positive_area: float
    region_noise: float
    region_snr: float
    qc_pass: bool
    qc_failure_reasons: str


def _region_noise(ppm, intensity, lo: float, hi: float) -> float:
    """Robust MAD noise from first differences inside a ppm window.

    ``sigma ~ 1.4826 * MAD(diff) / sqrt(2)`` -- differencing removes any smooth
    baseline slope so the estimate reflects point-to-point noise, matching the
    convention used in :mod:`chemyx_lab.analysis.nmr`.
    """
    np = _np()
    ppm = np.asarray(ppm, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    mask = (ppm >= lo) & (ppm <= hi) & np.isfinite(intensity)
    values = intensity[mask]
    if values.size < 3:
        return float("nan")
    diff = np.diff(values)
    med = float(np.median(diff))
    noise = float(1.4826 * np.median(np.abs(diff - med)) / np.sqrt(2.0))
    if not np.isfinite(noise) or noise <= 0:
        noise = float(np.std(diff) / np.sqrt(2.0)) or float("nan")
    return noise


def integrate_fixed_region(
    ppm: Sequence[float],
    intensity: Sequence[float],
    region: FixedRegion,
) -> RegionIntegral:
    """Integrate one spectrum over one fixed region (foot-to-foot baseline).

    Reuses :func:`chemyx_lab.analysis.nmr.integrate_above_local_baseline` so the
    fixed-window area is computed exactly like the per-peak local integral, only
    with constant boundaries.  Failures (window off-axis, too few points) become
    a QC reason rather than an exception.
    """
    np = _np()
    lo, hi = sorted((float(region.left_ppm), float(region.right_ppm)))
    reasons: list[str] = []
    noise = _region_noise(ppm, intensity, lo, hi)
    try:
        integral = integrate_above_local_baseline(
            ppm, intensity, left_ppm=lo, right_ppm=hi
        )
        signed = integral.signed_area
        positive = integral.positive_area
    except Exception as exc:  # off-axis window, <3 points, etc.
        reasons.append(f"integration_failed: {exc}")
        signed = positive = float("nan")
    snr = float("nan")
    if np.isfinite(noise) and noise > 0 and np.isfinite(positive):
        # Peak-height SNR proxy: tallest positive point above the foot line.
        ppm_arr = np.asarray(ppm, dtype=float)
        y = np.asarray(intensity, dtype=float)
        mask = (ppm_arr >= lo) & (ppm_arr <= hi)
        if np.any(mask):
            snr = float((np.max(y[mask]) - np.min(y[mask])) / noise)
    if not np.isfinite(noise):
        reasons.append("noise_not_estimable")
    return RegionIntegral(
        region_name=region.name,
        left_ppm=lo,
        right_ppm=hi,
        signed_area=signed,
        positive_area=positive,
        region_noise=noise,
        region_snr=snr,
        qc_pass=len(reasons) == 0,
        qc_failure_reasons="; ".join(reasons),
    )


# ---------------------------------------------------------------------------
# Ordinary least squares slope with confidence interval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlopeFit:
    """OLS slope of y vs x with a two-sided confidence interval."""

    slope: float
    intercept: float
    slope_se: float
    slope_ci95_low: float
    slope_ci95_high: float
    n: int


def ols_slope(
    x: Sequence[float],
    y: Sequence[float],
    *,
    confidence: float = 0.95,
) -> SlopeFit:
    """Fit ``y = intercept + slope*x`` and return the slope with a t-interval.

    ``slope_se = sqrt(s^2 / Sxx)`` with ``s^2 = RSS / (n - 2)`` and the interval
    uses the Student-t critical value on ``n - 2`` degrees of freedom.  Returns
    NaN fields (n < 3 or zero x-spread) rather than raising.
    """
    np = _np()
    xa = np.asarray(list(x), dtype=float)
    ya = np.asarray(list(y), dtype=float)
    mask = np.isfinite(xa) & np.isfinite(ya)
    xa, ya = xa[mask], ya[mask]
    n = int(xa.size)
    nan = float("nan")
    if n < 3:
        return SlopeFit(nan, nan, nan, nan, nan, n)
    xbar = float(np.mean(xa))
    sxx = float(np.sum((xa - xbar) ** 2))
    if sxx <= 0:
        return SlopeFit(nan, nan, nan, nan, nan, n)
    sxy = float(np.sum((xa - xbar) * (ya - np.mean(ya))))
    slope = sxy / sxx
    intercept = float(np.mean(ya) - slope * xbar)
    residuals = ya - (intercept + slope * xa)
    dof = n - 2
    s2 = float(np.sum(residuals ** 2) / dof)
    slope_se = float(np.sqrt(s2 / sxx))
    tcrit = float(_stats().t.ppf(0.5 + 0.5 * float(confidence), dof))
    half = tcrit * slope_se
    return SlopeFit(
        slope=slope,
        intercept=intercept,
        slope_se=slope_se,
        slope_ci95_low=slope - half,
        slope_ci95_high=slope + half,
        n=n,
    )


def rolling_slope(
    x: Sequence[float],
    y: Sequence[float],
    *,
    window: int = 4,
    confidence: float = 0.95,
) -> list[SlopeFit]:
    """Trailing-window OLS slope at each point (NaN until the window fills).

    Uses actual (possibly irregular) ``x`` values within each trailing window,
    so a slope is a real per-hour rate, not a per-sample difference.
    """
    fits: list[SlopeFit] = []
    n = len(list(x))
    xa, ya = list(x), list(y)
    for i in range(n):
        if i + 1 < int(window):
            fits.append(SlopeFit(*( [float("nan")] * 5 + [0])))
        else:
            lo = i + 1 - int(window)
            fits.append(ols_slope(xa[lo : i + 1], ya[lo : i + 1], confidence=confidence))
    return fits


# ---------------------------------------------------------------------------
# Finite-difference rates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateRow:
    """Per-point rate quantities for one analysis target."""

    delta_area: float
    delta_time_hours: float
    absolute_rate_per_hour: float
    relative_rate_percent_per_hour: float
    rolling_slope: float
    rolling_slope_se: float
    rolling_slope_ci95_low: float
    rolling_slope_ci95_high: float


def series_rates(
    times_hours: Sequence[float],
    areas: Sequence[float],
    *,
    rolling_window: int = 4,
) -> list[RateRow]:
    """Compute finite-difference and rolling-slope rates over real time.

    The first point has no predecessor, so its finite-difference fields are
    NaN.  ``relative_rate`` is NaN when the previous area is ~0 (dividing a
    change by a near-zero base is meaningless) or the time step is non-positive.
    """
    np = _np()
    t = [float(v) for v in times_hours]
    a = [float(v) for v in areas]
    slopes = rolling_slope(t, a, window=rolling_window)
    rows: list[RateRow] = []
    nan = float("nan")
    for i in range(len(a)):
        if i == 0:
            d_area = d_time = abs_rate = rel_rate = nan
        else:
            d_area = a[i] - a[i - 1]
            d_time = t[i] - t[i - 1]
            if np.isfinite(d_time) and d_time > 0:
                abs_rate = d_area / d_time
                rel_rate = (
                    100.0 * d_area / (a[i - 1] * d_time)
                    if abs(a[i - 1]) > 1e-12
                    else nan
                )
            else:
                abs_rate = rel_rate = nan
        s = slopes[i]
        rows.append(
            RateRow(
                delta_area=d_area,
                delta_time_hours=d_time,
                absolute_rate_per_hour=abs_rate,
                relative_rate_percent_per_hour=rel_rate,
                rolling_slope=s.slope,
                rolling_slope_se=s.slope_se,
                rolling_slope_ci95_low=s.slope_ci95_low,
                rolling_slope_ci95_high=s.slope_ci95_high,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Statistical plateau
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlateauAnalysis:
    """Equivalence-based plateau decision for one analysis target."""

    window_start_time: float
    window_end_time: float
    points_in_window: int
    rolling_slope: float
    rolling_slope_se: float
    rolling_slope_ci95_low: float
    rolling_slope_ci95_high: float
    equivalence_margin_low: float
    equivalence_margin_high: float
    plateau_statistical_pass: bool
    decline_detected: bool
    plateau_duration_hours: float
    plateau_failure_reason: str


def _equivalence_margin(
    window_areas,
    equivalence_abs: float | None,
    equivalence_percent_per_hour: float | None,
) -> float:
    """Return the (symmetric) equivalence half-margin in area-per-hour units."""
    np = _np()
    if equivalence_abs is not None:
        return abs(float(equivalence_abs))
    if equivalence_percent_per_hour is not None:
        reference = float(np.nanmean(np.abs(window_areas)))
        return abs(float(equivalence_percent_per_hour)) / 100.0 * reference
    return float("nan")


def statistical_plateau(
    times_hours: Sequence[float],
    areas: Sequence[float],
    *,
    minimum_points: int = 4,
    equivalence_abs: float | None = None,
    equivalence_percent_per_hour: float | None = 1.0,
    persistence_points: int = 3,
    allow_declining: bool = False,
    confidence: float = 0.95,
) -> PlateauAnalysis:
    """Declare a plateau only when the slope CI sits inside an equivalence band.

    Algorithm
    ---------
    1. Fit the slope and its ``confidence`` interval over the most recent
       ``minimum_points``.
    2. Build a symmetric equivalence margin, either absolute (area/hour) or a
       percent-of-mean-area per hour.
    3. The window passes when the *entire* slope CI lies within
       ``[-margin, +margin]``.
    4. ``decline_detected`` is true when the whole CI is below ``-margin``.
       Unless ``allow_declining`` is set, a detected decline fails the plateau
       (a falling signal is not a completed reaction).
    5. Require the pass to persist over ``persistence_points`` consecutive
       trailing windows; ``plateau_duration_hours`` is the time spanned by that
       persistent stretch.
    """
    np = _np()
    t = np.asarray(list(times_hours), dtype=float)
    a = np.asarray(list(areas), dtype=float)
    mask = np.isfinite(t) & np.isfinite(a)
    t, a = t[mask], a[mask]
    n = int(t.size)
    nan = float("nan")
    mp = int(minimum_points)

    if n < mp:
        return PlateauAnalysis(
            window_start_time=nan, window_end_time=nan, points_in_window=n,
            rolling_slope=nan, rolling_slope_se=nan,
            rolling_slope_ci95_low=nan, rolling_slope_ci95_high=nan,
            equivalence_margin_low=nan, equivalence_margin_high=nan,
            plateau_statistical_pass=False, decline_detected=False,
            plateau_duration_hours=nan,
            plateau_failure_reason=f"insufficient_points ({n} < {mp})",
        )

    def evaluate(end: int):
        sl = slice(end - mp + 1, end + 1)
        fit = ols_slope(t[sl], a[sl], confidence=confidence)
        margin = _equivalence_margin(
            a[sl], equivalence_abs, equivalence_percent_per_hour
        )
        within = (
            np.isfinite(fit.slope_ci95_low)
            and np.isfinite(fit.slope_ci95_high)
            and np.isfinite(margin)
            and fit.slope_ci95_low >= -margin
            and fit.slope_ci95_high <= margin
        )
        decline = (
            np.isfinite(fit.slope_ci95_high)
            and np.isfinite(margin)
            and fit.slope_ci95_high < -margin
        )
        passes = bool(within and not (decline and not allow_declining))
        return fit, margin, passes, decline

    final_fit, final_margin, final_pass, final_decline = evaluate(n - 1)

    # Persistence: count consecutive passing windows walking back from the end.
    consecutive = 0
    earliest_start = t[n - 1]
    for end in range(n - 1, mp - 2, -1):
        _fit, _margin, passes, _decline = evaluate(end)
        if passes:
            consecutive += 1
            earliest_start = t[end - mp + 1]
        else:
            break

    plateau_pass = final_pass and consecutive >= int(persistence_points)
    if final_decline and not allow_declining:
        reason = "declining_series"
    elif not final_pass:
        reason = "slope_ci_outside_equivalence_margin"
    elif consecutive < int(persistence_points):
        reason = f"not_persistent ({consecutive} < {persistence_points} windows)"
    else:
        reason = ""
    duration = float(t[n - 1] - earliest_start) if plateau_pass else float("nan")

    return PlateauAnalysis(
        window_start_time=float(t[n - mp]),
        window_end_time=float(t[n - 1]),
        points_in_window=mp,
        rolling_slope=final_fit.slope,
        rolling_slope_se=final_fit.slope_se,
        rolling_slope_ci95_low=final_fit.slope_ci95_low,
        rolling_slope_ci95_high=final_fit.slope_ci95_high,
        equivalence_margin_low=-final_margin,
        equivalence_margin_high=final_margin,
        plateau_statistical_pass=plateau_pass,
        decline_detected=final_decline,
        plateau_duration_hours=duration,
        plateau_failure_reason=reason,
    )
