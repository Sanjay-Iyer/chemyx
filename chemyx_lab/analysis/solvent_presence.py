"""Independent full-spectrum sample-presence evidence for Si6 automation.

Magnitude FFT avoids dependence on target phasing, target presence, or regional
peak picking. Both configured solvent windows must contain a resolved signal.
Thresholds are in unnormalized FFT units and must be reviewed when gain/scans
or solvent changes. Missing sample evidence never represents zero target area.
"""
from __future__ import annotations

import hashlib
import math
from itertools import product

import numpy as np
from scipy.signal import find_peaks

from .nmr import build_magnitude_spectrum


DEFAULTS = {
    "consecutive_missing": 3,
    "minimum_height": 10000.0,
    "minimum_prominence": 5000.0,
    "minimum_snr": 10.0,
    "windows_ppm": [[1.90, 2.25], [6.80, 7.10]],
    "noise_windows_ppm": [[3.0, 4.0], [8.0, 9.0]],
    "expected_centers_ppm": [2.08, 6.99],
    "maximum_shift_ppm": [0.15, 0.10],
    "maximum_pair_shift_difference_ppm": 0.04,
}


def solvent_config(value):
    optional = {"expected_centers_ppm", "maximum_shift_ppm", "maximum_pair_shift_difference_ppm"}
    if not isinstance(value, dict) or set(value) - set(DEFAULTS) or (set(DEFAULTS) - optional) - set(value):
        raise ValueError("solvent_monitor requires supported solvent/noise windows and signal thresholds")
    result = dict(DEFAULTS, **value)
    if type(result["consecutive_missing"]) is not int or result["consecutive_missing"] < 1:
        raise ValueError("solvent_monitor.consecutive_missing must be a positive integer")
    for name in ("minimum_height", "minimum_prominence", "minimum_snr"):
        number = result[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"solvent_monitor.{name} must be finite and positive")
    for name in ("windows_ppm", "noise_windows_ppm"):
        windows = result[name]
        if not isinstance(windows, list) or len(windows) < (2 if name == "windows_ppm" else 1):
            raise ValueError(f"solvent_monitor.{name} needs at least {'two' if name == 'windows_ppm' else 'one'} windows")
        for window in windows:
            if not isinstance(window, list) or len(window) != 2 or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in window) or window[0] >= window[1]:
                raise ValueError(f"solvent_monitor.{name} requires finite increasing [low, high] pairs")
    all_windows = sorted(result["windows_ppm"] + result["noise_windows_ppm"])
    if any(b[0] <= a[1] for a, b in zip(all_windows, all_windows[1:])):
        raise ValueError("Solvent and noise windows must not overlap")
    if len(result["windows_ppm"]) != 2:
        raise ValueError("Si6 sample confirmation requires exactly two solvent windows")
    for name in ("expected_centers_ppm", "maximum_shift_ppm"):
        numbers = result[name]
        if not isinstance(numbers, list) or len(numbers) != 2 or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
            raise ValueError(f"solvent_monitor.{name} requires two finite numbers")
    for center, shift, (low, high) in zip(result["expected_centers_ppm"], result["maximum_shift_ppm"], result["windows_ppm"]):
        if not low < center < high or shift <= 0:
            raise ValueError("Expected solvent centers must be inside their windows with positive maximum shifts")
    difference = result["maximum_pair_shift_difference_ppm"]
    if isinstance(difference, bool) or not isinstance(difference, (int, float)) or not math.isfinite(difference) or difference <= 0:
        raise ValueError("maximum_pair_shift_difference_ppm must be finite and positive")
    return result


