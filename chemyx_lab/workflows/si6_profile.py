"""Validate the opt-in two-stage profile using existing workflow conventions."""
from __future__ import annotations

import math

from .dose_guard import validate_experiment_id
from . import pump_channels
from .. import config


def validate_profile(raw, stages):
    workflow = raw["workflow"]
    if raw["nmr"].get("auto_gain") is not False:
        raise ValueError("Completion monitoring requires fixed gain: nmr.auto_gain must be false")
    validate_experiment_id(workflow.get("experiment_id"))
    if len(stages) != 2 or not all(s.completion and s.plateau_stopping_enabled for s in stages):
        raise ValueError("Two-stage completion profile requires exactly two automatic completion stages")
    if [s.completion["trend"] for s in stages] != ["decreasing", "increasing"]:
        raise ValueError("Two-stage profile requires decreasing then increasing trends")
    if workflow.get("initial_needle_position", "UP") != "UP":
        raise ValueError("Sampling SOP requires initial_needle_position UP")
    if stages[0].before_monitoring or stages[1].before_monitoring or stages[1].after_monitoring:
        raise ValueError("Two-stage profile permits boundary actions only between its stages")
    for stage in stages:
        if stage.completion["minimum_duration_hours"] >= stage.max_hours:
            raise ValueError("Completion minimum duration must be below stage max_hours")
        # Every confirmation window must itself satisfy minimum_points. The
        # earliest qualifying end needs the minimum/window count; subsequent
        # confirmations each need one more observation.
        needed = max(stage.completion["minimum_points"], stage.completion["window_points"]) + stage.completion["consecutive_confirmations"] - 1
        if stage.max_measurements < needed:
            raise ValueError("Stage has too few measurement slots for sustained completion")
    doses = [e for e in stages[0].after_monitoring if str(e["action"]).lower() in {"withdraw", "infuse", "nmr"}]
    if len(doses) != 1 or doses[0]["action"] != "infuse" or pump_channels.operation_channel(raw, doses[0]) != 2:
        raise ValueError("Transition must contain exactly one channel-2 infusion and no other fluid/NMR operations")
    if "rate_ml_min" not in doses[0]:
        raise ValueError("Transition infusion requires explicit rate_ml_min")
    if 2 not in pump_channels.channel_definitions(raw):
        raise ValueError("Channel 2 syringe configuration is required")
    if any(config.resolve_units(settings["units"]) != 0 for settings in pump_channels.channel_definitions(raw).values()):
        raise ValueError("Two-stage rate_ml_min commands require mL/min units on every channel")
    qc = raw["analysis"].get("measurement_qc")
    fields = {"noise_multiplier", "max_noise_fraction", "max_area_uncertainty_fraction", "undetected_max_fraction"}
    if not isinstance(qc, dict) or set(qc) != fields:
        raise ValueError("analysis.measurement_qc needs noise and bounded-absence criteria")
    for name, number in qc.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"measurement_qc.{name} must be finite and positive")
    for name in fields - {"noise_multiplier"}:
        if qc[name] >= 1:
            raise ValueError(f"measurement_qc.{name} must be below one")
