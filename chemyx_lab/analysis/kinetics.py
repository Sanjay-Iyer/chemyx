"""Kinetic-model fitting for integrated-area vs elapsed-time series.

A small, explicit model registry -- zero-order, first-order decay, first-order
product formation, and first-order formation with a lag -- fit to
``area`` vs ``elapsed_hours`` by non-linear least squares.  Each fit reports the
rate constant with a t-based confidence interval, characteristic times
(half-life, t90, t95), goodness of fit, and residual diagnostics.

Model comparison uses **AICc** (small-sample-corrected Akaike information
criterion), not :math:`R^2`: adding parameters can only increase :math:`R^2`,
so :math:`R^2` cannot penalise an over-flexible model, whereas AICc and BIC do.

Correlated-residuals caveat
---------------------------
Ordinary least squares assumes independent residuals.  A reaction sampled
frequently in time usually violates this, which makes reported standard errors
*optimistic* (too small).  This first pass does not implement generalized least
squares with an AR(1) error model; instead it computes the Durbin-Watson
statistic, the lag-1 autocorrelation, and a Ljung-Box p-value, and raises a QC
warning (``residual_autocorrelation_optimistic_uncertainty``) when meaningful
autocorrelation is present so the intervals are not over-trusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .lineshapes import fit_diagnostics, ljung_box_pvalue


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
# Model functions (t is elapsed hours; k has units 1/hour, or area/hour)
# ---------------------------------------------------------------------------


def zero_order(t, a0: float, k: float):
    np = _np()
    return float(a0) + float(k) * np.asarray(t, dtype=float)


def first_order_decay(t, a0: float, k: float):
    np = _np()
    return float(a0) * np.exp(-float(k) * np.asarray(t, dtype=float))


def first_order_formation(t, plateau: float, k: float):
    np = _np()
    return float(plateau) * (1.0 - np.exp(-float(k) * np.asarray(t, dtype=float)))


def first_order_formation_lag(t, plateau: float, k: float, lag: float):
    np = _np()
    tt = np.maximum(np.asarray(t, dtype=float) - float(lag), 0.0)
    return float(plateau) * (1.0 - np.exp(-float(k) * tt))


@dataclass(frozen=True)
class _ModelSpec:
    func: Callable
    param_names: tuple[str, ...]
    rate_index: int  # which parameter is the reported rate constant


MODELS: dict[str, _ModelSpec] = {
    "zero_order": _ModelSpec(zero_order, ("a0", "k"), 1),
    "first_order_decay": _ModelSpec(first_order_decay, ("a0", "k"), 1),
    "first_order_formation": _ModelSpec(first_order_formation, ("plateau", "k"), 1),
    "first_order_formation_lag": _ModelSpec(
        first_order_formation_lag, ("plateau", "k", "lag"), 1
    ),
}


@dataclass(frozen=True)
class KineticFit:
    """Full result of fitting one kinetic model to one area-vs-time series."""

    analysis_target: str
    kinetic_model: str
    fit_success: bool
    n_observations: int
    rate_constant: float
    rate_constant_se: float
    rate_constant_ci95_low: float
    rate_constant_ci95_high: float
    half_life: float
    t90: float
    t95: float
    plateau_area: float
    lag_time: float
    fit_rmse: float
    r_squared: float
    aic: float
    aicc: float
    bic: float
    durbin_watson: float
    residual_lag1_autocorrelation: float
    ljung_box_pvalue: float
    fit_qc_pass: bool
    fit_qc_failure_reasons: str
    params: dict[str, float] = field(default_factory=dict)


def _initial_guess(model: str, t, a):
    np = _np()
    t = np.asarray(t, dtype=float)
    a = np.asarray(a, dtype=float)
    span_t = float(t[-1] - t[0]) or 1.0
    a_first, a_last, a_max = float(a[0]), float(a[-1]), float(np.max(a))
    k0 = 1.0 / span_t
    if model == "zero_order":
        p0 = [a_first, (a_last - a_first) / span_t]
        bounds = ([-np.inf, -np.inf], [np.inf, np.inf])
    elif model == "first_order_decay":
        p0 = [max(a_first, 1e-9), k0]
        bounds = ([0.0, 1e-12], [np.inf, np.inf])
    elif model == "first_order_formation":
        p0 = [max(a_max, 1e-9), k0]
        bounds = ([0.0, 1e-12], [np.inf, np.inf])
    elif model == "first_order_formation_lag":
        p0 = [max(a_max, 1e-9), k0, 0.0]
        bounds = ([0.0, 1e-12, 0.0], [np.inf, np.inf, max(span_t, 1e-9)])
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"unknown model {model!r}")
    return p0, bounds


def _derived_times(model: str, params: dict[str, float]) -> tuple[float, float, float, float, float]:
    """Return (half_life, t90, t95, plateau_area, lag_time) for a model fit."""
    nan = float("nan")
    k = params.get("k", nan)
    lag = params.get("lag", 0.0 if model == "first_order_formation_lag" else nan)
    if model == "zero_order":
        # No characteristic time for a constant-rate process.
        return nan, nan, nan, nan, nan
    if not (k > 0 and math.isfinite(k)):
        return nan, nan, nan, params.get("plateau", nan), lag
    half_life = math.log(2.0) / k
    t90 = math.log(10.0) / k
    t95 = math.log(20.0) / k
    if model == "first_order_decay":
        return half_life, t90, t95, 0.0, nan
    # formation / formation_lag: characteristic times measured from lag onset
    offset = lag if (model == "first_order_formation_lag" and math.isfinite(lag)) else 0.0
    plateau = params.get("plateau", nan)
    return half_life, t90 + offset, t95 + offset, plateau, lag


def fit_kinetic_model(
    times_hours: Sequence[float],
    areas: Sequence[float],
    *,
    model: str,
    analysis_target: str = "",
    noise: float | None = None,
    confidence: float = 0.95,
    autocorr_lag1_limit: float = 0.4,
    ljung_box_alpha: float = 0.05,
) -> KineticFit:
    """Fit one kinetic ``model`` to an area-vs-time series.

    Non-converging fits return ``fit_success = False`` with a reason and NaN
    parameters.  ``fit_qc_pass`` additionally requires enough observations, an
    identifiable rate constant (finite, positive where the model requires it,
    with a finite standard error), and no strong residual autocorrelation.
    """
    np = _np()
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; expected {sorted(MODELS)}")
    spec = MODELS[model]
    t = np.asarray(list(times_hours), dtype=float)
    a = np.asarray(list(areas), dtype=float)
    mask = np.isfinite(t) & np.isfinite(a)
    t, a = t[mask], a[mask]
    order = np.argsort(t)
    t, a = t[order], a[order]
    n = int(t.size)
    nan = float("nan")
    n_params = len(spec.param_names)

    def _fail(reason: str) -> KineticFit:
        return KineticFit(
            analysis_target=analysis_target, kinetic_model=model,
            fit_success=False, n_observations=n, rate_constant=nan,
            rate_constant_se=nan, rate_constant_ci95_low=nan,
            rate_constant_ci95_high=nan, half_life=nan, t90=nan, t95=nan,
            plateau_area=nan, lag_time=nan, fit_rmse=nan, r_squared=nan,
            aic=nan, aicc=nan, bic=nan, durbin_watson=nan,
            residual_lag1_autocorrelation=nan, ljung_box_pvalue=nan,
            fit_qc_pass=False, fit_qc_failure_reasons=reason, params={},
        )

    if n < n_params + 1:
        return _fail(f"insufficient_observations ({n} < {n_params + 1})")

    p0, bounds = _initial_guess(model, t, a)
    try:
        popt, pcov = _optimize().curve_fit(
            spec.func, t, a, p0=p0, bounds=bounds, max_nfev=20000
        )
    except Exception as exc:
        return _fail(f"curve_fit_failed: {exc}")

    params = {name: float(v) for name, v in zip(spec.param_names, popt)}
    perr = (
        np.sqrt(np.clip(np.diag(pcov), 0.0, np.inf))
        if np.all(np.isfinite(pcov))
        else np.full(n_params, np.nan)
    )
    k = params["k"]
    k_se = float(perr[spec.rate_index])
    dof = n - n_params
    if math.isfinite(k_se) and dof > 0:
        tcrit = float(_stats().t.ppf(0.5 + 0.5 * float(confidence), dof))
        k_ci_low = k - tcrit * k_se
        k_ci_high = k + tcrit * k_se
    else:
        k_ci_low = k_ci_high = nan

    predicted = spec.func(t, *popt)
    residuals = a - predicted
    diag = fit_diagnostics(a, predicted, n_params=n_params, noise=noise)
    lb_p = ljung_box_pvalue(residuals)
    half_life, t90, t95, plateau_area, lag_time = _derived_times(model, params)

    reasons: list[str] = []
    identifiable = math.isfinite(k) and math.isfinite(k_se) and k_se > 0
    if not identifiable:
        reasons.append("rate_constant_not_identifiable")
    if model != "zero_order" and not (k > 0):
        reasons.append("non_positive_rate_constant")
    if n < n_params + 2:
        reasons.append("few_observations_for_uncertainty")
    lag1 = diag["residual_lag1_autocorrelation"]
    if (math.isfinite(lag1) and abs(lag1) > float(autocorr_lag1_limit)) or (
        math.isfinite(lb_p) and lb_p < float(ljung_box_alpha)
    ):
        reasons.append("residual_autocorrelation_optimistic_uncertainty")

    return KineticFit(
        analysis_target=analysis_target,
        kinetic_model=model,
        fit_success=True,
        n_observations=n,
        rate_constant=k,
        rate_constant_se=k_se,
        rate_constant_ci95_low=k_ci_low,
        rate_constant_ci95_high=k_ci_high,
        half_life=half_life,
        t90=t90,
        t95=t95,
        plateau_area=plateau_area,
        lag_time=lag_time,
        fit_rmse=diag["fit_rmse"],
        r_squared=diag["r_squared"],
        aic=diag["aic"],
        aicc=diag["aicc"],
        bic=diag["bic"],
        durbin_watson=diag["durbin_watson"],
        residual_lag1_autocorrelation=lag1,
        ljung_box_pvalue=lb_p,
        fit_qc_pass=len(reasons) == 0,
        fit_qc_failure_reasons="; ".join(reasons),
        params=params,
    )


def fit_all_models(
    times_hours: Sequence[float],
    areas: Sequence[float],
    *,
    models: Sequence[str],
    analysis_target: str = "",
    noise: float | None = None,
    confidence: float = 0.95,
) -> tuple[list[KineticFit], str]:
    """Fit several models and return ``(fits, best_model_by_aicc)``.

    ``best_model_by_aicc`` is the model with the lowest AICc among fits that
    both succeeded and passed QC; empty string when none qualify.
    """
    np = _np()
    fits = [
        fit_kinetic_model(
            times_hours, areas, model=m, analysis_target=analysis_target,
            noise=noise, confidence=confidence,
        )
        for m in models
    ]
    candidates = {
        f.kinetic_model: f.aicc
        for f in fits
        if f.fit_success and f.fit_qc_pass and np.isfinite(f.aicc)
    }
    best = min(candidates, key=candidates.get) if candidates else ""
    return fits, best
