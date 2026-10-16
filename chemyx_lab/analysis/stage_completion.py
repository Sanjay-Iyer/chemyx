"""Deterministic stage evidence; no instrument access or spectrum processing."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np


def validate_area_completion(value: dict) -> dict:
    """Strict, intentionally small live endpoint schema; no QC fields."""
    common = {"method", "trend", "max_iterations", "consecutive_iterations", "epsilon"}
    trend = value.get("trend")
    extra = {"near_zero_fraction"} if trend == "decreasing" else {"relative_change_threshold", "minimum_growth"}
    if trend not in {"decreasing", "increasing"} or set(value) - common - extra:
        raise ValueError("area_only completion needs decreasing/increasing trend and supported area fields")
    result = dict(value)
    result.setdefault("epsilon", 1.e-12)
    for name in (("max_iterations",) if "max_iterations" in result else ()) + ("consecutive_iterations",):
        if type(result.get(name)) is not int or result[name] < (2 if trend == "increasing" and name == "consecutive_iterations" else 1):
            raise ValueError(f"completion.{name} must be a positive integer (plateau observations >= 2)")
    if "max_iterations" in result and result["max_iterations"] < result["consecutive_iterations"]:
        raise ValueError("max_iterations must accommodate consecutive_iterations")
    for name in ("epsilon", "near_zero_fraction" if trend == "decreasing" else "relative_change_threshold"):
        number = result.get(name)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"completion.{name} must be finite and positive")
        if name != "epsilon" and number >= 1:
            raise ValueError(f"completion.{name} must be below one")
    if trend == "increasing":
        growth = result.get("minimum_growth")
        if not isinstance(growth, dict) or set(growth) != {"enabled", "fraction_of_initial"} or type(growth["enabled"]) is not bool:
            raise ValueError("completion.minimum_growth needs enabled and fraction_of_initial")
        fraction = growth["fraction_of_initial"]
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not math.isfinite(fraction) or fraction <= 0:
            raise ValueError("minimum_growth.fraction_of_initial must be finite and positive")
    return result


def area_completion_evidence(rows: list[dict], rules: dict) -> dict:
    """Read target area only from observations with confirmed sample presence.

    A zero initial area uses epsilon as denominator. Stage 2's stable window
    starts at the first observation meeting growth; earlier flatness cannot count.
    Missing/ineligible or repeated acquisition evidence resets the consecutive
    window; a timed latch disables early completion. Retrospective QC and
    uncertainties remain advisory under the current area-only protocol.
    """
    count = 0
    growth_seen = not rules.get("minimum_growth", {}).get("enabled", True)
    eligible = []
    seen_raw, last_stamp = set(), None
    for row in rows:
        valid = row.get("solvent_present", True) and row.get("endpoint_eligible") is not False
        raw_hash = row.get("raw_sha256")
        if raw_hash:
            if raw_hash in seen_raw:
                valid = False
            seen_raw.add(raw_hash)
        stamp = None
        if row.get("acquired_at") is not None:
            try:
                stamp = datetime.fromisoformat(row["acquired_at"])
                if last_stamp is not None and stamp <= last_stamp:
                    valid = False
            except (ValueError, TypeError):
                valid = False
        if valid and stamp is not None:
            last_stamp = stamp
        eligible.append(valid)
    trusted = [r for r, valid in zip(rows, eligible) if valid]
    initial = float(trusted[0]["peak_area"]) if trusted else 0.0
    denominator = max(abs(initial), rules["epsilon"])
    previous = None
    change = None
    growth_fraction = 0.0
    minimum = rules.get("min_completion_hours")
    post_threshold_points = 0
    time_active = False
    for row, valid in zip(rows, eligible):
        elapsed = row.get("stage_elapsed_hours")
        time_active = minimum is None or (
            isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
            and math.isfinite(elapsed) and elapsed >= minimum
        )
        if not valid:
            count, previous, change = 0, None, None
            continue
        area = float(row["peak_area"])
        if not math.isfinite(area) or area < 0:
            raise ValueError("The recorded area signal must be finite and nonnegative")
        # Keep the original reference/growth history, but begin a fresh
        # confirmation window using actual monotonic acquisition-start elapsed.
        if rules["trend"] == "increasing":
            growth_fraction = max(growth_fraction, (area - initial) / denominator)
        if not time_active:
            if rules["trend"] == "increasing":
                growth_seen = growth_seen or growth_fraction >= rules["minimum_growth"]["fraction_of_initial"]
            count, previous, change = 0, None, None
            continue
        post_threshold_points += 1
        if rules["trend"] == "decreasing":
            count = count + 1 if area / denominator <= rules["near_zero_fraction"] else 0
        else:
            was_growing = growth_seen
            growth_seen = growth_seen or growth_fraction >= rules["minimum_growth"]["fraction_of_initial"]
            change = abs(area - previous) / max(abs(previous), rules["epsilon"]) if previous is not None else None
            if not growth_seen:
                count = 0
            elif was_growing and change is not None and change <= rules["relative_change_threshold"]:
                count += 1
            else:
                count = 1
        previous = area
    fallback = any(r.get("stage_timed_fallback", False) for r in rows)
    complete = bool(rows) and not fallback and time_active and count >= rules["consecutive_iterations"]
    return {"complete": complete, "method": "area_only", "trend": rules["trend"], "points": len(rows),
            "criteria": dict(rules), "initial_area": initial if trusted else None, "current_area": previous,
            "normalization_area": denominator if trusted else None, "zero_initial_area": bool(trusted) and initial == 0,
            "confirmations_required": rules["consecutive_iterations"], "confirmations_passed": count,
            "consecutive_observations": count, "growth_seen": growth_seen if rules["trend"] == "increasing" else None,
            "growth_fraction": growth_fraction if rules["trend"] == "increasing" else None,
            "relative_change": change, "limit_reached": "max_iterations" in rules and len(rows) >= rules["max_iterations"] and not complete and not fallback,
            "min_completion_hours": minimum, "completion_time_active": time_active,
            "post_threshold_eligible_points": post_threshold_points,
            "timed_fallback": fallback, "trusted_points": len(trusted),
            "reason": "timed fallback; solvent/sample presence unconfirmed" if fallback else "area endpoint satisfied" if complete else "minimum completion time not reached or monotonic stage time unavailable" if minimum is not None and not time_active else "area endpoint not yet satisfied"}


def validate_completion(value: dict) -> dict:
    if isinstance(value, dict) and value.get("method") == "area_only":
        return validate_area_completion(value)
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
    if rules.get("method") == "area_only":
        return area_completion_evidence(rows, rules)
    result = {"complete": False, "trend": rules["trend"], "points": len(rows),
              "criteria": dict(rules), "reason": "insufficient evidence"}
    result.update(confirmations_required=rules["consecutive_confirmations"], confirmations_passed=0)
    if rows:
        result.update(initial_area=rows[0].get("peak_area"), current_area=rows[-1].get("peak_area"),
                      initial_peak_ppm=rows[0].get("peak_ppm"), current_peak_ppm=rows[-1].get("peak_ppm"),
                      shift_previous_ppm=rows[-1].get("shift_previous_ppm"),
                      shift_reference_ppm=rows[-1].get("shift_reference_ppm"))
    needed = max(rules["minimum_points"], rules["window_points"] + rules["consecutive_confirmations"] - 1)
    if len(rows) < needed:
        return result
    try:
        if any(r.get("error") or not r.get("measurement_valid", r.get("peak_clear", False)) for r in rows):
            raise ValueError("invalid measurement in stage history")
        areas = np.array([float(r["peak_area"]) for r in rows])
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
        failed = [name for name, ok in {
            "minimum_points": end >= rules["minimum_points"],
            "minimum_duration_hours": hours[end-1] >= rules["minimum_duration_hours"],
            "low_fraction": low, "near_observed_maximum": near_maximum,
            "minimum_progress_fraction": progress >= rules["minimum_progress_fraction"],
            "max_window_range_fraction": span <= rules["max_window_range_fraction"],
            "max_abs_slope_fraction_per_hour": abs(slope) <= rules["max_abs_slope_fraction_per_hour"],
        }.items() if not ok]
        checks.append({"failed_criteria": failed, "end_point": end, "elapsed_hours": float(hours[end - 1]),
                       "range_fraction": span, "slope_fraction_per_hour": slope,
                       "progress_fraction": progress, "low_region": low, "near_observed_maximum": near_maximum, "passed": bool(passed)})
    result.update(complete=all(c["passed"] for c in checks), confirmations_passed=sum(c["passed"] for c in checks), normalization_area=float(scale),
                  initial_area=float(areas[0]), checks=checks,
                  reason="sustained completion" if all(c["passed"] for c in checks) else "criteria not sustained")
    return result
