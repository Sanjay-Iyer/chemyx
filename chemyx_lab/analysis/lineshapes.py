"""Line-shape models, least-squares peak fitting, and fit diagnostics.

The regional peak picker in :mod:`chemyx_lab.analysis.nmr` locates peaks and
integrates them, but it never fits an analytic line shape -- positions come
from a 3-point parabolic interpolation and widths from ``scipy.peak_widths``.
This module adds *optional* line-shape fitting so that, where a peak is
well-resolved, we can report interpretable goodness-of-fit metrics, compare
Gaussian / Lorentzian / pseudo-Voigt models on an information-criterion basis,
and provide a fitted model for the residual bootstrap in
:mod:`chemyx_lab.analysis.uncertainty`.

All shapes are parameterised by **height** (peak maximum above a locally
flat baseline), **center** (ppm) and **FWHM** (full width at half maximum,
ppm), which are the quantities the pipeline reports.  The pseudo-Voigt adds a
mixing fraction ``eta`` in ``[0, 1]`` (1 = pure Lorentzian, 0 = pure Gaussian).

Fitting operates on a *baseline-corrected* window: the caller is expected to
pass ``y`` with the local baseline already removed (the region picker already
produces such a ``corrected`` trace).  Fits that do not converge, or whose
covariance is not positive-definite, are returned with ``success = False`` and
NaN standard errors rather than raising -- a single bad peak must never abort a
run.

Model-selection note
--------------------
Models are compared with the small-sample-corrected AIC (AICc) and BIC, never
with :math:`R^2` alone: :math:`R^2` never decreases when parameters are added,
so it always favours the most flexible model regardless of whether the extra
flexibility is justified.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence


# 2*sqrt(2*ln2): converts a Gaussian FWHM to its standard deviation sigma.
_FWHM_TO_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))


def _np():
    import numpy as np

    return np


def _optimize():
    from scipy import optimize

    return optimize


def _stats():
    from scipy import stats

    return stats


# ---------------------------------------------------------------------------
# Analytic line shapes (height-parameterised)
# ---------------------------------------------------------------------------


def gaussian(x, height: float, center: float, fwhm: float):
    """Gaussian peak of the given height, center and FWHM (ppm)."""
    np = _np()
    x = np.asarray(x, dtype=float)
    sigma = float(fwhm) / _FWHM_TO_SIGMA
    if sigma <= 0:
        return np.full_like(x, np.nan)
    return float(height) * np.exp(-0.5 * ((x - float(center)) / sigma) ** 2)


def lorentzian(x, height: float, center: float, fwhm: float):
    """Lorentzian (Cauchy) peak of the given height, center and FWHM (ppm)."""
    np = _np()
    x = np.asarray(x, dtype=float)
    half = 0.5 * float(fwhm)
    if half <= 0:
        return np.full_like(x, np.nan)
    return float(height) * half * half / ((x - float(center)) ** 2 + half * half)


def pseudo_voigt(x, height: float, center: float, fwhm: float, eta: float):
    """Linear Gaussian/Lorentzian mix sharing one height, center and FWHM.

    ``eta`` is the Lorentzian fraction; it is clipped to ``[0, 1]``.
    """
    np = _np()
    eta = min(1.0, max(0.0, float(eta)))
    g = gaussian(x, height, center, fwhm)
    l = lorentzian(x, height, center, fwhm)
    return eta * l + (1.0 - eta) * g


# name -> (function, parameter names, has_eta)
MODEL_FUNCTIONS: dict[str, tuple[Callable, tuple[str, ...], bool]] = {
    "gaussian": (gaussian, ("height", "center", "fwhm"), False),
    "lorentzian": (lorentzian, ("height", "center", "fwhm"), False),
    "pseudo_voigt": (pseudo_voigt, ("height", "center", "fwhm", "eta"), True),
}


@dataclass(frozen=True)
class PeakFit:
    """Result of fitting one analytic line shape to a baseline-corrected peak."""

    model: str
    success: bool
    params: dict[str, float]
    param_se: dict[str, float]
    center_ppm: float
    height: float
    fwhm_ppm: float
    area: float
    lorentzian_fraction: float
    gaussian_fraction: float
    n_points: int
    n_params: int
    residuals: object  # numpy array, or None when the fit failed
    failure_reason: str = ""
    model_curve: object = None  # fitted y over the input x, or None


@dataclass(frozen=True)
class ModelComparison:
    """AICc/BIC comparison of competing line-shape models for one peak."""

    best_model: str
    aicc: dict[str, float]
    bic: dict[str, float]
    delta_aicc: dict[str, float]
    fits: dict[str, PeakFit] = field(default_factory=dict)


def _estimate_initial(x, y) -> tuple[float, float, float]:
    """Rough (height, center, fwhm) starting guess from a corrected window."""
    np = _np()
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    idx = int(np.argmax(y))
    height = float(y[idx])
    center = float(x[idx])
    half = height / 2.0
    above = np.flatnonzero(y >= half)
    if above.size >= 2:
        fwhm = float(abs(x[above[-1]] - x[above[0]]))
    else:
        fwhm = float(abs(x[-1] - x[0]) / 4.0)
    span = float(abs(x[-1] - x[0]))
    if not (fwhm > 0):
        fwhm = span / 10.0 if span > 0 else 1.0
    return height, center, fwhm


def fit_peak(
    x: Sequence[float],
    y: Sequence[float],
    *,
    model: str = "pseudo_voigt",
    max_nfev: int = 10000,
) -> PeakFit:
    """Fit one line shape to a baseline-corrected peak window.

    Parameters
    ----------
    x, y:
        Chemical-shift axis (ppm) and *baseline-corrected* intensity over a
        window that brackets a single peak.
    model:
        One of ``"gaussian"``, ``"lorentzian"``, ``"pseudo_voigt"``.

    Returns
    -------
    PeakFit
        ``success`` is ``False`` (with ``failure_reason`` set and NaN standard
        errors) when there are too few points, the optimiser does not converge,
        or the covariance matrix is not usable.  The nominal parameters are
        still returned in that case so a plot can show the attempted curve.
    """
    np = _np()
    optimize = _optimize()
    if model not in MODEL_FUNCTIONS:
        raise ValueError(f"unknown model {model!r}; expected {sorted(MODEL_FUNCTIONS)}")
    func, names, has_eta = MODEL_FUNCTIONS[model]
    x = np.asarray(list(x), dtype=float)
    y = np.asarray(list(y), dtype=float)
    n_params = len(names)
    nan = float("nan")

    def _failed(reason: str) -> PeakFit:
        return PeakFit(
            model=model, success=False,
            params={k: nan for k in names}, param_se={k: nan for k in names},
            center_ppm=nan, height=nan, fwhm_ppm=nan, area=nan,
            lorentzian_fraction=nan, gaussian_fraction=nan,
            n_points=int(x.size), n_params=n_params, residuals=None,
            failure_reason=reason, model_curve=None,
        )

    if x.size < n_params + 1:
        return _failed("insufficient_points")
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        return _failed("non_finite_input")

    height0, center0, fwhm0 = _estimate_initial(x, y)
    span = float(abs(x[-1] - x[0])) or 1.0
    lo = [0.0, float(np.min(x)), 1e-6 * span]
    hi = [max(5.0 * abs(height0), 1e-9) + 1e-9, float(np.max(x)), 2.0 * span]
    p0 = [max(height0, 1e-9), center0, max(fwhm0, 1e-6 * span)]
    if has_eta:
        p0.append(0.5)
        lo.append(0.0)
        hi.append(1.0)

    try:
        popt, pcov = optimize.curve_fit(
            func, x, y, p0=p0, bounds=(lo, hi), max_nfev=int(max_nfev),
        )
    except Exception as exc:  # optimiser did not converge / bad model
        return _failed(f"curve_fit_failed: {exc}")

    params = {name: float(value) for name, value in zip(names, popt)}
    if np.all(np.isfinite(pcov)):
        perr = np.sqrt(np.clip(np.diag(pcov), 0.0, np.inf))
        param_se = {name: float(err) for name, err in zip(names, perr)}
        identifiable = np.all(np.isfinite(perr))
    else:
        param_se = {name: nan for name in names}
        identifiable = False

    model_curve = func(x, *popt)
    residuals = y - model_curve
    area = float(_np_trapz(np, model_curve, x))
    eta = params.get("eta", 1.0 if model == "lorentzian" else 0.0)
    reason = "" if identifiable else "covariance_not_identifiable"
    return PeakFit(
        model=model,
        success=bool(identifiable),
        params=params,
        param_se=param_se,
        center_ppm=params["center"],
        height=params["height"],
        fwhm_ppm=params["fwhm"],
        area=area,
        lorentzian_fraction=float(eta),
        gaussian_fraction=float(1.0 - eta),
        n_points=int(x.size),
        n_params=n_params,
        residuals=residuals,
        failure_reason=reason,
        model_curve=model_curve,
    )


def _np_trapz(np, y, x):
    """Trapezoidal integral tolerant of numpy's trapz/trapezoid rename."""
    integrator = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return integrator(y, x)


