"""Arduino YAML loading, fingerprints, and fail-closed live prerequisites."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from chemyx_lab.config import read_mapping_config

from .errors import ConfigurationError, LiveExecutionBlocked

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_ROOT = REPO_ROOT / "runs" / "arduino"

SECTION_KEYS = {
    "arduino": {
        "port", "baud_rate", "expected_device", "expected_board", "expected_version",
        "ready_timeout_s", "read_timeout_s", "write_timeout_s", "command_timeout_s",
        "overall_timeout_s", "fingerprint",
    },
    "firmware": {"motion_enabled", "limits_enabled", "version"},
    "signal_interface": {
        "installed", "interface_type", "wiring_reviewed", "signal_inverted",
        "dm542_signal_voltage_v",
    },
    "motor": {
        "model", "rated_phase_current_a", "full_steps_per_revolution",
        "coil_pairs_identified", "mechanically_disconnected_for_test_02",
        "connected_to_axis_for_test_03",
    },
    "driver": {
        "model", "supply_voltage_v", "supply_current_a", "current_switch_setting",
        "microstep_setting", "microsteps_per_full_step", "enable_active_low",
    },
    "motion": {
        "lead_screw_lead_mm_per_revolution", "steps_per_mm", "home_backoff_steps",
        "safe_up_position_steps", "test_down_position_steps", "maximum_travel_steps",
        "maximum_speed_steps_s", "maximum_acceleration_steps_s2", "test_02_steps",
        "test_02_speed_steps_s", "home_speed_steps_s",
    },
    "limits": {
        "upper_installed", "lower_installed", "normally_closed", "upper_active_low",
        "lower_active_low", "upper_state_change_tested", "lower_state_change_tested",
    },
    "safety": {
        "fuse_installed", "emergency_disconnect_documented", "mechanical_hard_stops_installed",
        "vertical_axis_safe_when_disabled", "hard_runtime_limit_s",
        "operator_shaft_safe_confirmed", "operator_inspection_required",
        "operator_inspection_clearance",
    },
    "results": {"run_root_dir"},
    "integrated": {
        "machine_config_path", "experiment_config_path", "pump_action_cycle_index",
        "pump_return_cycle_index", "post_motion_settle_s", "post_pump_settle_s",
        "nmr_diagnostic", "expected_nmr_artifact_suffix",
        "test3_state_continuity_confirmed",
    },
}

DEFAULTS: dict[str, Any] = {
    "arduino": {
        "port": None,
        "baud_rate": 115200,
        "expected_device": "needle_controller",
        "expected_board": "uno_r4_minima",
        "expected_version": "0.1.0",
        "ready_timeout_s": 5.0,
        "read_timeout_s": 0.1,
        "write_timeout_s": 1.0,
        "command_timeout_s": 10.0,
        "overall_timeout_s": 60.0,
        "fingerprint": {},
    },
    "firmware": {"motion_enabled": False, "limits_enabled": False, "version": "0.1.0"},
    "signal_interface": {},
    "motor": {},
    "driver": {"model": "DM542T", "supply_voltage_v": 24},
    "motion": {},
    "limits": {"normally_closed": True},
    "safety": {
        "hard_runtime_limit_s": 120,
        "operator_inspection_required": False,
        "operator_inspection_clearance": None,
    },
    "results": {"run_root_dir": str(DEFAULT_RUN_ROOT)},
    "integrated": {},
}


def _merge(raw: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(DEFAULTS)
    for section, values in raw.items():
        if section not in SECTION_KEYS:
            raise ConfigurationError(f"Unknown Arduino config section {section!r}")
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise ConfigurationError(f"Arduino config section {section!r} must be a mapping")
        unknown = sorted(set(values) - SECTION_KEYS[section])
        if unknown:
            raise ConfigurationError(
                f"Unknown key(s) in Arduino config [{section}]: {', '.join(unknown)}"
            )
        merged[section].update(values)
    return merged


def load_arduino_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    raw = read_mapping_config(config_path, "Arduino config")
    merged = _merge(raw)
    validate_config_structure(merged)
    merged["_source_path"] = str(config_path.resolve())
    return merged


def validate_config_structure(cfg: dict[str, Any]) -> None:
    numeric_positive = (
        ("arduino", "baud_rate"), ("arduino", "ready_timeout_s"),
        ("arduino", "read_timeout_s"), ("arduino", "write_timeout_s"),
        ("arduino", "command_timeout_s"), ("arduino", "overall_timeout_s"),
        ("safety", "hard_runtime_limit_s"),
    )
    for section, key in numeric_positive:
        value = cfg[section].get(key)
        if isinstance(value, bool):
            raise ConfigurationError(f"{section}.{key} must be a positive number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{section}.{key} must be a positive number") from exc
        if not math.isfinite(number) or number <= 0:
            raise ConfigurationError(f"{section}.{key} must be a positive finite number")
    if float(cfg["safety"]["hard_runtime_limit_s"]) > 120:
        raise ConfigurationError("safety.hard_runtime_limit_s cannot exceed 120 seconds")
    if float(cfg["arduino"]["overall_timeout_s"]) > 120:
        raise ConfigurationError("arduino.overall_timeout_s cannot exceed 120 seconds")
    for key in ("expected_device", "expected_board"):
        if not str(cfg["arduino"].get(key) or "").strip():
            raise ConfigurationError(f"arduino.{key} is required")
    expected_version = str(cfg["arduino"].get("expected_version") or "").strip()
    firmware_version = str(cfg["firmware"].get("version") or "").strip()
    if not expected_version or expected_version != firmware_version:
        raise ConfigurationError(
            "arduino.expected_version and firmware.version must be identical non-empty strings"
        )
    fingerprint = cfg["arduino"].get("fingerprint")
    if fingerprint is not None and not isinstance(fingerprint, dict):
        raise ConfigurationError("arduino.fingerprint must be a mapping")


def hardware_fingerprint(cfg: dict[str, Any], test_name: str | None = None) -> str:
    """Hash stable hardware relevant to a stage.

    Temporary Test-2 state (motor mechanically disconnected) is deliberately
    recorded as an operator confirmation rather than part of the stable motor
    fingerprint. Installing limits or coupling the reviewed motor therefore
    does not invalidate the electrical Test-2 evidence.
    """
    arduino_identity = dict(cfg.get("arduino", {}))
    arduino_identity.pop("overall_timeout_s", None)
    arduino_identity.pop("command_timeout_s", None)
    stable_motor = dict(cfg.get("motor", {}))
    stable_motor.pop("mechanically_disconnected_for_test_02", None)
    test2_motor = dict(stable_motor)
    test2_motor.pop("connected_to_axis_for_test_03", None)
    stable_safety = dict(cfg.get("safety", {}))
    stable_safety.pop("operator_shaft_safe_confirmed", None)
    stable_safety.pop("operator_inspection_required", None)
    stable_limits = dict(cfg.get("limits", {}))
    stable_limits.pop("upper_state_change_tested", None)
    stable_limits.pop("lower_state_change_tested", None)
    firmware = cfg.get("firmware", {})
    identity_firmware = {"version": firmware.get("version")}
    test2_firmware = {
        "version": firmware.get("version"),
        "motion_enabled": firmware.get("motion_enabled"),
    }
    common = {"arduino": arduino_identity, "firmware": firmware}
    if test_name == "test_01_arduino_connection":
        # Test 1 proves identity/protocol only. Later commissioning flags must
        # not invalidate this deliberately motion-disabled evidence.
        payload = {"arduino": arduino_identity, "firmware": identity_firmware}
    elif test_name == "test_02_unloaded_motor":
        payload = {
            "arduino": arduino_identity,
            "firmware": test2_firmware,
            "signal_interface": cfg.get("signal_interface", {}),
            "motor": test2_motor,
            "driver": cfg.get("driver", {}),
            "safety": {
                key: stable_safety.get(key)
                for key in ("fuse_installed", "operator_inspection_clearance")
            },
        }
    elif test_name in {
        "test_03_limit_switch_preflight",
        "test_03_needle_axis",
        "test_04b_integrated_system",
    }:
        payload = {
            **common,
            "signal_interface": cfg.get("signal_interface", {}),
            "motor": stable_motor,
            "driver": cfg.get("driver", {}),
            "motion": cfg.get("motion", {}),
            "limits": stable_limits,
            "safety": stable_safety,
        }
        if test_name == "test_04b_integrated_system":
            payload["integrated"] = cfg.get("integrated", {})
    else:
        payload = {
            **common,
            "signal_interface": cfg.get("signal_interface", {}),
            "motor": stable_motor,
            "driver": cfg.get("driver", {}),
            "motion": cfg.get("motion", {}),
            "limits": stable_limits,
            "safety": stable_safety,
        }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _present(value: Any) -> bool:
    return value not in (None, "", False)


def _positive_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _positive_integer(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value > 0
    )


def _numeric_equals(value: Any, expected: float) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value)) and float(value) == float(expected)
    except (TypeError, ValueError):
        return False


def _nonnegative_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= 0


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def test1_missing(cfg: dict[str, Any]) -> list[str]:
    a = cfg["arduino"]
    missing = []
    if not a.get("port") and not any(_present(v) for v in (a.get("fingerprint") or {}).values()):
        missing.append("Explicit Arduino COM port or verified unique device fingerprint")
    return missing


def test2_missing(
    cfg: dict[str, Any], *, include_unloaded_conditions: bool = True
) -> list[str]:
    s, m, d, motion, safety, fw = (
        cfg["signal_interface"], cfg["motor"], cfg["driver"], cfg["motion"],
        cfg["safety"], cfg["firmware"],
    )
    checks = [
        (s.get("installed") is True, "Verified open-collector Arduino-to-DM542T interface"),
        (_present(s.get("interface_type")) and "direct" not in str(s.get("interface_type", "")).lower(), "Exact verified signal-interface type"),
        (s.get("wiring_reviewed") is True, "Signal-interface wiring review"),
        (_is_bool(s.get("signal_inverted")), "Explicit boolean signal inversion setting"),
        (_numeric_equals(s.get("dm542_signal_voltage_v"), 5.0), "DM542T 5 V control setting"),
        (fw.get("motion_enabled") is True, "Firmware motion commissioning flag after expert review"),
        (_present(m.get("model")), "Exact NEMA 17 model"),
        (_positive_value(m.get("rated_phase_current_a")), "Motor rated phase current"),
        (_positive_value(m.get("full_steps_per_revolution")), "Motor full steps per revolution"),
        (m.get("coil_pairs_identified") is True, "Identified motor coil pairs"),
        (_numeric_equals(d.get("supply_voltage_v"), 24.0), "Verified 24 V driver supply"),
        (_positive_value(d.get("supply_current_a")), "24 V power-supply current rating"),
        (_present(d.get("current_switch_setting")), "DM542T current switch setting"),
        (_present(d.get("microstep_setting")), "DM542T microstep switch setting"),
        (_positive_value(d.get("microsteps_per_full_step")), "Numeric microsteps per full step"),
        (str(d.get("model", "")).strip().upper() == "DM542T", "Verified DM542T driver model"),
        (_is_bool(d.get("enable_active_low")), "Explicit boolean DM542T enable polarity"),
        (_positive_integer(motion.get("test_02_steps")) and motion.get("test_02_steps") <= 200000, "Integer Test 2 step count at or below firmware cap"),
        (_positive_integer(motion.get("test_02_speed_steps_s")) and motion.get("test_02_speed_steps_s") <= 5000, "Integer Test 2 speed at or below firmware cap"),
        (safety.get("fuse_installed") is True, "Fuse installed"),
        (safety.get("emergency_disconnect_documented") is True, "Documented emergency driver-power disconnect"),
        (safety.get("operator_inspection_required") is not True, "Resolution of prior operator-inspection requirement"),
    ]
    if include_unloaded_conditions:
        checks.extend(
            [
                (m.get("mechanically_disconnected_for_test_02") is True, "Motor mechanically disconnected from needle axis"),
                (safety.get("operator_shaft_safe_confirmed") is True, "Operator confirmation that unloaded shaft can rotate safely"),
            ]
        )
    return [label for passed, label in checks if not passed]


def test3_limit_preflight_missing(cfg: dict[str, Any], *, test2_record_valid: bool) -> list[str]:
    limits = cfg["limits"]
    checks = [
        (test2_record_valid, "Matching successful live Test 2 result record"),
        (not test2_missing(cfg, include_unloaded_conditions=False), "All Test 2 electrical and motor prerequisites"),
        (limits.get("upper_installed") is True, "Upper normally closed limit switch installed"),
        (limits.get("lower_installed") is True, "Lower normally closed limit switch installed"),
        (limits.get("normally_closed") is True, "Normally closed limit-switch wiring"),
        (_is_bool(limits.get("upper_active_low")), "Explicit boolean upper limit polarity"),
        (_is_bool(limits.get("lower_active_low")), "Explicit boolean lower limit polarity"),
        (cfg["firmware"].get("limits_enabled") is True, "Firmware limit-switch commissioning flag"),
    ]
    return [label for passed, label in checks if not passed]


def test3_missing(
    cfg: dict[str, Any], *, test2_record_valid: bool, limit_record_valid: bool = False
) -> list[str]:
    limits, motion, safety = cfg["limits"], cfg["motion"], cfg["safety"]
    checks = [
        (test2_record_valid, "Matching successful live Test 2 result record"),
        (not test2_missing(cfg, include_unloaded_conditions=False), "All Test 2 electrical and motor prerequisites"),
        (cfg["motor"].get("connected_to_axis_for_test_03") is True, "Motor mechanically connected to the reviewed needle axis"),
        (limits.get("upper_installed") is True, "Upper normally closed limit switch installed"),
        (limits.get("lower_installed") is True, "Lower normally closed limit switch installed"),
        (limits.get("normally_closed") is True, "Normally closed limit-switch wiring"),
        (_is_bool(limits.get("upper_active_low")), "Explicit boolean upper limit polarity"),
        (_is_bool(limits.get("lower_active_low")), "Explicit boolean lower limit polarity"),
        (limit_record_valid, "Matching successful live Test 3 limit-switch preflight record"),
        (cfg["firmware"].get("limits_enabled") is True, "Firmware limit-switch commissioning flag"),
        (safety.get("mechanical_hard_stops_installed") is True, "Mechanical hard stops"),
        (_positive_value(motion.get("lead_screw_lead_mm_per_revolution")), "Lead-screw lead"),
        (_positive_value(motion.get("steps_per_mm")), "Calculated steps per millimeter"),
        (_positive_integer(motion.get("home_backoff_steps")), "Integer home backoff distance"),
        (_positive_integer(motion.get("safe_up_position_steps")), "Integer safe UP position"),
        (_positive_integer(motion.get("test_down_position_steps")), "Integer conservative DOWN test position"),
        (_positive_integer(motion.get("maximum_travel_steps")) and motion.get("maximum_travel_steps") <= 200000, "Integer maximum travel at or below firmware command cap"),
        (_positive_integer(motion.get("maximum_speed_steps_s")) and motion.get("maximum_speed_steps_s") <= 5000, "Integer maximum speed at or below firmware cap"),
        (_positive_integer(motion.get("maximum_acceleration_steps_s2")), "Integer maximum acceleration"),
        (_positive_integer(motion.get("home_speed_steps_s")) and motion.get("home_speed_steps_s") <= 5000, "Integer conservative homing speed at or below firmware cap"),
        (safety.get("emergency_disconnect_documented") is True, "Documented emergency driver-power disconnect"),
        (safety.get("vertical_axis_safe_when_disabled") is True, "Verified axis cannot fall dangerously without holding torque"),
        (safety.get("operator_inspection_required") is not True, "Resolution of prior operator-inspection requirement"),
    ]
    try:
        safe_up = int(motion.get("safe_up_position_steps"))
        test_down = int(motion.get("test_down_position_steps"))
        maximum = int(motion.get("maximum_travel_steps"))
        geometry_valid = 0 < safe_up < test_down < maximum
    except (TypeError, ValueError):
        geometry_valid = False
    checks.append(
        (geometry_valid, "Conservative geometry 0 < safe UP < test DOWN < maximum travel")
    )
    try:
        calculated_steps_per_mm = (
            float(cfg["motor"]["full_steps_per_revolution"])
            * float(cfg["driver"]["microsteps_per_full_step"])
            / float(motion["lead_screw_lead_mm_per_revolution"])
        )
        configured_steps_per_mm = float(motion["steps_per_mm"])
        steps_per_mm_valid = math.isclose(
            calculated_steps_per_mm,
            configured_steps_per_mm,
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        steps_per_mm_valid = False
    checks.append(
        (steps_per_mm_valid, "steps_per_mm matches full steps, microsteps, and lead-screw lead")
    )
    return [label for passed, label in checks if not passed]


def test4_full_missing(cfg: dict[str, Any], prerequisite_records: dict[str, bool]) -> list[str]:
    integrated = cfg["integrated"]
    checks = [
        (all(prerequisite_records.get(name, False) for name in ("test_01", "test_02", "test_03", "test_04a")), "Matching successful live Test 1, Test 2, Test 3, and Test 4A records"),
        (not test3_missing(cfg, test2_record_valid=prerequisite_records.get("test_02", False), limit_record_valid=prerequisite_records.get("test_03", False)), "All Test 3 axis prerequisites"),
        (_present(integrated.get("machine_config_path")), "Machine configuration path"),
        (_present(integrated.get("experiment_config_path")), "Validated experiment configuration path"),
        (_present(integrated.get("pump_action_cycle_index")), "Explicit approved pump diagnostic cycle item"),
        (_present(integrated.get("pump_return_cycle_index")), "Explicit approved pump return cycle item"),
        (_present(integrated.get("nmr_diagnostic")), "Explicit approved NMR diagnostic selection"),
        (_present(integrated.get("expected_nmr_artifact_suffix")), "Expected NMR output artifact suffix"),
        (_positive_value(integrated.get("post_motion_settle_s")), "Finite positive post-motion settling delay"),
        (_positive_value(integrated.get("post_pump_settle_s")), "Finite positive post-pump settling delay"),
        (integrated.get("test3_state_continuity_confirmed") is True, "Confirmed no reset, power loss, or manual axis motion since Test 3"),
    ]
    return [label for passed, label in checks if not passed]


def require_live(test_name: str, missing: list[str]) -> None:
    if missing:
        raise LiveExecutionBlocked(test_name, missing)
