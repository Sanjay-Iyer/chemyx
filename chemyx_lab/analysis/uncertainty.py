"""Bootstrap uncertainty estimates for fitted NMR peaks.

The preferred method is a **residual bootstrap** around a fitted line shape
(:mod:`chemyx_lab.analysis.lineshapes`): the fit residuals are resampled with
replacement, added back to the fitted curve, and the peak is re-fit many times.
The spread of the re-fit parameters gives standard errors and percentile
confidence intervals for the center, height, FWHM and area.

Guarantees the pipeline relies on:

* **Deterministic.** All resampling draws come from ``numpy.random.default_rng``
  seeded from the analysis configuration, so a re-run reproduces the intervals
  exactly.
* **Fail-soft, never fabricate.** If the initial fit is not identifiable, the
  center/height/FWHM uncertainties are returned as NaN with a QC reason and
  only an area uncertainty (from a parametric noise bootstrap that does not
  need a fit) is reported.  A single un-bootstrappable peak never aborts a run.
* **Honest QC.** ``bootstrap_qc_pass`` is ``False`` when too few resamples
  produced a usable re-fit, so downstream consumers can distinguish a genuine
  tight interval from a degenerate one.

Interpretation caveat: zero-filled / interpolated spectrum points are **not**
independent measurements.  A residual bootstrap over an oversampled spectrum
can therefore *understate* the true experimental uncertainty; treat the
intervals as a lower bound on measurement error, documented in
``docs/nmr_statistics.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .lineshapes import PeakFit, fit_peak


def _np():
    import numpy as np

    return np


def _trapz(np, y, x):
    integrator = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return integrator(y, x)


@dataclass(frozen=True)
class ParamUncertainty:
    """Standard error and percentile CI for one bootstrapped parameter."""

    se: float
    ci95_low: float
    ci95_high: float


_NAN_PARAM = ParamUncertainty(float("nan"), float("nan"), float("nan"))


@dataclass(frozen=True)
class BootstrapResult:
    """Bootstrap uncertainty for one peak's center, height, FWHM and area."""

    center_ppm: ParamUncertainty
    height: ParamUncertainty
    fwhm_ppm: ParamUncertainty
    area: ParamUncertainty
    iterations_requested: int
    iterations_succeeded: int
    qc_pass: bool
    reason: str
    method: str


