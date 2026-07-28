"""Internal-standard normalization with correct uncertainty propagation.

Optional feature: when an internal standard is configured (a peak family, or a
fixed ppm window with a known integral), analyte areas can be reported relative
to it so run-to-run intensity drift cancels.  The analyte-to-standard ratio and
its uncertainty are computed by first-order error propagation for a quotient::

    ratio          = A / S
    (u_ratio/ratio)^2 = (u_A/A)^2 + (u_S/S)^2      (A, S independent)

which is exact to first order and assumes the analyte and standard area
uncertainties are independent.

Quantitative caveat
-------------------
A peak-area ratio is only a true molar ratio when the acquisition satisfies
qNMR conditions (a relaxation delay long enough for full recovery, a 90-degree
or well-characterised pulse, adequate SNR).  Those parameters are recorded
alongside the ratio; when they are unknown the ratio is a *relative* quantity,
not a calibrated concentration ratio, and callers must say so.
"""

from __future__ import annotations

from dataclasses import dataclass


def _np():
    import numpy as np

    return np


@dataclass(frozen=True)
class NormalizationResult:
    """Analyte area normalized to an internal standard, with uncertainty."""

    internal_standard_area: float
    internal_standard_area_uncertainty: float
    normalized_area: float
    normalized_area_uncertainty: float
    analyte_to_standard_ratio: float
    ratio_uncertainty: float
    normalization_qc_pass: bool
    normalization_failure_reason: str


def ratio_with_uncertainty(
    analyte_area: float,
    standard_area: float,
    *,
    analyte_uncertainty: float = float("nan"),
    standard_uncertainty: float = float("nan"),
    min_abs_standard: float = 1e-12,
) -> NormalizationResult:
    """Return the analyte/standard ratio and its propagated uncertainty.

    The ratio is undefined when the standard area is at or below
    ``min_abs_standard`` (dividing by ~0), in which case NaN values and a reason
    are returned.  When either input uncertainty is missing (NaN) the ratio is
    still computed but its uncertainty is NaN and QC records the omission.
    """
    np = _np()
    a = float(analyte_area)
    s = float(standard_area)
    reasons: list[str] = []
    if not np.isfinite(s) or abs(s) <= float(min_abs_standard):
        return NormalizationResult(
            internal_standard_area=s,
            internal_standard_area_uncertainty=float(standard_uncertainty),
            normalized_area=float("nan"),
            normalized_area_uncertainty=float("nan"),
            analyte_to_standard_ratio=float("nan"),
            ratio_uncertainty=float("nan"),
            normalization_qc_pass=False,
            normalization_failure_reason="standard_area_near_zero",
        )
    ratio = a / s
    ua, us = float(analyte_uncertainty), float(standard_uncertainty)
    if np.isfinite(ua) and np.isfinite(us) and a != 0:
        rel = np.sqrt((ua / a) ** 2 + (us / s) ** 2)
        ratio_unc = abs(ratio) * float(rel)
    elif np.isfinite(ua) and np.isfinite(us) and a == 0:
        # ratio is 0; only the standard term contributes at first order via ua/s
        ratio_unc = abs(ua / s)
    else:
        ratio_unc = float("nan")
        reasons.append("input_uncertainty_missing")
    return NormalizationResult(
        internal_standard_area=s,
        internal_standard_area_uncertainty=us,
        normalized_area=ratio,
        normalized_area_uncertainty=ratio_unc,
        analyte_to_standard_ratio=ratio,
        ratio_uncertainty=ratio_unc,
        normalization_qc_pass=len(reasons) == 0,
        normalization_failure_reason="; ".join(reasons),
    )
