"""YAML validation for 01_needle_move.py, plus calibration loading rules."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from calibration_utils import CalibrationError, load_calibration
from motion_utils import (
    FIRMWARE_MAX_ABSOLUTE_STEPS,
    MotionConfigError,
    build_move_plan,
    load_move_config,
)


BASE_CONFIG: dict[str, Any] = {
    "serial": {"port": "COM_TEST", "baud": 115200, "reset_wait_seconds": 2.0},
    "driver": {"steps_per_revolution": 800},
    "execution": {
        "movement_mode": "degrees",
        "require_zero_net_steps": True,
        "require_typed_confirmation": True,
        "default_pause_between_moves_seconds": 2.0,
        "maximum_absolute_steps_per_move": 5000,
    },
    "moves": [
        {"name": "forward_1", "direction": "forward", "mm": 5.0, "degrees": 90.0},
        {"name": "backward_1", "direction": "backward", "mm": 5.0, "degrees": 90.0},
    ],
}


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    """Write a movement config, deep-merging the given section overrides."""
    config = copy.deepcopy(BASE_CONFIG)
    for section, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(section), dict):
            config[section].update(value)
        else:
            config[section] = value
    path = tmp_path / "move.yaml"
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


# --- 7. Invalid YAML ---------------------------------------------------------


def test_unparseable_yaml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("serial: [unclosed\n  bracket: true\n", encoding="utf-8")

    with pytest.raises(MotionConfigError, match="not valid YAML"):
        load_move_config(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="not found"):
        load_move_config(tmp_path / "absent.yaml")


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(MotionConfigError, match="empty"):
        load_move_config(path)


def test_yaml_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(MotionConfigError, match="mapping"):
        load_move_config(path)


@pytest.mark.parametrize("section", ["serial", "driver", "execution", "moves"])
def test_every_required_section_must_be_present(tmp_path: Path, section: str) -> None:
    config = copy.deepcopy(BASE_CONFIG)
    del config[section]
    path = tmp_path / "move.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(MotionConfigError, match=section):
        load_move_config(path)


def test_moves_must_be_a_non_empty_list(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="non-empty"):
        load_move_config(write_config(tmp_path, moves=[]))


def test_booleans_must_be_real_booleans(tmp_path: Path) -> None:
    path = write_config(tmp_path, execution={"use_mm_calibration": "yes"})

    with pytest.raises(MotionConfigError, match="true or false"):
        load_move_config(path)


def test_port_must_be_a_non_empty_string(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="serial.port"):
        load_move_config(write_config(tmp_path, serial={"port": "   "}))


# --- 8/9. Missing calibration and calibrated: false ---------------------------


def test_missing_calibration_file_explains_both_ways_forward(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError) as error:
        load_calibration(tmp_path / "needle_calibration.yaml")

    message = str(error.value)
    assert "99_needle_calibration.py" in message
    assert "use_mm_calibration: false" in message


def test_calibrated_false_blocks_millimetre_mode(tmp_path: Path) -> None:
    path = write_calibration(tmp_path, calibrated=False)

    with pytest.raises(CalibrationError, match="calibrated: false"):
        load_calibration(path).require_calibrated()


def test_calibrated_true_without_mm_per_step_is_rejected(tmp_path: Path) -> None:
    path = write_calibration(tmp_path, mm_per_step=None)

    with pytest.raises(CalibrationError, match="no usable"):
        load_calibration(path).require_calibrated()


def test_shipped_calibration_file_is_honestly_uncalibrated(package_root: Path) -> None:
    # The repository must not ship invented calibration numbers.
    calibration = load_calibration(package_root / "configs" / "needle_calibration.yaml")

    assert calibration.calibrated is False
    assert calibration.mm_per_step is None
    with pytest.raises(CalibrationError):
        calibration.require_calibrated()


def test_calibrated_file_is_usable_for_millimetre_planning(tmp_path: Path) -> None:
    calibration = load_calibration(write_calibration(tmp_path)).require_calibrated()
    config = load_move_config(
        write_config(tmp_path, execution={"movement_mode": "mm"})
    )

    plan = build_move_plan(config, mm_per_step=calibration.mm_per_step)

    assert [move.unit for move in plan] == ["mm", "mm"]
    assert plan[0].signed_steps == 889   # 5.0 / 0.005625 = 888.9
    assert plan[1].signed_steps == -889


def test_millimetre_mode_requires_a_calibration_value(tmp_path: Path) -> None:
    config = load_move_config(
        write_config(tmp_path, execution={"movement_mode": "mm"})
    )

    with pytest.raises(MotionConfigError, match="mm_per_step"):
        build_move_plan(config, mm_per_step=None)


# --- 10. Negative or zero movement magnitudes --------------------------------


@pytest.mark.parametrize("magnitude", [0, -1.0, -90.0])
def test_non_positive_degrees_are_rejected(tmp_path: Path, magnitude: float) -> None:
    config = load_move_config(
        write_config(
            tmp_path,
            moves=[{"name": "bad", "direction": "forward", "degrees": magnitude}],
        )
    )

    with pytest.raises(MotionConfigError, match="greater than zero"):
        build_move_plan(config)


def test_a_value_that_rounds_to_zero_steps_is_rejected(tmp_path: Path) -> None:
    # 0.1 degrees is 0.22 steps at 800 steps/revolution.
    config = load_move_config(
        write_config(
            tmp_path,
            moves=[{"name": "tiny", "direction": "forward", "degrees": 0.1}],
        )
    )

    with pytest.raises(MotionConfigError, match="rounds to 0 steps"):
        build_move_plan(config)


def test_the_inactive_unit_field_may_be_absent(tmp_path: Path) -> None:
    # In degrees mode, a move without an `mm` field is still valid.
    config = load_move_config(
        write_config(
            tmp_path,
            moves=[
                {"name": "f", "direction": "forward", "degrees": 90.0},
                {"name": "b", "direction": "backward", "degrees": 90.0},
            ],
        )
    )

    plan = build_move_plan(config)

    assert [move.signed_steps for move in plan] == [200, -200]


def test_the_active_unit_field_must_be_present(tmp_path: Path) -> None:
    config = load_move_config(
        write_config(
            tmp_path, moves=[{"name": "f", "direction": "forward", "mm": 5.0}]
        )
    )

    with pytest.raises(MotionConfigError, match="degrees"):
        build_move_plan(config)


@pytest.mark.parametrize("bad_name", ["", "   ", None, 7])
def test_every_move_needs_a_name(tmp_path: Path, bad_name: object) -> None:
    config = load_move_config(
        write_config(
            tmp_path,
            moves=[{"name": bad_name, "direction": "forward", "degrees": 90.0}],
        )
    )

    with pytest.raises(MotionConfigError, match="name"):
        build_move_plan(config)


# --- 11. Unsupported direction names -----------------------------------------


@pytest.mark.parametrize(
    "direction", ["up", "down", "FORWARD", "Forward", "reverse", "back", "", None, 1]
)
def test_unsupported_direction_names_are_rejected(
    tmp_path: Path, direction: object
) -> None:
    config = load_move_config(
        write_config(
            tmp_path,
            moves=[{"name": "m", "direction": direction, "degrees": 90.0}],
        )
    )

    with pytest.raises(MotionConfigError, match="direction must be one of"):
        build_move_plan(config)


def test_only_forward_and_backward_are_accepted(tmp_path: Path) -> None:
    config = load_move_config(
        write_config(
            tmp_path,
            moves=[
                {"name": "f", "direction": "forward", "degrees": 90.0},
                {"name": "b", "direction": "backward", "degrees": 90.0},
            ],
        )
    )

    plan = build_move_plan(config)

    assert plan[0].signed_steps > 0
    assert plan[1].signed_steps < 0


# --- 12. Excessive motion ----------------------------------------------------


def test_a_move_beyond_the_configured_maximum_is_rejected(tmp_path: Path) -> None:
    config = load_move_config(
        write_config(
            tmp_path,
            execution={"maximum_absolute_steps_per_move": 400},
            moves=[{"name": "far", "direction": "forward", "degrees": 360.0}],
        )
    )

    with pytest.raises(MotionConfigError, match="exceeds"):
        build_move_plan(config)


def test_a_configured_maximum_above_the_firmware_limit_is_rejected(
    tmp_path: Path,
) -> None:
    path = write_config(
        tmp_path,
        execution={
            "maximum_absolute_steps_per_move": FIRMWARE_MAX_ABSOLUTE_STEPS + 1
        },
    )

    with pytest.raises(MotionConfigError, match="firmware limit"):
        load_move_config(path)


def test_a_move_at_exactly_the_maximum_is_allowed(tmp_path: Path) -> None:
    config = load_move_config(
        write_config(
            tmp_path,
            execution={
                "maximum_absolute_steps_per_move": 800,
                "require_zero_net_steps": False,
            },
            moves=[{"name": "one_turn", "direction": "forward", "degrees": 360.0}],
        )
    )

    assert build_move_plan(config)[0].signed_steps == 800


# --- The shipped example configuration ---------------------------------------


def test_shipped_move_config_is_valid_and_balanced(package_root: Path) -> None:
    config = load_move_config(package_root / "configs" / "01_needle_move.yaml")
    plan = build_move_plan(config)

    assert config.execution.use_mm_calibration is False  # rig is not calibrated yet
    assert [move.signed_steps for move in plan] == [200, 100, -300, -100, 100]
    assert sum(move.signed_steps for move in plan) == 0