def _summarize(samples, confidence: float) -> ParamUncertainty:
    np = _np()
    values = np.asarray(samples, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return _NAN_PARAM
    alpha = 1.0 - float(confidence)
    lo, hi = np.percentile(values, [100.0 * alpha / 2.0, 100.0 * (1.0 - alpha / 2.0)])
    return ParamUncertainty(
        se=float(np.std(values, ddof=1)),
        ci95_low=float(lo),
        ci95_high=float(hi),
    )


def bootstrap_peak_fit(
    x: Sequence[float],
    y: Sequence[float],
    *,
    model: str = "pseudo_voigt",
    noise: float | None = None,
    iterations: int = 500,
    confidence: float = 0.95,
    seed: int = 12345,
    min_success_fraction: float = 0.5,
    min_success_count: int = 20,
) -> BootstrapResult:
    """Residual-bootstrap the fit of one baseline-corrected peak.

    Parameters
    ----------
    x, y:
        Chemical-shift axis and *baseline-corrected* intensity for one peak.
    model:
        Line-shape model passed to :func:`fit_peak`.
    noise:
        Robust intensity noise, used only for the area-only fallback when the
        initial fit is not identifiable.
    iterations:
        Number of bootstrap resamples requested.
    confidence:
        Two-sided confidence level for the percentile interval (default 0.95).
    seed:
        Deterministic RNG seed.

    Returns
    -------
    BootstrapResult
        Per-parameter SE and CI.  ``method`` is ``"fit_residual"`` on the normal
        path or ``"parametric_area_only"`` when the fit was not identifiable.
    """
    np = _np()
    base_fit: PeakFit = fit_peak(x, y, model=model)
    if not base_fit.success or base_fit.residuals is None:
        return _area_only_fallback(
            x, y, noise=noise, iterations=iterations,
            confidence=confidence, seed=seed,
            reason=f"initial_fit_failed: {base_fit.failure_reason or 'not_identifiable'}",
        )

    base_curve = np.asarray(base_fit.model_curve, dtype=float)
    residuals = np.asarray(base_fit.residuals, dtype=float)
    rng = np.random.default_rng(int(seed))
    x_arr = np.asarray(list(x), dtype=float)

    centers: list[float] = []
    heights: list[float] = []
    fwhms: list[float] = []
    areas: list[float] = []
    for _ in range(int(iterations)):
        resampled = rng.choice(residuals, size=residuals.size, replace=True)
        y_star = base_curve + resampled
        fit_b = fit_peak(x_arr, y_star, model=model)
        params = fit_b.params
        c, h, w = params.get("center"), params.get("height"), params.get("fwhm")
        if not (np.isfinite(c) and np.isfinite(h) and np.isfinite(w)):
            continue
        centers.append(float(c))
        heights.append(float(h))
        fwhms.append(float(w))
        areas.append(float(fit_b.area))

    succeeded = len(centers)
    threshold = max(int(min_success_count), int(min_success_fraction * int(iterations)))
    qc_pass = succeeded >= threshold
    reason = "" if qc_pass else f"insufficient_bootstrap_success ({succeeded}/{iterations})"
    return BootstrapResult(
        center_ppm=_summarize(centers, confidence),
        height=_summarize(heights, confidence),
        fwhm_ppm=_summarize(fwhms, confidence),
        area=_summarize(areas, confidence),
        iterations_requested=int(iterations),
        iterations_succeeded=succeeded,
        qc_pass=qc_pass,
        reason=reason,
        method="fit_residual",
    )


def _area_only_fallback(
    x: Sequence[float],
    y: Sequence[float],
    *,
    noise: float | None,
    iterations: int,
    confidence: float,
    seed: int,
    reason: str,
) -> BootstrapResult:
    """Parametric area bootstrap used when no identifiable fit is available.

    Adds Gaussian noise of the estimated level to the corrected trace and
    re-integrates a signed area over the whole window.  Center/height/FWHM are
    left NaN because they are not identifiable without a fit.
    """
    np = _np()
    if noise is None or not (float(noise) > 0):
        return BootstrapResult(
            center_ppm=_NAN_PARAM, height=_NAN_PARAM, fwhm_ppm=_NAN_PARAM,
            area=_NAN_PARAM, iterations_requested=int(iterations),
            iterations_succeeded=0, qc_pass=False,
            reason=f"{reason}; no_noise_for_area_bootstrap",
            method="parametric_area_only",
        )
    x_arr = np.asarray(list(x), dtype=float)
    y_arr = np.asarray(list(y), dtype=float)
    order = np.argsort(x_arr)
    x_arr, y_arr = x_arr[order], y_arr[order]
    rng = np.random.default_rng(int(seed))
    areas = [
        float(_trapz(np, y_arr + rng.normal(0.0, float(noise), size=y_arr.size), x_arr))
        for _ in range(int(iterations))
    ]
    return BootstrapResult(
        center_ppm=_NAN_PARAM, height=_NAN_PARAM, fwhm_ppm=_NAN_PARAM,
        area=_summarize(areas, confidence),
        iterations_requested=int(iterations),
        iterations_succeeded=len(areas),
        qc_pass=True,
        reason=f"{reason}; area_from_parametric_noise_bootstrap",
        method="parametric_area_only",
    )


def bootstrap_statistic(
    values: Sequence[float],
    statistic: Callable[[object], float],
    *,
    iterations: int = 1000,
    confidence: float = 0.95,
    seed: int = 12345,
) -> ParamUncertainty:
    """Non-parametric bootstrap of an arbitrary scalar ``statistic`` of a sample.

    A small general-purpose helper (used e.g. for family-level area spread).
    Deterministic given ``seed``.
    """
    np = _np()
    data = np.asarray(list(values), dtype=float)
    data = data[np.isfinite(data)]
    if data.size < 2:
        return _NAN_PARAM
    rng = np.random.default_rng(int(seed))
    samples = [
        float(statistic(rng.choice(data, size=data.size, replace=True)))
        for _ in range(int(iterations))
    ]
    return _summarize(samples, confidence)
