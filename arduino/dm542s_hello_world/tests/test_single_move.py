"""Validation and planning rules for the one-way scripts 04 and 05.

No COM port is opened anywhere in this file. Everything under test happens
before the serial connection exists, which is exactly where these scripts do
their safety work.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from calibration_utils import CalibrationError
from motion_utils import BACKWARD, FIRMWARE_MAX_ABSOLUTE_STEPS, FORWARD, MotionConfigError
from single_move_utils import (
    load_single_move_config,
    plan_single_move,
)


BASE_CONFIG: dict[str, Any] = {
    "serial": {"port": "COM_TEST", "baud": 115200, "reset_wait_seconds": 2.0},
    "driver": {"steps_per_revolution": 800},
    "movement": {
        "movement_mode": "degrees",
        "distance": 90.0,
        "require_typed_confirmation": True,
        "maximum_absolute_steps_per_move": 5000,
    },
    "software_limits": {
        "enabled": True,
        "minimum_steps": -1000,
        "maximum_steps": 1000,
    },
}


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    """Write a single-move config, deep-merging the given section overrides."""
    config = copy.deepcopy(BASE_CONFIG)
    for section, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(section), dict):
            config[section].update(value)
        else:
            config[section] = value
    path = tmp_path / "single_move.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def write_calibration(tmp_path: Path, **overrides: Any) -> Path:
    document = {
        "calibration": {
            "calibrated": True,
            "created_at": "2026-08-06T00:00:00Z",
            "steps_per_revolution": 800,
            "mm_per_degree": 0.0125,
            "mm_per_step": 0.005625,
            "steps_per_mm": 177.777778,
            "fit_method": "through_origin",
            "source_results_file": "calibration_results/example.yaml",
            "number_of_measurements": 3,
        }
    }
    document["calibration"].update(overrides)
    path = tmp_path / "needle_calibration.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def plan_forward(config, **kwargs):
    return plan_single_move(
        config, direction=FORWARD, move_name="needle_up", **kwargs
    )


def plan_backward(config, **kwargs):
    return plan_single_move(
        config, direction=BACKWARD, move_name="needle_down", **kwargs
    )


# ---------------------------------------------------------------------------
# The shipped configuration files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, planner, expected_sign",
    [
        ("04_needle_up.yaml", plan_forward, +1),
        ("05_needle_down.yaml", plan_backward, -1),
    ],
)
def test_shipped_configs_load_and_plan(
    package_root: Path, filename, planner, expected_sign
):
    config = load_single_move_config(package_root / "configs" / filename)
    _, move = planner(config)
    # Ships uncalibrated, so the shipped unit must not require calibration.
    assert config.movement_mode == "degrees"
    assert move.signed_steps == expected_sign * 200  # 90 deg at 800 steps/rev


def test_every_script_has_a_matching_config(package_root: Path):
    """The package convention: configs/<script name>.yaml, one per script."""
    for script in ("04_needle_up", "05_needle_down"):
        assert (package_root / f"{script}.py").is_file()
        assert (package_root / "configs" / f"{script}.yaml").is_file()


# ---------------------------------------------------------------------------
# Direction belongs to the script, never to the file
# ---------------------------------------------------------------------------


def test_direction_comes_from_the_script_not_the_config(tmp_path: Path):
    config = load_single_move_config(write_config(tmp_path))
    _, forward = plan_forward(config)
    _, backward = plan_backward(config)
    assert forward.signed_steps == +200
    assert backward.signed_steps == -200
    assert forward.direction == FORWARD
    assert backward.direction == BACKWARD


def test_direction_key_in_config_is_rejected_with_an_explanation(tmp_path: Path):
    path = write_config(tmp_path, movement={"direction": "backward"})
    with pytest.raises(MotionConfigError) as error:
        load_single_move_config(path)
    assert "direction" in str(error.value)
    assert "which script you run" in str(error.value)


def test_negative_distance_is_rejected_not_silently_reversed(tmp_path: Path):
    path = write_config(tmp_path, movement={"distance": -90.0})
    with pytest.raises(MotionConfigError, match="greater than zero"):
        load_single_move_config(path)


def test_zero_distance_is_rejected(tmp_path: Path):
    path = write_config(tmp_path, movement={"distance": 0.0})
    with pytest.raises(MotionConfigError, match="greater than zero"):
        load_single_move_config(path)


# ---------------------------------------------------------------------------
# distance is the knob: modes and conversion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "distance, expected_steps",
    [(90.0, 200), (180.0, 400), (360.0, 800), (45.0, 100)],
)
def test_degrees_distance_sets_the_step_count(
    tmp_path: Path, distance, expected_steps
):
    config = load_single_move_config(
        write_config(tmp_path, movement={"distance": distance})
    )
    _, move = plan_forward(config)
    assert move.signed_steps == expected_steps


def test_steps_mode_uses_the_distance_directly(tmp_path: Path):
    config = load_single_move_config(
        write_config(
            tmp_path, movement={"movement_mode": "steps", "distance": 137}
        )
    )
    _, move = plan_forward(config)
    assert move.signed_steps == 137


def test_steps_mode_rejects_a_fractional_distance(tmp_path: Path):
    path = write_config(
        tmp_path, movement={"movement_mode": "steps", "distance": 137.5}
    )
    with pytest.raises(MotionConfigError, match="whole number"):
        load_single_move_config(path)


def test_mm_mode_converts_with_the_calibration(tmp_path: Path):
    config = load_single_move_config(
        write_config(tmp_path, movement={"movement_mode": "mm", "distance": 1.0})
    )
    _, move = plan_forward(config, mm_per_step=0.005625)
    assert move.signed_steps == 178  # 1.0 / 0.005625 = 177.78, rounded


def test_mm_mode_without_a_calibration_refuses_to_plan(tmp_path: Path):
    config = load_single_move_config(
        write_config(tmp_path, movement={"movement_mode": "mm", "distance": 1.0})
    )
    with pytest.raises(MotionConfigError):
        plan_forward(config)


def test_mm_mode_rejects_an_uncalibrated_authoritative_file(tmp_path: Path):
    from calibration_utils import load_calibration

    path = write_calibration(tmp_path, calibrated=False, mm_per_step=None)
    with pytest.raises(CalibrationError):
        load_calibration(path).require_calibrated()


def test_unknown_mode_is_rejected(tmp_path: Path):
    path = write_config(tmp_path, movement={"movement_mode": "inches"})
    with pytest.raises(MotionConfigError, match="movement_mode must be one of"):
        load_single_move_config(path)


def test_a_distance_that_rounds_to_zero_steps_is_rejected(tmp_path: Path):
    config = load_single_move_config(
        write_config(tmp_path, movement={"distance": 0.1})
    )
    # 0.1 deg at 800 steps/rev is 0.22 steps, which rounds to 0.
    with pytest.raises(MotionConfigError, match="rounds to 0 steps"):
        plan_forward(config)


# ---------------------------------------------------------------------------
# Ceilings and bounds
# ---------------------------------------------------------------------------


def test_distance_above_the_per_move_ceiling_is_rejected(tmp_path: Path):
    config = load_single_move_config(
        write_config(
            tmp_path,
            movement={"distance": 900.0, "maximum_absolute_steps_per_move": 400},
        )
    )
    with pytest.raises(MotionConfigError, match="maximum_absolute_steps_per_move"):
        plan_forward(config)


def test_per_move_ceiling_above_the_firmware_limit_is_rejected(tmp_path: Path):
    path = write_config(
        tmp_path,
        movement={"maximum_absolute_steps_per_move": FIRMWARE_MAX_ABSOLUTE_STEPS + 1},
    )
    with pytest.raises(MotionConfigError, match="exceeds the firmware limit"):
        load_single_move_config(path)


def test_forward_move_beyond_the_upper_software_bound_is_rejected(tmp_path: Path):
    from motion_utils import validate_plan_within_limits

    config = load_single_move_config(
        write_config(
            tmp_path,
            movement={"distance": 720.0},  # 1600 steps
            software_limits={"maximum_steps": 1000},
        )
    )
    _, move = plan_forward(config)
    with pytest.raises(MotionConfigError):
        validate_plan_within_limits([move], config.software_limits)


def test_backward_move_beyond_the_lower_software_bound_is_rejected(tmp_path: Path):
    from motion_utils import validate_plan_within_limits

    config = load_single_move_config(
        write_config(
            tmp_path,
            movement={"distance": 720.0},  # -1600 steps
            software_limits={"minimum_steps": -1000},
        )
    )
    _, move = plan_backward(config)
    with pytest.raises(MotionConfigError):
        validate_plan_within_limits([move], config.software_limits)


def test_a_timeout_ceiling_below_the_move_is_rejected(tmp_path: Path):
    from motion_utils import validate_timeouts_cover_plan

    config = load_single_move_config(
        write_config(
            tmp_path,
            movement={"distance": 360.0},  # 800 steps -> 8 s of motion
            timing={"minimum_timeout_seconds": 1.0, "maximum_timeout_seconds": 2.0},
        )
    )
    _, move = plan_forward(config)
    with pytest.raises(MotionConfigError, match="below what move"):
        validate_timeouts_cover_plan([move], config.timing)


# ---------------------------------------------------------------------------
# Strict schema: nothing is silently ignored
# ---------------------------------------------------------------------------


def test_misspelled_key_suggests_the_right_one(tmp_path: Path):
    path = write_config(tmp_path, movement={"distanse": 90.0})
    with pytest.raises(MotionConfigError) as error:
        load_single_move_config(path)
    assert "did you mean 'distance'" in str(error.value)


@pytest.mark.parametrize(
    "key, value, expected",
    [
        ("mm", 5.0, "movement_mode: mm"),
        ("degrees", 90.0, "movement_mode: degrees"),
        ("steps", 200, "movement_mode: steps"),
        ("value", 90.0, "spells the distance"),
        ("require_zero_net_steps", True, "never returns to its starting point"),
    ],
)
def test_keys_copied_from_script_01_are_rejected_with_guidance(
    tmp_path: Path, key, value, expected
):
    path = write_config(tmp_path, movement={key: value})
    with pytest.raises(MotionConfigError) as error:
        load_single_move_config(path)
    assert expected in str(error.value)


@pytest.mark.parametrize(
    "section, expected",
    [
        ("moves", "exactly one move"),
        ("execution", "uses movement:"),
    ],
)
def test_top_level_sections_from_script_01_are_rejected(
    tmp_path: Path, section, expected
):
    config = copy.deepcopy(BASE_CONFIG)
    config[section] = [] if section == "moves" else {}
    path = tmp_path / "single_move.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MotionConfigError) as error:
        load_single_move_config(path)
    assert expected in str(error.value)


@pytest.mark.parametrize(
    "missing", ["movement_mode", "distance", "require_typed_confirmation"]
)
def test_required_movement_values_are_required(tmp_path: Path, missing: str):
    config = copy.deepcopy(BASE_CONFIG)
    del config["movement"][missing]
    path = tmp_path / "single_move.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MotionConfigError, match=missing):
        load_single_move_config(path)


def test_missing_movement_section_is_rejected(tmp_path: Path):
    config = copy.deepcopy(BASE_CONFIG)
    del config["movement"]
    path = tmp_path / "single_move.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MotionConfigError, match="movement"):
        load_single_move_config(path)


# ---------------------------------------------------------------------------
# The scripts themselves
# ---------------------------------------------------------------------------


def test_scripts_pin_their_own_direction(needle_up_script, needle_down_script):
    """Importing the scripts must not need hardware, and must fix direction."""
    assert needle_up_script.DEFAULT_CONFIG.name == "04_needle_up.yaml"
    assert needle_down_script.DEFAULT_CONFIG.name == "05_needle_down.yaml"
    assert callable(needle_up_script.main)
    assert callable(needle_down_script.main)


def test_a_missing_config_file_is_reported_not_traced(tmp_path: Path):
    with pytest.raises(MotionConfigError, match="not found"):
        load_single_move_config(tmp_path / "nope.yaml")
