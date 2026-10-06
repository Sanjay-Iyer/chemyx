"""Experiment-level Chemyx channel configuration and transfer validation.

This module is hardware-free. Volumes in recipes and bookkeeping are always
mL; the shared metered-move implementation converts to the device's units.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from .. import config


CHANNEL_FIELDS = {
    "syringe_diameter_mm", "syringe_capacity_ml", "initial_retained_volume_ml",
    "syringe_safety_margin_ml", "units", "rate_ml_min", "default_volume_ml",
}
PUMP_FIELDS = CHANNEL_FIELDS | {"channel", "default_channel", "channels"}
def channel_number(value: Any, *, legacy: bool = False) -> int:
    allowed = (0, 1, 2) if legacy else (1, 2)
    # YAML string keys are useful, but booleans and fractional numbers are not.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Chemyx channel must be 1 or 2 (legacy default may be 0)")
    if str(value) not in {str(ch) for ch in allowed}:
        raise ValueError("Chemyx channel must be 1 or 2 (legacy default may be 0)")
    return int(value)


def default_channel(raw: dict[str, Any]) -> int:
    pump = raw["pump"]
    if "channel" in pump and "default_channel" in pump and pump["channel"] != pump["default_channel"]:
        raise ValueError("pump.channel and pump.default_channel disagree")
    value = config._explicit_env_pump_overrides().get(
        "channel", pump.get("default_channel", pump.get("channel", 1))
    )
    return channel_number(value, legacy="channels" not in pump)


def positive(value: Any, label: str, *, zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if isinstance(value, bool) or not math.isfinite(number) or (number < 0 if zero else number <= 0):
        raise ValueError(f"{label} must be finite and {'nonnegative' if zero else 'positive'}")
    return number


def channel_definitions(raw: dict[str, Any]) -> dict[int, dict[str, Any]]:
    pump = raw["pump"]
    unknown = set(pump) - PUMP_FIELDS
    if unknown:
        raise ValueError(f"Unknown pump field(s): {', '.join(sorted(unknown))}")
    default = default_channel(raw)
    if "channels" not in pump:
        definitions = {default: {key: value for key, value in pump.items() if key in CHANNEL_FIELDS}}
    else:
        if not isinstance(pump["channels"], dict) or not pump["channels"]:
            raise ValueError("pump.channels must be a nonempty mapping of channel 1 and/or 2")
        if set(pump) & CHANNEL_FIELDS:
            raise ValueError("Put syringe settings inside pump.channels when using that mapping")
        definitions = {}
        for key, value in pump["channels"].items():
            ch = channel_number(key)
            if ch in definitions:
                raise ValueError(f"Duplicate Chemyx channel {ch}")
            if not isinstance(value, dict):
                raise ValueError(f"pump.channels.{ch} must be a mapping")
            unknown = set(value) - CHANNEL_FIELDS
            if unknown:
                raise ValueError(f"Unknown pump.channels.{ch} field(s): {', '.join(sorted(unknown))}")
            definitions[ch] = dict(value)
        if default not in definitions:
            raise ValueError(f"Default Chemyx channel {default} is not configured")
    for ch, settings in definitions.items():
        label = f"pump.channels.{ch}" if "channels" in pump else "pump"
        for key in ("syringe_diameter_mm", "syringe_capacity_ml", "units", "rate_ml_min"):
            if key not in settings:
                raise ValueError(f"{label}.{key} is required")
        settings.setdefault("initial_retained_volume_ml", 0.0)
        settings.setdefault("syringe_safety_margin_ml", 0.0)
        settings.setdefault("default_volume_ml", 5.0)
        for key in ("syringe_diameter_mm", "syringe_capacity_ml", "rate_ml_min", "default_volume_ml"):
            positive(settings[key], f"{label}.{key}")
        for key in ("initial_retained_volume_ml", "syringe_safety_margin_ml"):
            positive(settings[key], f"{label}.{key}", zero=True)
        diameter = float(settings["syringe_diameter_mm"])
        if not config.DIAMETER_MIN <= diameter <= config.DIAMETER_MAX:
            raise ValueError(f"{label}.syringe_diameter_mm is outside the Chemyx limits")
        units = config.resolve_units(settings["units"])
        validate_rate(settings["rate_ml_min"], units, label)
        check_volume(ch, float(settings["initial_retained_volume_ml"]), settings)
    return definitions


def validate_rate(value: Any, units: int, label: str) -> float:
    rate = positive(value, f"{label}.rate_ml_min")
    lo, hi = config.rate_limits(units)
    if not lo <= rate <= hi:
        raise ValueError(f"{label}.rate_ml_min is outside the Chemyx limits for {config.UNITS[units]}")
    return rate


def instrument_configs(raw: dict[str, Any], default: config.PumpConfig) -> dict[int, config.PumpConfig]:
    definitions = channel_definitions(raw)
    if default.channel != default_channel(raw):
        raise ValueError("Effective pump default channel disagrees with the experiment")
    result = {}
    for ch, settings in definitions.items():
        cfg = default if ch == default.channel else replace(
            default, channel=ch, diameter=float(settings["syringe_diameter_mm"]),
            units=config.resolve_units(settings["units"]), rate=float(settings["rate_ml_min"]),
            volume=float(settings["default_volume_ml"]),
        )
        if not math.isfinite(cfg.diameter) or not config.DIAMETER_MIN <= cfg.diameter <= config.DIAMETER_MAX:
            raise ValueError(f"Effective channel {ch} syringe diameter is outside the Chemyx limits")
        validate_rate(cfg.rate, cfg.units, f"effective channel {ch}")
        result[ch] = cfg
    return result


def operation_channel(raw: dict[str, Any], event: dict[str, Any]) -> int:
    ch = channel_number(event["channel"]) if "channel" in event else default_channel(raw)
    if ch not in channel_definitions(raw):
        raise ValueError(f"Chemyx channel {ch} requested but not configured")
    return ch


def validate_operation(raw: dict[str, Any], event: dict[str, Any], label: str) -> None:
    if str(event.get("action", "")).lower() in {"withdraw", "infuse"}:
        ch = operation_channel(raw, event)
        settings = channel_definitions(raw)[ch]
        positive(event.get("volume_ml"), f"{label}.volume_ml")
        if "rate_ml_min" in event:
            validate_rate(event["rate_ml_min"], config.resolve_units(settings["units"]), label)
    elif "channel" in event or "rate_ml_min" in event:
        raise ValueError(f"{label}: channel/rate_ml_min applies only to withdraw or infuse")


def validate_stage_actions(raw: dict[str, Any], events: Any, label: str) -> None:
    if not isinstance(events, list):
        raise ValueError(f"{label} must be a list")
    position = "UP"
    for index, event in enumerate(events, 1):
        item = f"{label}[{index}]"
        if not isinstance(event, dict):
            raise ValueError(f"{item} must be a mapping")
        action = str(event.get("action", "")).lower()
        fields = {
            "withdraw": {"channel", "volume_ml", "rate_ml_min", "needle_position"},
            "infuse": {"channel", "volume_ml", "rate_ml_min", "needle_position"},
            "pause": {"seconds"}, "needle": {"position"}, "nmr": {"needle_position"},
        }
        if action not in fields:
            raise ValueError(f"{item}: expected withdraw, infuse, pause, needle, or nmr")
        unknown = set(event) - fields[action] - {"action", "note"}
        if unknown:
            raise ValueError(f"Unknown {item} field(s): {', '.join(sorted(unknown))}")
        validate_operation(raw, event, item)
        if action == "pause":
            positive(event.get("seconds"), f"{item}.seconds", zero=True)
        if action in {"withdraw", "infuse", "nmr", "needle"}:
            key = "position" if action == "needle" else "needle_position"
            if event.get(key) not in {"UP", "DOWN"}:
                raise ValueError(f"{item}.{key} must explicitly be UP or DOWN")
            if action == "needle":
                position = event[key]
            elif event[key] != position:
                raise ValueError(f"{item}: needle_position does not match preceding needle actions ({position})")
    if position != "UP":
        raise ValueError(f"{label} must finish with the needle UP before monitoring or the next stage")


def check_volume(ch: int, retained: float, settings: dict[str, Any]) -> None:
    capacity = float(settings["syringe_capacity_ml"])
    margin = float(settings["syringe_safety_margin_ml"])
    if retained < -1e-9:
        raise ValueError(f"Chemyx channel {ch} infuses more volume than the syringe can contain")
    if retained + margin > capacity + 1e-9:
        raise ValueError(f"Unsafe syringe capacity on Chemyx channel {ch}: {retained:g} mL retained + {margin:g} mL margin exceeds {capacity:g} mL capacity")


def validate_channel_capacity(raw: dict[str, Any], stages: list[Any]) -> dict[int, dict[str, float]]:
    """Check every prefix, each channel's cycle balance, and one-time actions.

    Balanced cycles are checked at each stage's starting volume. This covers
    any number of measurements, including operator-authorized extensions.
    """
    definitions = channel_definitions(raw)
    retained = {ch: float(s["initial_retained_volume_ml"]) for ch, s in definitions.items()}
    maximum = dict(retained)

    def apply(events: Any) -> None:
        for event in events:
            action = str(event["action"]).lower()
            if action not in {"withdraw", "infuse"}:
                continue
            ch = operation_channel(raw, event)
            volume = float(event["volume_ml"])
            retained[ch] += volume if action == "withdraw" else -volume
            check_volume(ch, retained[ch], definitions[ch])
            maximum[ch] = max(maximum[ch], retained[ch])

    for stage in stages:
        apply(stage.before_monitoring)
        before = dict(retained)
        apply(raw["workflow"]["cycle"])
        for ch in definitions:
            if not math.isclose(before[ch], retained[ch], rel_tol=0, abs_tol=1e-9):
                raise ValueError(f"The repeated Si6 cycle must return channel {ch} to its initial retained volume")
        apply(stage.after_monitoring)
    return {
        ch: {"syringe_capacity_ml": float(s["syringe_capacity_ml"]),
             "initial_retained_volume_ml": float(s["initial_retained_volume_ml"]),
             "safety_margin_ml": float(s["syringe_safety_margin_ml"]),
             "maximum_retained_volume_ml": maximum[ch], "end_retained_volume_ml": retained[ch]}
        for ch, s in definitions.items()
    }


def uses_channel_extension(raw: dict[str, Any]) -> bool:
    workflow = raw["workflow"]
    return (
        "channels" in raw["pump"] or "default_channel" in raw["pump"]
        or any("channel" in e or "rate_ml_min" in e for e in workflow["cycle"])
        or any(s and ("before_monitoring" in s or "after_monitoring" in s)
               for s in [workflow["initial_stage"], workflow.get("first_addition_stage"), *workflow.get("repeating_stages", [])])
    )