def evaluate_solvent_spectrum(ppm, magnitude, settings):
    cfg = solvent_config(settings)
    x, y = np.asarray(ppm), np.asarray(magnitude)
    if len(x) != len(y) or len(x) < 10 or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("Solvent check requires a finite full spectrum")
    order = np.argsort(x)
    x, y = x[order], y[order]
    if np.any(np.diff(x) <= 0):
        raise ValueError("Solvent spectrum ppm axis must be strictly monotonic")
    differences = []
    for low, high in cfg["noise_windows_ppm"]:
        local = y[(x >= low) & (x <= high)]
        if len(local) < 10:
            return {"present": False, "reason": "noise window outside acquisition coverage", "windows": [], "noise": None}
        differences.extend(np.diff(local))
    differences = np.asarray(differences)
    noise = max(float(np.median(abs(differences - np.median(differences))) / (.67448975 * np.sqrt(2))), 1.e-12)
    # Detect on the full trace: a peak outside a window cannot masquerade as an
    # in-window maximum because its clipped flank ends at the window boundary.
    indices, properties = find_peaks(y, prominence=cfg["minimum_prominence"])
    evidence = []
    for (low, high), center, shift in zip(cfg["windows_ppm"], cfg["expected_centers_ppm"], cfg["maximum_shift_ppm"]):
        mask = (x >= low) & (x <= high)
        local_x, local_y = x[mask], y[mask]
        candidates = []
        covered = x[0] <= low and x[-1] >= high and len(local_y) >= 10
        if covered:
            for index, prominence in zip(indices, properties["prominences"]):
                ppm = float(x[index])
                if not low <= ppm <= high or abs(ppm-center) > shift + 1.e-9:
                    continue
                height = float(y[index])
                snr = float(prominence / noise)
                if height >= cfg["minimum_height"] and snr >= cfg["minimum_snr"]:
                    candidates.append({"ppm": ppm, "height": height, "prominence": float(prominence), "snr": snr, "shift_ppm": ppm-center})
        best = max(candidates, key=lambda p: p["prominence"], default=None)
        evidence.append({"window_ppm": [low, high], "expected_center_ppm": center, "maximum_shift_ppm": shift,
                         "covered": covered, "present": best is not None, "peak": best, "candidates": candidates})
    pairs = [pair for pair in product(*(w["candidates"] for w in evidence))
             if abs(pair[0]["shift_ppm"]-pair[1]["shift_ppm"]) <= cfg["maximum_pair_shift_difference_ppm"]]
    pair = max(pairs, key=lambda p: sum(v["prominence"] for v in p), default=None)
    if pair:
        for window, peak in zip(evidence, pair):
            window["peak"] = peak
    present = pair is not None
    return {"present": present, "reason": "both solvent signals present with consistent shifts" if present else "required solvent pair missing, weak, out of coverage or inconsistent in shift",
            "pair_shift_difference_ppm": abs(pair[0]["shift_ppm"]-pair[1]["shift_ppm"]) if pair else None,
            "noise": noise, "windows": evidence}


def solvent_presence(source, settings):
    spectrum = build_magnitude_spectrum(source, zero_fill_points=65536, line_broadening_hz=.3)
    result = evaluate_solvent_spectrum(spectrum.ppm_axis, spectrum.magnitude, settings)
    result.update(raw_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  source_path=str(source), method="full unnormalized magnitude FFT; 65536 points; 0.3 Hz broadening", criteria=dict(settings))
    return result


def record_sample_presence(row, history, settings, evidence):
    """Stage-local consecutive failures and irreversible timed fallback latch."""
    missing = 0 if evidence["present"] else (history[-1].get("solvent_missing_streak", 0) if history else 0) + 1
    fallback = bool(history and history[-1].get("stage_timed_fallback")) or missing >= settings["consecutive_missing"]
    row.update(solvent_present=bool(evidence["present"]), solvent_missing_streak=missing,
               stage_timed_fallback=fallback, solvent_evidence=evidence,
               endpoint_eligible=bool(evidence["present"]) and not fallback)
    if not evidence["present"]:
        row.update(peak_area=None, peak_ppm=None, peak_clear=False,
                   signal_classification="sample_presence_unconfirmed",
                   metric_source="unavailable: required solvent signals missing; target area is not zero",
                   diagnostic_notes="Solvent/sample probably low or not reaching NMR; acquisition excluded from chemical endpoint")
    if fallback:
        row["diagnostic_notes"] = (row.get("diagnostic_notes", "") + "; timed fallback latched for this stage; continue to configured duration").strip("; ")
    return row
