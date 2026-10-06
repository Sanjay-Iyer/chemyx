"""Deterministic stage evidence; no instrument access or spectrum processing."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np


def validate_completion(value: dict) -> dict:
    fields = {"trend", "minimum_points", "minimum_duration_hours", "window_points",
              "consecutive_confirmations", "minimum_progress_fraction", "low_fraction",
              "max_window_range_fraction", "max_abs_slope_fraction_per_hour",
              "expected_duration_hours"}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError("completion must be a mapping with supported fields")
    if value.get("trend") not in {"decreasing", "increasing"}:
        raise ValueError("completion.trend must be decreasing or increasing")
    for key in ("minimum_points", "window_points", "consecutive_confirmations"):
        if type(value.get(key)) is not int or value[key] < (2 if key != "consecutive_confirmations" else 1):
            raise ValueError(f"completion.{key} must be a positive integer (windows/points >= 2)")
    for key in ("minimum_duration_hours", "minimum_progress_fraction", "max_window_range_fraction",
                "max_abs_slope_fraction_per_hour"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"completion.{key} must be finite and positive")
    if not 0 < value["minimum_progress_fraction"] < 1:
        raise ValueError("completion.minimum_progress_fraction must be between zero and one")
    if value["trend"] == "decreasing":
        low = value.get("low_fraction")
        if isinstance(low, bool) or not isinstance(low, (int, float)) or not math.isfinite(low) or not 0 < low < 1:
            raise ValueError("decreasing completion.low_fraction must be between zero and one")
    if "expected_duration_hours" in value:
        number = value["expected_duration_hours"]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError("completion.expected_duration_hours must be positive")
    return dict(value)


def completion_evidence(rows: list[dict], rules: dict) -> dict:
    """Require progress plus several overlapping stable, QC-qualified windows.

    Areas are normalized to the first positive valid observation for decreasing
    stages and the largest observed area for increasing stages. Uncertainty is
    included in low/range tests. Invalid rows and metadata timing fail closed.
    """
    result = {"complete": False, "trend": rules["trend"], "points": len(rows),
              "criteria": dict(rules), "reason": "insufficient evidence"}
    needed = max(rules["minimum_points"], rules["window_points"] + rules["consecutive_confirmations"] - 1)
    if len(rows) < needed:
        return result
    try:
        if any(r.get("error") or not r.get("measurement_valid", r.get("peak_clear", False)) for r in rows):
            raise ValueError("invalid measurement in stage history")
        areas = np.array([float(r.get("completion_area", r["peak_area"])) for r in rows])
        uncertainty = np.array([float(r.get("area_uncertainty", 0)) for r in rows])
        times = [datetime.fromisoformat(r["acquired_at"]) for r in rows]
        hours = np.array([(t - times[0]).total_seconds() / 3600 for t in times])
        if not np.all(np.isfinite(areas)) or not np.all(np.isfinite(uncertainty)) or np.any(areas < 0) or np.any(uncertainty < 0):
            raise ValueError("nonfinite or negative measurement")
        if np.any(np.diff(hours) <= 0):
            raise ValueError("Strictly increasing acquisition metadata times required")
        if rules["trend"] == "decreasing" and (areas[0] <= 0 or not rows[0].get("peak_clear")):
            raise ValueError("Decreasing stage requires a positive detected initial peak")
        if rules["trend"] == "increasing" and (max(areas) <= 0 or not any(r.get("peak_clear") for r in rows)):
            raise ValueError("Increasing stage requires subsequent detected signal growth")
    except (KeyError, TypeError, ValueError) as exc:
        result["reason"] = str(exc)
        return result
    scale = areas[0] if rules["trend"] == "decreasing" else float(max(areas))
    checks = []
    for end in range(len(rows) - rules["consecutive_confirmations"] + 1, len(rows) + 1):
        start = end - rules["window_points"]
        a, u, t = areas[start:end], uncertainty[start:end], hours[start:end]
        span = float(max(a + u) - min(a - u)) / scale
        slope = float(np.polyfit(t - t[0], a / scale, 1)[0])
        progress = float((areas[0] - max(a + u)) / scale if rules["trend"] == "decreasing" else (min(a - u) - areas[0]) / scale)
        low = bool(max(a + u) / scale <= rules["low_fraction"]) if rules["trend"] == "decreasing" else True
        near_maximum = bool(min(a - u) / scale >= 1 - rules["max_window_range_fraction"]) if rules["trend"] == "increasing" else True
        passed = (end >= rules["minimum_points"] and hours[end - 1] >= rules["minimum_duration_hours"]
                  and low and near_maximum and progress >= rules["minimum_progress_fraction"]
                  and span <= rules["max_window_range_fraction"]
                  and abs(slope) <= rules["max_abs_slope_fraction_per_hour"])
        checks.append({"end_point": end, "elapsed_hours": float(hours[end - 1]),
                       "range_fraction": span, "slope_fraction_per_hour": slope,
                       "progress_fraction": progress, "low_region": low, "near_observed_maximum": near_maximum, "passed": bool(passed)})
    result.update(complete=all(c["passed"] for c in checks), normalization_area=float(scale),
                  initial_area=float(areas[0]), checks=checks,
                  reason="sustained completion" if all(c["passed"] for c in checks) else "criteria not sustained")
    return result