# ---------------------------------------------------------------------------
# Goodness-of-fit diagnostics
# ---------------------------------------------------------------------------


def fit_diagnostics(
    y: Sequence[float],
    y_pred: Sequence[float],
    *,
    n_params: int,
    noise: float | None = None,
) -> dict[str, float]:
    """Return interpretable goodness-of-fit metrics for one fit.

    ``noise`` is the robust intensity noise (same units as ``y``); it is
    required for ``reduced_chi_square`` and returned as NaN otherwise.  Every
    metric that is undefined for the given ``n``/``n_params`` (e.g. adjusted
    :math:`R^2` when ``n - p - 1 <= 0``) is returned as NaN rather than raised.

    Definitions (``r = y - y_pred``, ``n = len(y)``, ``p = n_params``)::

        rmse             = sqrt(sum(r^2) / n)
        r_squared        = 1 - sum(r^2) / sum((y - mean(y))^2)
        adjusted_r2      = 1 - (1 - r2) * (n - 1) / (n - p - 1)
        reduced_chi2     = sum(r^2) / (noise^2 * (n - p))
        aic              = n * ln(rss / n) + 2p          (Gaussian errors)
        aicc             = aic + 2p(p + 1) / (n - p - 1)
        bic              = n * ln(rss / n) + p * ln(n)
        durbin_watson    = sum(diff(r)^2) / sum(r^2)     (~2 => no lag-1 corr)
        lag1_autocorr    = corr(r[:-1], r[1:])
    """
    np = _np()
    y = np.asarray(list(y), dtype=float)
    y_pred = np.asarray(list(y_pred), dtype=float)
    nan = float("nan")
    n = int(y.size)
    p = int(n_params)
    out = {
        "fit_rmse": nan, "fit_normalized_rmse": nan, "r_squared": nan,
        "adjusted_r_squared": nan, "reduced_chi_square": nan,
        "aic": nan, "aicc": nan, "bic": nan, "max_absolute_residual": nan,
        "durbin_watson": nan, "residual_lag1_autocorrelation": nan,
    }
    if n == 0 or y.shape != y_pred.shape:
        return out
    residuals = y - y_pred
    rss = float(np.sum(residuals ** 2))
    out["fit_rmse"] = math.sqrt(rss / n)
    out["max_absolute_residual"] = float(np.max(np.abs(residuals)))

    data_range = float(np.max(y) - np.min(y))
    if data_range > 0:
        out["fit_normalized_rmse"] = out["fit_rmse"] / data_range

    tss = float(np.sum((y - np.mean(y)) ** 2))
    if tss > 0:
        r2 = 1.0 - rss / tss
        out["r_squared"] = r2
        if n - p - 1 > 0:
            out["adjusted_r_squared"] = 1.0 - (1.0 - r2) * (n - 1) / (n - p - 1)

    if noise is not None and float(noise) > 0 and n - p > 0:
        out["reduced_chi_square"] = rss / (float(noise) ** 2 * (n - p))

    if rss > 0 and n > 0:
        aic = n * math.log(rss / n) + 2.0 * p
        out["aic"] = aic
        if n - p - 1 > 0:
            out["aicc"] = aic + (2.0 * p * (p + 1)) / (n - p - 1)
        out["bic"] = n * math.log(rss / n) + p * math.log(n)
        out["durbin_watson"] = float(np.sum(np.diff(residuals) ** 2) / rss)
    elif rss == 0 and n > 0:
        # A perfect fit is infinitely preferred; keep it selectable (not NaN).
        out["aic"] = out["bic"] = float("-inf")
        if n - p - 1 > 0:
            out["aicc"] = float("-inf")

    if n >= 3:
        r0 = residuals[:-1]
        r1 = residuals[1:]
        denom = float(np.std(r0) * np.std(r1))
        if denom > 0:
            out["residual_lag1_autocorrelation"] = float(
                np.mean((r0 - r0.mean()) * (r1 - r1.mean())) / denom
            )
    return out


