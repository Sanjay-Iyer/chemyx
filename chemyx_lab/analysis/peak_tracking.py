"""Si6 candidate controls; phase and baseline processing remain unchanged."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import math

DEFAULTS = {
    "search": {"enabled": True, "half_width_ppm": .20},
    "previous_shift": {"enabled": True, "maximum_ppm": .08},
    "reference_shift": {"enabled": False, "maximum_ppm": .15},
    "continuity": {"enabled": True},
    "snr": {"enabled": True, "minimum": 5.0},
    "prominence": {"enabled": True, "minimum_snr": 5.0},
    "absolute_prominence": {"enabled": False, "minimum": 0.0},
    "width": {"enabled": True, "minimum_ppm": .015, "maximum_ppm": .167},
    "separation": {"enabled": True, "minimum_ppm": .04},
    "height": {"enabled": False, "minimum": 0.0},
    "area": {"enabled": True, "minimum": 1.e-12},
    "shoulder": {"enabled": False, "maximum_asymmetry": .5},
    "slope": {"enabled": False, "minimum_flank_fraction": .05},
    "shape": {"enabled": False, "minimum_interpolation_quality": .8},
    "neighbor": {"enabled": False, "minimum_distance_widths": 2.0},
}


def identification_config(value):
    """Live identity parameters, explicitly disabling every retrospective filter."""
    allowed = {"search", "previous_shift", "reference_shift", "continuity", "prominence",
               "absolute_prominence", "width", "separation"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("analysis.peak_finding supports identity parameters only; put QC filters in qc_reporting")
    cfg = tracking_config(value)
    for name in set(cfg) - allowed:
        cfg[name]["enabled"] = False
    return cfg


def tracking_config(value):
    if not isinstance(value, dict):
        raise ValueError("analysis.peak_tracking mapping required; migrate the two-stage configuration")
    result = deepcopy(DEFAULTS)
    if set(value) - set(result):
        raise ValueError("Unknown peak_tracking filters: " + str(set(value) - set(result)))
    for name, options in value.items():
        if not isinstance(options, dict) or set(options) - set(result[name]):
            raise ValueError(f"Unsupported peak_tracking.{name} parameters")
        result[name].update(options)
    for name, options in result.items():
        if type(options["enabled"]) is not bool:
            raise ValueError(f"peak_tracking.{name}.enabled must be boolean")
        for key, number in options.items():
            if key != "enabled" and (isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0):
                raise ValueError(f"peak_tracking.{name}.{key} must be finite and nonnegative")
    if result["width"]["minimum_ppm"] > result["width"]["maximum_ppm"]:
        raise ValueError("Peak width minimum exceeds maximum")
    return result


def configure_candidates(args, value):
    """Generate candidates permissively; live tracker applies identity controls."""
    cfg = tracking_config(value)
    args.min_prominence_snr = 0.0
    args.min_peak_width_ppm = 1.e-12
    args.min_peak_distance_ppm = max(1.e-12, cfg["separation"]["minimum_ppm"]) if cfg["separation"]["enabled"] else 1.e-12
    # Review QC and automatic QC-dependent reference correction are offline.
    # The live profile uses the vendor ppm axis and comparable unnormalized area.
    args.reference_model = "metadata"
    args.reference_method = "metadata"
    args.reference_observed_ppm = args.reference_expected_ppm = None
    args.solvent = "none"
    args.normalization = "none"
    args.qc_min_snr = -1.e300
    args.qc_min_prominence_snr = 0.0
    args.qc_min_width_hz = 0.0
    args.qc_max_width_hz = 1.e300
    args.qc_require_positive_area = False
    return args


def candidate_records(picked):
    import numpy as np
    records = []
    x, y = picked.ppm_axis, picked.quantitative_corrected
    integrate = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
    for peak in picked.peaks:
        record = asdict(peak)
        mask = abs(x - peak.interpolated_ppm) <= peak.width_ppm
        if mask.sum() < 2:
            # Retain undersampled artifacts for rejection auditing, never hide
            # them and subsequently interpret the spectrum as nondetection.
            record.update(integration_left_ppm=peak.interpolated_ppm,
                          integration_right_ppm=peak.interpolated_ppm,
                          ppm_resolution=float(np.median(np.diff(x))),
                          negative_area=0.0, asymmetry=1.0, flank_fraction=0.0)
            records.append(record)
            continue
        xx, yy = x[mask], y[mask]
        record.update(integration_left_ppm=float(xx[0]), integration_right_ppm=float(xx[-1]),
                      ppm_resolution=float(np.median(np.diff(x))),
                      negative_area=float(integrate(np.maximum(-yy, 0), xx)))
        left = yy[xx <= peak.interpolated_ppm]
        right = yy[xx > peak.interpolated_ppm]
        left_area = float(integrate(np.maximum(left, 0), xx[xx <= peak.interpolated_ppm])) if len(left)>1 else 0
        right_area = float(integrate(np.maximum(right, 0), xx[xx > peak.interpolated_ppm])) if len(right)>1 else 0
        record["asymmetry"] = abs(left_area-right_area)/max(left_area+right_area, 1.e-30)
        record["flank_fraction"] = float(min(peak.peak_height-yy[0], peak.peak_height-yy[-1])/max(abs(peak.peak_height), 1.e-30))
        records.append(record)
    return records
