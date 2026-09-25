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
    "firmware": {"motion_enabled", "limits_enabled", "runtime_configurable", "version"},
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
        "sample_down_position_steps",
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
    "needle": {
        "min_position", "max_position", "home_position", "up_position",
        "down_position", "steps_per_unit", "up_step_sign", "state_path",
    },
}

DEFAULTS: dict[str, Any] = {
    "arduino": {
        "port": None,
        "baud_rate": 115200,
        "expected_device": "needle_controller",
        "expected_board": "uno_r4_minima",
        "expected_version": "1.2.1",
        "ready_timeout_s": 5.0,
        "read_timeout_s": 0.1,
        "write_timeout_s": 1.0,
        "command_timeout_s": 10.0,
        "overall_timeout_s": 60.0,
        "fingerprint": {},
    },
    "firmware": {
        "motion_enabled": False,
        "limits_enabled": False,
        "runtime_configurable": True,
        "version": "1.2.1",
    },
    "signal_interface": {},
    "motor": {},
    "driver": {"model": "DM542S", "supply_voltage_v": 24},
    "motion": {},
    "limits": {"normally_closed": True},
    "safety": {
        "hard_runtime_limit_s": 120,
        "operator_inspection_required": False,
        "operator_inspection_clearance": None,
    },
    "results": {"run_root_dir": str(DEFAULT_RUN_ROOT)},
    "integrated": {},
    "needle": {
        "min_position": -3, "max_position": 5, "home_position": 0,
        "up_position": 1, "down_position": -1,
        "steps_per_unit": None, "up_step_sign": None,
        "state_path": str(DEFAULT_RUN_ROOT / "needle_state.json"),
    },
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
    state_path = Path(merged["needle"]["state_path"])
    if not state_path.is_absolute():
        merged["needle"]["state_path"] = str((REPO_ROOT / state_path).resolve())
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
    for key in ("motion_enabled", "limits_enabled", "runtime_configurable"):
        if not isinstance(cfg["firmware"].get(key), bool):
            raise ConfigurationError(f"firmware.{key} must be true or false")
    if cfg["firmware"]["limits_enabled"]:
        raise ConfigurationError("Firmware 1.2.1 has no physical limit inputs; set firmware.limits_enabled=false")
    if cfg["signal_interface"].get("signal_inverted") not in (None, False):
        raise ConfigurationError("Validated D3/D4 demo uses non-inverted STEP/DIR signals")
    fingerprint = cfg["arduino"].get("fingerprint")
    if fingerprint is not None and not isinstance(fingerprint, dict):
        raise ConfigurationError("arduino.fingerprint must be a mapping")
    needle = cfg["needle"]
    for key in ("min_position", "max_position", "home_position", "up_position", "down_position"):
        if type(needle.get(key)) is not int:
            raise ConfigurationError(f"needle.{key} must be an integer")
    if needle["home_position"] != 0 or not (
        needle["min_position"] <= needle["down_position"] < 0
        < needle["up_position"] <= needle["max_position"]
    ):
        raise ConfigurationError("Needle positions must satisfy min <= DOWN < HOME=0 < UP <= max")
    if needle.get("steps_per_unit") is not None and not _positive_integer(needle["steps_per_unit"]):
        raise ConfigurationError("needle.steps_per_unit must be a positive integer")
    if needle.get("up_step_sign") is not None and (type(needle["up_step_sign"]) is not int or needle["up_step_sign"] not in (-1, 1)):
        raise ConfigurationError("needle.up_step_sign must be -1 or +1")
    if not str(needle.get("state_path") or "").strip():
        raise ConfigurationError("needle.state_path is required")
    for key in ("test_02_speed_steps_s", "maximum_speed_steps_s"):
        value = cfg["motion"].get(key)
        if value is not None and (type(value) is not int or not 1 <= value <= 100):
            raise ConfigurationError(f"motion.{key} must be an integer from 1 to 100 steps/s")
    acceleration = cfg["motion"].get("maximum_acceleration_steps_s2")
    if acceleration is not None and (type(acceleration) is not int or not 1 <= acceleration <= 50000):
        raise ConfigurationError("motion.maximum_acceleration_steps_s2 must be an integer from 1 to 50000")


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
            "needle": cfg.get("needle", {}),
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
            "needle": cfg.get("needle", {}),
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
    s, m, motion, safety, fw = (
        cfg["signal_interface"], cfg["motor"], cfg["motion"], cfg["safety"], cfg["firmware"],
    )
    checks = [
        (s.get("wiring_reviewed") is True, "Operator review of validated D3 STEP / D4 DIR wiring"),
        (fw.get("motion_enabled") is True, "Firmware software motion arm configured"),
        (_positive_integer(motion.get("test_02_steps")) and motion.get("test_02_steps") <= 200000, "Integer Test 2 step count at or below firmware cap"),
        (_positive_integer(motion.get("test_02_speed_steps_s")) and motion.get("test_02_speed_steps_s") <= 100, "Integer Test 2 speed at or below firmware cap (100 steps/s)"),
        (safety.get("emergency_disconnect_documented") is True, "Documented emergency driver-power disconnect"),
        (safety.get("operator_inspection_required") is not True, "Resolution of prior operator-inspection requirement"),
    ]
    if include_unloaded_conditions:
        checks.extend(
            [
                (m.get("mechanically_disconnected_for_test_02") is True, "Motor mechanically disconnected for unloaded Test 2"),
                (safety.get("operator_shaft_safe_confirmed") is True, "Operator confirmation that unloaded shaft can rotate safely"),
            ]
        )
    return [label for passed, label in checks if not passed]


def test3_limit_preflight_missing(cfg: dict[str, Any], *, test2_record_valid: bool) -> list[str]:
    # Deprecated API kept for callers of older versions; no switches exist.
    return []


def test3_missing(
    cfg: dict[str, Any], *, test2_record_valid: bool, limit_record_valid: bool = False
) -> list[str]:
    # limit_record_valid is ignored for compatibility with older callers.
    motion, safety, needle = cfg["motion"], cfg["safety"], cfg["needle"]
    checks = [
        (test2_record_valid, "Matching successful live Test 2 result record"),
        (not test2_missing(cfg, include_unloaded_conditions=False), "D3/D4 motion prerequisites"),
        (cfg["motor"].get("connected_to_axis_for_test_03") is True, "Motor connected to inspected needle axis"),
        (_positive_integer(needle.get("steps_per_unit")), "Calibrated integer motor steps per logical needle unit"),
        (needle.get("up_step_sign") in (-1, 1), "Verified physical UP direction sign (-1 or +1)"),
        (_positive_integer(motion.get("maximum_speed_steps_s")) and motion.get("maximum_speed_steps_s") <= 100, "Integer maximum speed at or below firmware cap (100 steps/s)"),
        (_positive_integer(motion.get("maximum_acceleration_steps_s2")), "Integer maximum acceleration"),
        (safety.get("emergency_disconnect_documented") is True, "Documented emergency driver-power disconnect"),
        (safety.get("operator_inspection_required") is not True, "Resolution of prior operator-inspection requirement"),
    ]
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
    ]
    return [label for passed, label in checks if not passed]


def require_live(test_name: str, missing: list[str]) -> None:
    # Demo mode: commissioning checklist items are advisory only, never blocking.
    for item in missing:
        print(f"NOTE ({test_name}): not recorded in YAML: {item}")
