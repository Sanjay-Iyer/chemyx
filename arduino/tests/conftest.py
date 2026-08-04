from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from arduino.python.config import load_arduino_config


@pytest.fixture
def base_config() -> dict:
    path = Path(__file__).resolve().parents[1] / "configs" / "arduino.example.yaml"
    return load_arduino_config(path)


@pytest.fixture
def commissioned_config(base_config: dict, tmp_path: Path) -> dict:
    cfg = deepcopy(base_config)
    cfg["arduino"]["port"] = "COM_A"
    cfg["firmware"]["motion_enabled"] = True
    cfg["firmware"]["limits_enabled"] = True
    cfg["signal_interface"].update(
        {
            "installed": True,
            "interface_type": "verified discrete NPN open collector",
            "wiring_reviewed": True,
            "signal_inverted": True,
            "dm542_signal_voltage_v": 5,
        }
    )
    cfg["motor"].update(
        {
            "model": "TEST-MOTOR-DATASHEET-VERIFIED",
            "rated_phase_current_a": 1.2,
            "full_steps_per_revolution": 200,
            "coil_pairs_identified": True,
            "mechanically_disconnected_for_test_02": True,
            "connected_to_axis_for_test_03": True,
        }
    )
    cfg["driver"].update(
        {
            "supply_current_a": 3.0,
            "current_switch_setting": "reviewed-test-setting",
            "microstep_setting": "reviewed-test-setting",
            "microsteps_per_full_step": 4,
            "enable_active_low": True,
        }
    )
    cfg["motion"].update(
        {
            "lead_screw_lead_mm_per_revolution": 8.0,
            "steps_per_mm": 100.0,
            "home_backoff_steps": 100,
            "safe_up_position_steps": 100,
            "test_down_position_steps": 500,
            "maximum_travel_steps": 1000,
            "maximum_speed_steps_s": 300,
            "maximum_acceleration_steps_s2": 300,
            "test_02_steps": 200,
            "test_02_speed_steps_s": 300,
            "home_speed_steps_s": 100,
        }
    )
    cfg["limits"].update(
        {
            "upper_installed": True,
            "lower_installed": True,
            "upper_active_low": False,
            "lower_active_low": False,
            "upper_state_change_tested": True,
            "lower_state_change_tested": True,
        }
    )
    cfg["safety"].update(
        {
            "fuse_installed": True,
            "emergency_disconnect_documented": True,
            "mechanical_hard_stops_installed": True,
            "vertical_axis_safe_when_disabled": True,
            "operator_shaft_safe_confirmed": True,
        }
    )
    cfg["results"]["run_root_dir"] = str(tmp_path)
    return cfg
