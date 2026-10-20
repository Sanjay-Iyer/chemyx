"""Offline SciPy boundary experiments. No instrument interfaces or run authority.

Identification may smooth a copy; integration always uses the original ALS real
trace. The fixed comparator deliberately preserves production's sampled feet.
Dynamic feet are interpolated, explicitly distinguishing this small convention
difference from a change in integration boundaries.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import math

import numpy as np
from scipy.signal import find_peaks, peak_prominences, peak_widths, savgol_filter

from .nmr import _trapezoid, integrate_above_local_baseline


@dataclass(frozen=True)
class DetectionSettings:
    name: str = "reference"
    search_low: float = 5.60
    search_high: float = 6.00
    prominence_snr: float = 5.0
    height_snr: float = 5.0
    distance_ppm: float = 0.02
    minimum_width_ppm: float = 0.012
    maximum_width_ppm: float = 0.18
    expected_ppm: float = 5.79
    maximum_shift_ppm: float = 0.12
    smoothing_ppm: float = 0.006
    prominence_window_ppm: float = 0.40
    maximum_boundary_half_span_ppm: float = 0.18
    envelope_gap_ppm: float = 0.025
    baseline_noise_tolerance: float = 2.0
    baseline_run_ppm: float = 0.008


METHODS = ("fixed", "width_50", "width_75", "width_90", "width_95",
           "prominence_bases", "local_minimum", "adaptive_noise")
METHOD_LABELS = {"fixed": "Original fixed 5.70–5.90", "width_50": "Half prominence (0.50)",
    "width_75": "Width at rel_height 0.75", "width_90": "Width at rel_height 0.90",
    "width_95": "Width at rel_height 0.95", "prominence_bases": "Prominence bases",
    "local_minimum": "Guarded local minima", "adaptive_noise": "Adaptive noise return",
    "production_variable": "Existing peaks_simple area"}


def parameter_sweep():
    """17 one-factor-at-a-time profiles; not an exhaustive or tuned grid."""
    base = DetectionSettings()
    variations = [
        ("search_narrow", {"search_low": 5.70, "search_high": 5.90}),
        ("search_wide", {"search_low": 5.50, "search_high": 6.10}),
        ("prominence_3", {"prominence_snr": 3.}),
        ("prominence_8", {"prominence_snr": 8.}),
        ("prominence_12", {"prominence_snr": 12.}),
        ("height_3", {"height_snr": 3.}), ("height_8", {"height_snr": 8.}),
        ("width_lenient", {"minimum_width_ppm": .006, "maximum_width_ppm": .22}),
        ("width_strict", {"minimum_width_ppm": .020, "maximum_width_ppm": .12}),
        ("distance_001", {"distance_ppm": .01}), ("distance_004", {"distance_ppm": .04}),
        ("shift_006", {"maximum_shift_ppm": .06}), ("shift_020", {"maximum_shift_ppm": .20}),
        ("expected_583", {"expected_ppm": 5.83}),
        ("no_smoothing", {"smoothing_ppm": 0.}),
        ("smoothing_0012", {"smoothing_ppm": .012}),
    ]
    return [base] + [replace(base, name=name, **values) for name, values in variations]


def ascending(ppm, intensity):
    x, y = np.asarray(ppm, float), np.asarray(intensity, float)
    if x.ndim != 1 or x.shape != y.shape or x.size < 3 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("Expected finite matching 1-D spectral arrays")
    order = np.argsort(x)
    x, y = x[order], y[order]
    if np.any(np.diff(x) <= 0):
        raise ValueError("ppm coordinates must be unique")
    return x, y


def integration_trace(ppm, intensity, low, high, *, interpolate=True):
    """Return the exact integration coordinates, original signal and foot line."""
    x, y = ascending(ppm, intensity)
    low, high = sorted((float(low), float(high)))
    if low < x[0] or high > x[-1] or high <= low:
        raise ValueError("Integration boundaries outside the spectrum or zero width")
    if interpolate:
        mask = (x > low) & (x < high)
        xx = np.r_[low, x[mask], high]
        yy = np.interp(xx, x, y)
    else:
        mask = (x >= low) & (x <= high)
        xx, yy = x[mask], y[mask]
    if xx.size < 3:
        raise ValueError("Integration region has fewer than three points")
    # Production uses linspace on its uniform grid. Dynamic boundaries may have
    # short partial endpoint steps, so their baseline must be linear in ppm.
    baseline = (np.interp(xx, [xx[0], xx[-1]], [yy[0], yy[-1]]) if interpolate
                else np.linspace(yy[0], yy[-1], yy.size))
    return xx, yy, baseline


def integrate_interval(ppm, intensity, low, high, *, interpolate=True):
    x, y, baseline = integration_trace(ppm, intensity, low, high, interpolate=interpolate)
    if not interpolate:
        result = integrate_above_local_baseline(ppm, intensity, left_ppm=low, right_ppm=high)
        return result.positive_area, result.signed_area
    residual = y - baseline
    return float(_trapezoid(np, np.maximum(residual, 0), x)), float(_trapezoid(np, residual, x))


def detection_context(ppm, intensity, noise, settings):
    x, y = ascending(ppm, intensity)
    region = (x >= 5.30) & (x <= 6.30)
    x, y = x[region], y[region]
    if x.size < 9 or not math.isfinite(noise) or noise <= 0:
        raise ValueError("Insufficient spectrum or invalid production regional-noise estimate")
    step = float(np.median(np.diff(x)))
    window = max(5, round(settings.smoothing_ppm / step) | 1)
    window = min(window, x.size if x.size % 2 else x.size - 1)
    smooth = savgol_filter(y, window, 3) if settings.smoothing_ppm > 0 else y.copy()
    wlen = max(3, round(settings.prominence_window_ppm / step) | 1)
    # All maxima are retained for nearby-peak/overlap guards, even if a wider
    # minimum-distance setting discards one in the qualified candidate list.
    all_idx, _ = find_peaks(smooth, prominence=3 * noise, width=.004 / step)
    idx, _ = find_peaks(smooth, height=settings.height_snr * noise,
        prominence=settings.prominence_snr * noise,
        distance=max(1, round(settings.distance_ppm / step)),
        width=(settings.minimum_width_ppm / step, settings.maximum_width_ppm / step), wlen=wlen)
    prominences, left_bases, right_bases = peak_prominences(smooth, idx, wlen=wlen)
    widths, _, left_ips, right_ips = peak_widths(smooth, idx, .5,
        prominence_data=(prominences, left_bases, right_bases))
    candidates = []
    for j, i in enumerate(idx):
        if settings.search_low <= x[i] <= settings.search_high:
            candidates.append({"index": int(i), "center": float(x[i]), "height": float(smooth[i]),
                "snr": float(smooth[i] / noise), "prominence": float(prominences[j]),
                "prominence_snr": float(prominences[j] / noise), "width": float(widths[j] * step),
                "left_base": int(left_bases[j]), "right_base": int(right_bases[j]),
                "left_half": float(np.interp(left_ips[j], np.arange(x.size), x)),
                "right_half": float(np.interp(right_ips[j], np.arange(x.size), x))})
    return {"x": x, "y": y, "smooth": smooth, "noise": noise, "step": step,
            "candidates": candidates, "nearby_indices": all_idx, "wlen": wlen}


def choose_target(context, settings, previous_center=None):
    """Bounded nearest expected/prior selection; no tallest-peak substitution."""
    candidates = [p for p in context["candidates"]
                  if abs(p["center"] - settings.expected_ppm) <= settings.maximum_shift_ppm]
    if previous_center is not None:
        candidates = [p for p in candidates if abs(p["center"] - previous_center) <= settings.maximum_shift_ppm]
    if not candidates:
        return None, "missing", ["no qualified bounded target; area is missing, never zero-filled"]
    anchor = settings.expected_ppm if previous_center is None else previous_center
    candidates.sort(key=lambda p: (abs(p["center"] - anchor), abs(p["center"] - settings.expected_ppm), p["index"]))
    chosen = candidates[0]
    flags = []
    if len(candidates) > 1:
        flags.append("multiple qualified candidates; chemical identity not proven")
        if abs(abs(candidates[1]["center"] - anchor) - abs(chosen["center"] - anchor)) < .015:
            flags.append("competing candidates similarly close to tracking anchor")
            return chosen, "ambiguous", flags
    return chosen, "success", flags


def envelope_indices(context, peak, settings):
    """Geometric group only: close submaxima are an ambiguous envelope, not species."""
    x = context["x"]
    members = [peak["index"]]
    for i in context["nearby_indices"]:
        if abs(x[i] - peak["center"]) <= settings.envelope_gap_ppm:
            members.append(int(i))
    return sorted(set(members))


def boundary_interval(context, peak, method, settings):
    x, y, smooth, step, noise = (context[k] for k in ("x", "y", "smooth", "step", "noise"))
    i = peak["index"]
    flags = []
    if method.startswith("width_"):
        rel_height = int(method.split("_")[1]) / 100
        prom_data = (np.array([peak["prominence"]]), np.array([peak["left_base"]], np.intp),
                     np.array([peak["right_base"]], np.intp))
        _, _, left, right = peak_widths(smooth, np.array([i]), rel_height, prominence_data=prom_data)
        return float(np.interp(left[0], np.arange(x.size), x)), float(np.interp(right[0], np.arange(x.size), x)), flags
    if method == "prominence_bases":
        return float(x[peak["left_base"]]), float(x[peak["right_base"]]), flags
    members = envelope_indices(context, peak, settings)
    if len(members) > 1:
        flags.append("multiplet-like/overlapping envelope; geometrical grouping cannot establish identity")
    outer_left, outer_right = members[0], members[-1]
    lo = max(0, int(np.searchsorted(x, peak["center"] - settings.maximum_boundary_half_span_ppm)))
    hi = min(x.size - 1, int(np.searchsorted(x, peak["center"] + settings.maximum_boundary_half_span_ppm)))
    # Never pass a distinct resolved maximum. Stop at the lowest intervening
    # valley and flag a split when the signal has not returned to baseline.
    for n in context["nearby_indices"]:
        if n < outer_left and n >= lo:
            lo = max(lo, int(n + np.argmin(smooth[n:outer_left + 1])))
        elif n > outer_right and n <= hi:
            hi = min(hi, int(outer_right + np.argmin(smooth[outer_right:n + 1])))
    baseline_level = max(float(smooth[peak["left_base"]]), float(smooth[peak["right_base"]]))
    tolerance = settings.baseline_noise_tolerance * noise
    threshold = baseline_level + tolerance
    minima, _ = find_peaks(-smooth, prominence=max(noise * .5, peak["prominence"] * .005))
    run = max(3, int(round(settings.baseline_run_ppm / step)))
    bounds = []
    for direction, edge, start in ((-1, lo, outer_left), (1, hi, outer_right)):
        found = None
        for j in range(start + direction, edge + direction, direction):
            if method == "local_minimum":
                if j in minima and smooth[j] <= threshold:
                    found = j
                    break
            elif method == "adaptive_noise":
                a, b = (j - run + 1, j + 1) if direction < 0 else (j, j + run)
                if a >= lo and b <= hi + 1 and np.all(smooth[a:b] <= threshold):
                    found = j
                    break
            else:
                raise ValueError(f"Unknown boundary method {method}")
        if found is None:
            found = edge
            flags.append(("low" if direction < 0 else "high") + " boundary reached guard before reliable baseline return")
        elif method == "adaptive_noise":
            # Interpolate the threshold crossing; the final intensity is then
            # interpolated from the original unsmoothed processed real array.
            inner = found - direction
            denominator = smooth[inner] - smooth[found]
            t = np.clip((threshold - smooth[found]) / denominator, 0, 1) if denominator else 0.
            bounds.append(float(x[found] + t * (x[inner] - x[found])))
            continue
        bounds.append(float(x[found]))
    return *bounds, flags


def evaluate(context, peak, detection_status, detection_flags, method, settings, *, fixed_area):
    nan = float("nan")
    result = {"method": method, "configuration": settings.name, "parameters": json.dumps(asdict(settings), sort_keys=True),
        "peak_center_ppm": peak["center"] if peak else nan, "peak_width_ppm": peak["width"] if peak else nan,
        "prominence": peak["prominence"] if peak else nan, "snr": peak["snr"] if peak else nan,
        "boundary_low_ppm": nan, "boundary_high_ppm": nan, "area_dynamic": nan, "area_signed": nan,
        "area_fixed": fixed_area, "detection_status": detection_status, "qc_flags": list(detection_flags),
        "integration_valid": False, "nearby_peak_count": 0, "envelope_maxima": 0,
        "edge_height_fraction": nan, "boundary_width_ppm": nan, "endpoint_interpolation": method != "fixed"}
    if peak is None and method != "fixed":
        return result
    if method == "fixed":
        low, high, flags = 5.70, 5.90, []
    else:
        low, high, flags = boundary_interval(context, peak, method, settings)
    result["qc_flags"].extend(flags)
    x, y = context["x"], context["y"]
    area, signed = integrate_interval(x, y, low, high, interpolate=method != "fixed")
    result.update(boundary_low_ppm=low, boundary_high_ppm=high, area_dynamic=area, area_signed=signed,
                  integration_valid=True, boundary_width_ppm=high - low)
    if peak is not None:
        members = envelope_indices(context, peak, settings)
        nearby = [int(n) for n in context["nearby_indices"] if low < x[n] < high and n not in members]
        result.update(nearby_peak_count=len(nearby), envelope_maxima=len(members))
        edge_level = max(np.interp(low, x, context["smooth"]), np.interp(high, x, context["smooth"]))
        contour = context["smooth"][peak["index"]] - peak["prominence"]
        result["edge_height_fraction"] = float(max(0., edge_level - contour) / max(peak["prominence"], 1e-12))
        if nearby:
            result["qc_flags"].append("other significant maxima inside integration interval; target-only area ambiguous")
            result["detection_status"] = "ambiguous"
        if len(members) > 1:
            result["qc_flags"].append("multiple nearby maxima in target envelope; multiplet versus overlap unresolved")
            result["detection_status"] = "ambiguous"
        if method != "fixed" and (low < peak["center"] - settings.maximum_boundary_half_span_ppm
                                  or high > peak["center"] + settings.maximum_boundary_half_span_ppm):
            result["qc_flags"].append("boundaries exceed conservative target half-span")
        if flags and any("guard" in f for f in flags):
            result["detection_status"] = "unreliable_boundary"
    result["area_difference"] = area - fixed_area
    result["relative_difference_percent"] = 100 * (area - fixed_area) / fixed_area if fixed_area > 1 else nan
    return result
