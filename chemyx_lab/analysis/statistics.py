"""Robust descriptive statistics and outlier flags for NMR peak series.

This module provides the small, pure numerical primitives used by the higher
level analysis modules (peak-family statistics, run QC, time-series rates).
Everything here operates on plain sequences of floats and returns plain floats
or small dataclasses, so it can be unit-tested without any NMR data.

Design rules that the rest of the pipeline relies on:

* **Never invent a value.** When a statistic is not defined for the input
  (too few points, zero spread, a mean indistinguishable from zero) the
  function returns ``float('nan')`` together with a short machine-readable
  reason string, rather than a misleading number.
* **Robust first.** Median, median-absolute-deviation (MAD) and the
  inter-quartile range (IQR) are reported alongside the classical mean/SD so a
  single anomalous spectrum cannot dominate a family summary.

Equations
---------
Robust z-score (Iglewicz & Hoaglin, 1993)::

    robust_z = 0.6745 * (x - median(x)) / MAD
    MAD      = median(|x - median(x)|)

``0.6745`` is the 0.75 quantile of the standard normal, so ``0.6745 / MAD``
estimates ``1 / sigma`` for normally distributed data and ``robust_z`` is on the
same scale as an ordinary z-score.  When ``MAD == 0`` (more than half the values
are identical) the estimator is undefined, so we fall back to the mean absolute
deviation about the median::

    robust_z = (x - median(x)) / (1.253314 * meanAD)
    meanAD   = mean(|x - median(x)|)

and if that is also zero every finite value is identical and all scores are 0.

Coefficient of variation::

    CV%% = 100 * sd / |mean|

which is only meaningful for a strictly positive quantity; it is returned as
NaN when ``|mean|`` is at or below a caller-supplied floor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


# Iglewicz-Hoaglin constant: the 0.75 quantile of the standard normal.
_ROBUST_Z_CONSTANT = 0.6745
# E[|X|] for a standard normal is sqrt(2/pi); its reciprocal scales meanAD to sigma.
_MEANAD_TO_SIGMA = 1.2533141373155001


def _np():
    """Lazily import numpy (keeps the module import-safe on a clean clone)."""
    import numpy as np

    return np


@dataclass(frozen=True)
class RobustSummary:
    """Classical and robust descriptive statistics for one quantity.

    ``cv_percent`` and ``robust_cv_percent`` are NaN when the (respective)
    center is too close to zero to define a coefficient of variation; the
    reason is recorded in :attr:`cv_reason`.
    """

    n: int
    mean: float
    median: float
    sd: float
    mad: float
    iqr: float
    minimum: float
    maximum: float
    cv_percent: float
    robust_cv_percent: float
    cv_reason: str


def _finite(values: Sequence[float]):
    np = _np()
    array = np.asarray(list(values), dtype=float) if not hasattr(values, "dtype") else values.astype(float)
    return array[np.isfinite(array)]


def median_absolute_deviation(values: Sequence[float], *, scaled: bool = False) -> float:
    """Return the median absolute deviation about the median.

    Parameters
    ----------
    scaled:
        When ``True`` multiply by ``1.4826`` so the result is a consistent
        estimator of the standard deviation for normal data.  When ``False``
        (default) the raw MAD is returned, which is what ``*_mad`` report
        columns contain.
    """
    np = _np()
    finite = _finite(values)
    if finite.size == 0:
        return float("nan")
    med = float(np.median(finite))
    mad = float(np.median(np.abs(finite - med)))
    return mad * 1.4826 if scaled else mad


def interquartile_range(values: Sequence[float]) -> float:
    """Return ``Q3 - Q1`` using linear interpolation, NaN when empty."""
    np = _np()
    finite = _finite(values)
    if finite.size == 0:
        return float("nan")
    q1, q3 = np.percentile(finite, [25.0, 75.0])
    return float(q3 - q1)


def coefficient_of_variation(
    values: Sequence[float],
    *,
    ddof: int = 1,
    min_abs_mean: float = 1e-12,
) -> tuple[float, str]:
    """Return ``(cv_percent, reason)`` for a strictly positive quantity.

    ``reason`` is empty on success.  CV is undefined when fewer than two finite
    values are present (``"insufficient_n"``) or when the mean is within
    ``min_abs_mean`` of zero (``"mean_near_zero"``) -- dividing a spread by a
    near-zero mean produces an arbitrarily large, meaningless number, so NaN is
    returned instead.
    """
    np = _np()
    finite = _finite(values)
    if finite.size < 2:
        return float("nan"), "insufficient_n"
    mean = float(np.mean(finite))
    if abs(mean) <= float(min_abs_mean):
        return float("nan"), "mean_near_zero"
    sd = float(np.std(finite, ddof=ddof))
    return 100.0 * sd / abs(mean), ""


def robust_coefficient_of_variation(
    values: Sequence[float],
    *,
    min_abs_median: float = 1e-12,
) -> tuple[float, str]:
    """Return ``(100 * scaledMAD / |median|, reason)``.

    A robust analogue of the CV that resists a single outlier.  Same NaN/reason
    contract as :func:`coefficient_of_variation`.
    """
    np = _np()
    finite = _finite(values)
    if finite.size < 2:
        return float("nan"), "insufficient_n"
    median = float(np.median(finite))
    if abs(median) <= float(min_abs_median):
        return float("nan"), "median_near_zero"
    scaled_mad = median_absolute_deviation(finite, scaled=True)
    return 100.0 * scaled_mad / abs(median), ""


def robust_z_scores(values: Sequence[float]) -> tuple[list[float], str]:
    """Return ``(scores, method)`` -- modified z-scores that resist outliers.

    ``method`` is one of ``"mad"`` (the normal case), ``"meanad_fallback"``
    (used when MAD is zero), ``"zero_spread"`` (every finite value identical, so
    all scores are 0), or ``"no_finite_values"``.  Non-finite inputs map to NaN
    scores and never contaminate the median/MAD.
    """
    np = _np()
    array = np.asarray(list(values), dtype=float)
    scores = np.full(array.shape, np.nan, dtype=float)
    finite_mask = np.isfinite(array)
    if not np.any(finite_mask):
        return scores.tolist(), "no_finite_values"

    finite = array[finite_mask]
    median = float(np.median(finite))
    abs_dev = np.abs(finite - median)
    mad = float(np.median(abs_dev))
    if mad > 0.0:
        scores[finite_mask] = _ROBUST_Z_CONSTANT * (finite - median) / mad
        return scores.tolist(), "mad"

    mean_ad = float(np.mean(abs_dev))
    if mean_ad > 0.0:
        scores[finite_mask] = (finite - median) / (_MEANAD_TO_SIGMA * mean_ad)
        return scores.tolist(), "meanad_fallback"

    scores[finite_mask] = 0.0
    return scores.tolist(), "zero_spread"


def flag_outliers(
    values: Sequence[float],
    *,
    threshold: float = 3.5,
) -> tuple[list[float], list[bool], str]:
    """Return ``(robust_z, is_outlier, method)`` using the modified z-score.

    A finite value is flagged when ``|robust_z| > threshold`` (Iglewicz-Hoaglin
    recommend 3.5).  Outliers are only ever *flagged*, never removed -- callers
    keep the original value and record the reason.  With ``"zero_spread"`` or
    ``"no_finite_values"`` nothing is flagged.
    """
    np = _np()
    scores, method = robust_z_scores(values)
    z = np.asarray(scores, dtype=float)
    is_outlier = np.zeros(z.shape, dtype=bool)
    if method in ("mad", "meanad_fallback"):
        is_outlier = np.isfinite(z) & (np.abs(z) > float(threshold))
    return scores, is_outlier.tolist(), method


def summarize(values: Sequence[float]) -> RobustSummary:
    """Return a :class:`RobustSummary` of a quantity across a peak family.

    All fields are NaN-safe: an empty input yields ``n == 0`` and NaN
    statistics; a single value yields NaN SD/CV with reason ``"insufficient_n"``.
    """
    np = _np()
    finite = _finite(values)
    n = int(finite.size)
    if n == 0:
        nan = float("nan")
        return RobustSummary(
            n=0, mean=nan, median=nan, sd=nan, mad=nan, iqr=nan,
            minimum=nan, maximum=nan, cv_percent=nan,
            robust_cv_percent=nan, cv_reason="no_finite_values",
        )
    cv_percent, cv_reason = coefficient_of_variation(finite)
    robust_cv, _ = robust_coefficient_of_variation(finite)
    return RobustSummary(
        n=n,
        mean=float(np.mean(finite)),
        median=float(np.median(finite)),
        sd=float(np.std(finite, ddof=1)) if n > 1 else float("nan"),
        mad=median_absolute_deviation(finite),
        iqr=interquartile_range(finite),
        minimum=float(np.min(finite)),
        maximum=float(np.max(finite)),
        cv_percent=cv_percent,
        robust_cv_percent=robust_cv,
        cv_reason=cv_reason,
    )
