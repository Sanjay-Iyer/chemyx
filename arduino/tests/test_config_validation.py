from copy import deepcopy
from pathlib import Path

import pytest

from arduino.python.config import (
    hardware_fingerprint,
    load_arduino_config,
    test2_missing as missing_test2,
    test3_missing as missing_test3,
    validate_config_structure,
)
from arduino.python.errors import ConfigurationError


def test_example_config_accepts_placeholders(base_config):
    validate_config_structure(base_config)
    assert base_config["arduino"]["baud_rate"] == 115200


def test_test2_blocks_without_signal_interface(base_config):
    assert "Verified open-collector Arduino-to-stepper-driver interface" in missing_test2(base_config)


def test_test2_blocks_without_motor_current(commissioned_config):
    commissioned_config["motor"]["rated_phase_current_a"] = None
    assert "Motor rated phase current" in missing_test2(commissioned_config)


def test_test3_blocks_without_both_limits(commissioned_config):
    commissioned_config["limits"]["upper_installed"] = False
    commissioned_config["limits"]["lower_installed"] = False
    missing = missing_test3(commissioned_config, test2_record_valid=True)
    assert "Upper normally closed limit switch installed" in missing
    assert "Lower normally closed limit switch installed" in missing


def test_hardware_fingerprint_changes_with_wiring(commissioned_config):
    original = hardware_fingerprint(commissioned_config)
    changed = deepcopy(commissioned_config)
    changed["signal_interface"]["interface_type"] = "different reviewed interface"
    assert hardware_fingerprint(changed) != original


def test_test2_fingerprint_survives_axis_coupling_and_limit_install(commissioned_config):
    original = hardware_fingerprint(commissioned_config, "test_02_unloaded_motor")
    changed = deepcopy(commissioned_config)
    changed["motor"]["mechanically_disconnected_for_test_02"] = False
    changed["limits"]["upper_installed"] = not changed["limits"]["upper_installed"]
    assert hardware_fingerprint(changed, "test_02_unloaded_motor") == original
    assert hardware_fingerprint(changed, "test_03_needle_axis") != hardware_fingerprint(
        commissioned_config, "test_03_needle_axis"
    )


def test_test1_fingerprint_survives_later_firmware_commissioning(base_config):
    original = hardware_fingerprint(base_config, "test_01_arduino_connection")
    base_config["firmware"]["motion_enabled"] = True
    base_config["firmware"]["limits_enabled"] = True
    assert hardware_fingerprint(base_config, "test_01_arduino_connection") == original


def test_runtime_ceiling_cannot_exceed_120_seconds(base_config):
    base_config["safety"]["hard_runtime_limit_s"] = 121
    with pytest.raises(ConfigurationError):
        validate_config_structure(base_config)


def test_commercial_example_selects_runtime_firmware():
    path = Path(__file__).resolve().parents[1] / "configs" / "commercial_arduino.example.yaml"
    cfg = load_arduino_config(path)
    assert cfg["arduino"]["expected_device"] == "commercial_needle_controller"
    assert cfg["arduino"]["expected_version"] == "1.0.0"
    assert cfg["firmware"]["runtime_configurable"] is True
    assert cfg["firmware"]["motion_enabled"] is False
    assert cfg["firmware"]["limits_enabled"] is False
    assert cfg["driver"]["model"] == "DM542S"