def ljung_box_pvalue(residuals: Sequence[float], *, lags: int | None = None) -> float:
    """Return the Ljung-Box test p-value for residual autocorrelation.

    A small p-value indicates the residuals are autocorrelated, meaning the
    model has missed structure and reported parameter standard errors are
    likely optimistic.  Returns NaN when there are too few points.

    ``Q = n (n + 2) sum_{k=1..h} rho_k^2 / (n - k)`` is compared to a
    chi-square distribution with ``h`` degrees of freedom.
    """
    np = _np()
    stats = _stats()
    r = np.asarray(list(residuals), dtype=float)
    n = int(r.size)
    if n < 8:
        return float("nan")
    h = int(lags) if lags is not None else min(10, n // 2)
    if h < 1:
        return float("nan")
    r = r - r.mean()
    denom = float(np.sum(r ** 2))
    if denom <= 0:
        return float("nan")
    q = 0.0
    for k in range(1, h + 1):
        rho_k = float(np.sum(r[k:] * r[:-k]) / denom)
        q += rho_k ** 2 / (n - k)
    q *= n * (n + 2)
    return float(1.0 - stats.chi2.cdf(q, h))


# ---------------------------------------------------------------------------
# Peak-shape descriptors (computed from data, not the fit)
# ---------------------------------------------------------------------------


def peak_asymmetry(x: Sequence[float], y: Sequence[float], center: float) -> float:
    """Return the half-width ratio (high-ppm side / low-ppm side) at half height.

    ``> 1`` means the peak tails toward high ppm.  NaN when the half-maximum
    crossings cannot be located on both sides.
    """
    np = _np()
    x = np.asarray(list(x), dtype=float)
    y = np.asarray(list(y), dtype=float)
    if x.size < 3:
        return float("nan")
    order = np.argsort(x)
    x, y = x[order], y[order]
    peak_idx = int(np.argmax(y))
    half = y[peak_idx] / 2.0
    if not (half > 0):
        return float("nan")

    def _cross(indices):
        prev = peak_idx
        for i in indices:
            if y[i] <= half:
                # linear interpolation between i and prev for the crossing
                if y[prev] == y[i]:
                    return float(x[i])
                frac = (y[prev] - half) / (y[prev] - y[i])
                return float(x[prev] + frac * (x[i] - x[prev]))
            prev = i
        return None

    left_x = _cross(range(peak_idx - 1, -1, -1))
    right_x = _cross(range(peak_idx + 1, x.size))
    if left_x is None or right_x is None:
        return float("nan")
    center = float(x[peak_idx])
    left_hw = center - left_x
    right_hw = right_x - center
    if not (left_hw > 0):
        return float("nan")
    return float(right_hw / left_hw)


def intensity_weighted_skewness(x: Sequence[float], y: Sequence[float]) -> float:
    """Return the intensity-weighted skewness of a peak's shape.

    Positive means a tail toward high ppm.  Uses only positive intensity as the
    weight; NaN when the weighted variance is degenerate.
    """
    np = _np()
    x = np.asarray(list(x), dtype=float)
    w = np.maximum(np.asarray(list(y), dtype=float), 0.0)
    total = float(np.sum(w))
    if total <= 0:
        return float("nan")
    m1 = float(np.sum(w * x) / total)
    m2 = float(np.sum(w * (x - m1) ** 2) / total)
    if m2 <= 0:
        return float("nan")
    m3 = float(np.sum(w * (x - m1) ** 3) / total)
    return float(m3 / m2 ** 1.5)


# ---------------------------------------------------------------------------
# Peak overlap / resolution diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlapDiagnostic:
    """Overlap of one peak with its nearest neighbour in the same spectrum."""

    nearest_peak_distance_ppm: float
    nearest_peak_distance_hz: float
    resolution: float
    overlap_fraction: float
    deconvolution_stable: bool
    overlap_warning: str


def peak_resolution(center1: float, fwhm1: float, center2: float, fwhm2: float) -> float:
    """Chromatographic resolution ``Rs = 2|c2 - c1| / (FWHM1 + FWHM2)``.

    ``Rs >= 1.5`` is baseline separation; ``Rs < 1`` means the peaks overlap
    enough that independently integrated areas are correlated.  This is a
    *diagnostic*, not an NMR validity rule.  NaN when both widths are zero.
    """
    denom = float(fwhm1) + float(fwhm2)
    if denom <= 0:
        return float("nan")
    return 2.0 * abs(float(center2) - float(center1)) / denom


def nearest_peak_overlap(
    centers: Sequence[float],
    fwhms: Sequence[float],
    *,
    observe_frequency_mhz: float = 0.0,
    resolution_warn: float = 1.0,
) -> list[OverlapDiagnostic]:
    """Per-peak overlap diagnostics against the nearest neighbouring peak.

    ``overlap_fraction`` is a monotone proxy in ``[0, 1]`` derived from the
    resolution (``exp(-Rs)``): ~0 when well separated, →1 as peaks merge.
    ``deconvolution_stable`` is ``False`` and a warning is emitted when
    ``resolution < resolution_warn`` -- a signal that independently integrated
    areas of the two peaks are not reliably separable and a joint deconvolution
    would be needed for trustworthy component areas.
    """
    np = _np()
    n = len(list(centers))
    c = [float(v) for v in centers]
    w = [float(v) for v in fwhms]
    out: list[OverlapDiagnostic] = []
    for i in range(n):
        if n < 2:
            out.append(
                OverlapDiagnostic(
                    float("nan"), float("nan"), float("nan"), 0.0, True, ""
                )
            )
            continue
        distances = [(abs(c[j] - c[i]), j) for j in range(n) if j != i]
        dist_ppm, j = min(distances, key=lambda item: item[0])
        rs = peak_resolution(c[i], w[i], c[j], w[j])
        overlap_fraction = (
            float(np.exp(-rs)) if np.isfinite(rs) else float("nan")
        )
        stable = not (np.isfinite(rs) and rs < float(resolution_warn))
        warning = "" if stable else f"peak_overlap_low_resolution (Rs={rs:.2f})"
        out.append(
            OverlapDiagnostic(
                nearest_peak_distance_ppm=dist_ppm,
                nearest_peak_distance_hz=dist_ppm * float(observe_frequency_mhz),
                resolution=rs,
                overlap_fraction=overlap_fraction,
                deconvolution_stable=bool(stable),
                overlap_warning=warning,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Model comparison
# ---------------------------------------------------------------------------


def compare_models(
    x: Sequence[float],
    y: Sequence[float],
    *,
    noise: float | None = None,
    models: Sequence[str] = ("gaussian", "lorentzian", "pseudo_voigt"),
) -> ModelComparison:
    """Fit each model and rank them by AICc (lower is better).

    ``delta_aicc[m] = AICc(m) - min AICc``; the winner has ``delta_aicc == 0``.
    Models whose fit failed get NaN criteria and are never selected as best.
    """
    np = _np()
    fits: dict[str, PeakFit] = {}
    aicc: dict[str, float] = {}
    bic: dict[str, float] = {}
    for name in models:
        fit = fit_peak(x, y, model=name)
        fits[name] = fit
        if fit.success and fit.model_curve is not None:
            diag = fit_diagnostics(
                y, fit.model_curve, n_params=fit.n_params, noise=noise
            )
            aicc[name] = diag["aicc"]
            bic[name] = diag["bic"]
        else:
            aicc[name] = float("nan")
            bic[name] = float("nan")

    # Selectable = anything that is not NaN (a perfect fit scores -inf and must
    # still be allowed to win); NaN marks a failed fit and is excluded.
    selectable = {k: v for k, v in aicc.items() if not np.isnan(v)}
    if selectable:
        best = min(selectable, key=selectable.get)
        best_aicc = selectable[best]
        delta = {}
        for k, v in aicc.items():
            if k == best:
                delta[k] = 0.0
            elif np.isnan(v):
                delta[k] = float("nan")
            else:
                delta[k] = float(v - best_aicc)
    else:
        best = ""
        delta = {k: float("nan") for k in aicc}
    return ModelComparison(
        best_model=best, aicc=aicc, bic=bic, delta_aicc=delta, fits=fits
    )
