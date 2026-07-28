"""Run-level quality-control scoring and control-chart limits.

The per-peak checks already live in :mod:`chemyx_lab.analysis.nmr`.  This module
adds a per-*spectrum* verdict for a whole run -- did this acquisition drift,
lose lock, or become an outlier relative to its neighbours -- and the control
limits used to plot those quantities over time.

Nothing here removes or edits a spectrum; it only labels each one with a
pass/fail and the reasons, so a questionable spectrum is flagged and retained
rather than silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


def _np():
    import numpy as np

    return np


@dataclass(frozen=True)
class ControlLimits:
    """Mean and +/-2 sigma (warning) / +/-3 sigma (control) limits for a metric.

    ``sufficient`` is ``False`` when there are too few observations to establish
    limits; callers must not draw control lines in that case without a caveat.
    """

    mean: float
    sd: float
    warn_low: float
    warn_high: float
    control_low: float
    control_high: float
    n: int
    sufficient: bool
    reason: str


def control_limits(
    values: Sequence[float],
    *,
    minimum_observations: int = 8,
) -> ControlLimits:
    """Compute Shewhart-style control limits, guarding against too few points.

    Limits from a handful of points are unstable, so with fewer than
    ``minimum_observations`` finite values ``sufficient`` is ``False`` and the
    reason explains why -- the numbers are still returned for reference but must
    be labelled provisional.
    """
    np = _np()
    v = np.asarray(list(values), dtype=float)
    v = v[np.isfinite(v)]
    n = int(v.size)
    nan = float("nan")
    if n == 0:
        return ControlLimits(nan, nan, nan, nan, nan, nan, 0, False, "no_finite_values")
    mean = float(np.mean(v))
    sd = float(np.std(v, ddof=1)) if n > 1 else nan
    sufficient = n >= int(minimum_observations) and np.isfinite(sd) and sd > 0
    reason = "" if sufficient else f"insufficient_observations ({n} < {minimum_observations})"
    return ControlLimits(
        mean=mean,
        sd=sd,
        warn_low=mean - 2.0 * sd,
        warn_high=mean + 2.0 * sd,
        control_low=mean - 3.0 * sd,
        control_high=mean + 3.0 * sd,
        n=n,
        sufficient=bool(sufficient),
        reason=reason,
    )


def median_ignoring_nan(values: Sequence[float]) -> float:
    """Median of the finite values, NaN when none are finite."""
    np = _np()
    v = np.asarray(list(values), dtype=float)
    v = v[np.isfinite(v)]
    return float(np.median(v)) if v.size else float("nan")


@dataclass(frozen=True)
class SpectrumQc:
    """Run-level QC verdict for one spectrum."""

    run_qc_pass: bool
    run_qc_failure_reasons: str


def evaluate_spectrum_qc(
    *,
    region_noise: float,
    peak_count: int,
    qc_pass_fraction: float,
    reference_qc_pass: bool = True,
    is_spectral_outlier: bool = False,
    minimum_peak_count: int = 1,
    minimum_qc_pass_fraction: float = 0.5,
) -> SpectrumQc:
    """Combine per-spectrum signals into a single pass/fail plus reasons.

    Conservative by design: a spectrum passes unless something concrete is
    wrong (noise not estimable, no resolved peaks, most peaks failing their own
    QC, a rejected reference correction, or a whole-spectrum outlier).  Reasons
    accumulate so nothing is hidden.
    """
    np = _np()
    reasons: list[str] = []
    if not np.isfinite(region_noise):
        reasons.append("region_noise_not_finite")
    if int(peak_count) < int(minimum_peak_count):
        reasons.append(f"peak_count_below_minimum ({peak_count} < {minimum_peak_count})")
    if np.isfinite(qc_pass_fraction) and qc_pass_fraction < float(minimum_qc_pass_fraction):
        reasons.append(
            f"qc_pass_fraction_low ({qc_pass_fraction:.2f} < {minimum_qc_pass_fraction})"
        )
    if not reference_qc_pass:
        reasons.append("reference_correction_rejected")
    if is_spectral_outlier:
        reasons.append("whole_spectrum_outlier")
    return SpectrumQc(run_qc_pass=len(reasons) == 0, run_qc_failure_reasons="; ".join(reasons))
