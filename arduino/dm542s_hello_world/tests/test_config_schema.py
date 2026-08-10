"""Movement-configuration schema: modes, per-move pauses, typos, duplicates.

The design goal being protected here is that a user changes the motion sequence
by editing a YAML list and nothing else -- and that a mistake in that YAML is
reported, never silently ignored.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from motion_utils import (
    MODE_DEGREES,
    MODE_MM,
    MODE_STEPS,
    MotionConfigError,
    build_move_plan,
    count_backward_moves,
    count_forward_moves,
    load_move_config,
    net_steps,
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
        {"name": "forward_1", "direction": "forward", "degrees": 90.0},
        {"name": "backward_1", "direction": "backward", "degrees": 90.0},
    ],
}


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    config = copy.deepcopy(BASE_CONFIG)
    for section, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(section), dict):
            config[section].update(value)
        else:
            config[section] = value
    path = tmp_path / "move.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def plan_for(tmp_path: Path, mm_per_step: float | None = None, **overrides: Any):
    return build_move_plan(
        load_move_config(write_config(tmp_path, **overrides)), mm_per_step=mm_per_step
    )


# --- The moves list is authoritative ----------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3, 7, 12])
def test_the_move_list_length_alone_determines_the_number_of_moves(
    tmp_path: Path, count: int
) -> None:
    moves = [
        {"name": f"m{i}", "direction": "forward", "degrees": 45.0}
        for i in range(count)
    ]
    plan = plan_for(
        tmp_path, moves=moves, execution={"require_zero_net_steps": False}
    )

    assert len(plan) == count
    assert [m.number for m in plan] == list(range(1, count + 1))


def test_no_forward_or_backward_counters_exist_in_the_schema() -> None:
    """Redundant counters could disagree with the list; there must be none."""
    from motion_utils import EXECUTION_KEYS, MOVE_KEYS, TOP_LEVEL_KEYS

    every_key = set(EXECUTION_KEYS) | set(MOVE_KEYS) | set(TOP_LEVEL_KEYS)
    for forbidden in (
        "number_of_forward_moves",
        "number_of_backward_moves",
        "move_count",
        "number_of_moves",
    ):
        assert forbidden not in every_key


def test_an_arbitrary_mixed_order_is_preserved_exactly(tmp_path: Path) -> None:
    # forward, forward, backward, backward, forward -- unique magnitude each.
    plan = plan_for(
        tmp_path,
        moves=[
            {"name": "lower_5", "direction": "forward", "degrees": 90.0},
            {"name": "lower_3", "direction": "forward", "degrees": 45.0},
            {"name": "raise_8", "direction": "backward", "degrees": 135.0},
            {"name": "raise_2", "direction": "backward", "degrees": 22.5},
            {"name": "lower_2", "direction": "forward", "degrees": 22.5},
        ],
    )

    assert [m.name for m in plan] == [
        "lower_5", "lower_3", "raise_8", "raise_2", "lower_2"
    ]
    assert [m.direction for m in plan] == [
        "forward", "forward", "backward", "backward", "forward"
    ]
    assert [m.signed_steps for m in plan] == [200, 100, -300, -50, 50]
    assert [m.cumulative_steps for m in plan] == [200, 300, 0, -50, 0]
    assert net_steps(plan) == 0
    assert count_forward_moves(plan) == 3
    assert count_backward_moves(plan) == 2


def test_every_move_keeps_its_own_unique_magnitude(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        moves=[
            {"name": "a", "direction": "forward", "degrees": 9.0},
            {"name": "b", "direction": "forward", "degrees": 18.0},
            {"name": "c", "direction": "forward", "degrees": 36.0},
            {"name": "d", "direction": "backward", "degrees": 63.0},
        ],
    )

    assert [m.requested_value for m in plan] == [9.0, 18.0, 36.0, 63.0]
    assert [m.signed_steps for m in plan] == [20, 40, 80, -140]
    assert net_steps(plan) == 0


# --- movement_mode enum, with the legacy boolean still accepted --------------


def test_degrees_mode_uses_steps_per_revolution(tmp_path: Path) -> None:
    plan = plan_for(tmp_path, execution={"movement_mode": MODE_DEGREES})

    assert [m.unit for m in plan] == ["degrees", "degrees"]
    assert plan[0].signed_steps == 200


def test_mm_mode_uses_the_calibration(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        mm_per_step=0.005,
        execution={"movement_mode": MODE_MM},
        moves=[
            {"name": "a", "direction": "forward", "mm": 5.0},
            {"name": "b", "direction": "backward", "mm": 5.0},
        ],
    )

    assert [m.unit for m in plan] == ["mm", "mm"]
    assert [m.signed_steps for m in plan] == [1000, -1000]


def test_steps_mode_does_not_convert_through_another_unit(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        execution={"movement_mode": MODE_STEPS},
        driver={"steps_per_revolution": 1600},   # deliberately different
        moves=[
            {"name": "a", "direction": "forward", "steps": 137},
            {"name": "b", "direction": "backward", "steps": 137},
        ],
    )

    assert plan[0].signed_steps == 137
    assert plan[0].exact_steps == 137.0          # exact, no rounding at all
    assert plan[0].unit == "steps"
    assert net_steps(plan) == 0


def test_steps_mode_requires_whole_numbers(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="whole number"):
        plan_for(
            tmp_path,
            execution={"movement_mode": MODE_STEPS},
            moves=[{"name": "a", "direction": "forward", "steps": 12.5}],
        )


@pytest.mark.parametrize(
    ("legacy", "expected"), [(True, MODE_MM), (False, MODE_DEGREES)]
)
def test_the_legacy_boolean_still_works(
    tmp_path: Path, legacy: bool, expected: str
) -> None:
    config = copy.deepcopy(BASE_CONFIG)
    del config["execution"]["movement_mode"]
    config["execution"]["use_mm_calibration"] = legacy
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    loaded = load_move_config(path)

    assert loaded.execution.movement_mode == expected
    assert any("use_mm_calibration is deprecated" in w for w in loaded.deprecations)


def test_a_contradictory_mode_and_legacy_boolean_is_refused(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        execution={"movement_mode": "degrees", "use_mm_calibration": True},
    )

    with pytest.raises(MotionConfigError, match="different things"):
        load_move_config(path)


def test_an_agreeing_mode_and_legacy_boolean_is_accepted(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, execution={"movement_mode": "mm", "use_mm_calibration": True}
    )

    assert load_move_config(path).execution.movement_mode == MODE_MM


@pytest.mark.parametrize("bad", ["MM", "millimetres", "inches", "", None, 1])
def test_an_invalid_movement_mode_is_refused(tmp_path: Path, bad: object) -> None:
    with pytest.raises(MotionConfigError, match="movement_mode"):
        load_move_config(write_config(tmp_path, execution={"movement_mode": bad}))


def test_the_mode_agnostic_value_key_works(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        moves=[
            {"name": "a", "direction": "forward", "value": 90.0},
            {"name": "b", "direction": "backward", "value": 90.0},
        ],
    )

    assert [m.signed_steps for m in plan] == [200, -200]


def test_value_and_the_named_unit_together_are_ambiguous(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="not both"):
        plan_for(
            tmp_path,
            moves=[{"name": "a", "direction": "forward", "value": 90.0, "degrees": 45.0}],
        )


def test_the_active_field_is_named_in_the_error(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="movement_mode is 'degrees'"):
        plan_for(tmp_path, moves=[{"name": "a", "direction": "forward", "mm": 5.0}])


# --- Per-move pauses ---------------------------------------------------------


def test_a_per_move_pause_overrides_the_global_default(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        execution={"default_pause_between_moves_seconds": 2.0},
        moves=[
            {"name": "a", "direction": "forward", "degrees": 90.0,
             "pause_after_seconds": 7.5},
            {"name": "b", "direction": "backward", "degrees": 90.0},
        ],
    )

    assert plan[0].pause_after_seconds == 7.5   # explicit
    assert plan[1].pause_after_seconds == 2.0   # inherited default


def test_a_zero_per_move_pause_is_honoured_not_treated_as_missing(
    tmp_path: Path,
) -> None:
    plan = plan_for(
        tmp_path,
        execution={"default_pause_between_moves_seconds": 5.0},
        moves=[
            {"name": "a", "direction": "forward", "degrees": 90.0,
             "pause_after_seconds": 0.0},
            {"name": "b", "direction": "backward", "degrees": 90.0},
        ],
    )

    assert plan[0].pause_after_seconds == 0.0


def test_a_negative_per_move_pause_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="pause_after_seconds"):
        plan_for(
            tmp_path,
            moves=[
                {"name": "a", "direction": "forward", "degrees": 90.0,
                 "pause_after_seconds": -1.0}
            ],
        )


def test_the_legacy_global_pause_key_is_still_accepted(tmp_path: Path) -> None:
    config = copy.deepcopy(BASE_CONFIG)
    del config["execution"]["default_pause_between_moves_seconds"]
    config["execution"]["pause_between_moves_seconds"] = 3.5
    path = tmp_path / "legacy_pause.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    loaded = load_move_config(path)

    assert loaded.execution.default_pause_between_moves_seconds == 3.5
    assert any("pause_between_moves_seconds" in w for w in loaded.deprecations)


# --- Unknown keys are rejected, never silently ignored -----------------------


def test_an_unknown_key_inside_a_move_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="unknown configuration key"):
        load_move_config(
            write_config(
                tmp_path,
                moves=[
                    {"name": "a", "direction": "forward", "degrees": 90.0,
                     "totally_made_up": 1}
                ],
            )
        )


def test_a_misspelled_move_key_suggests_the_right_one(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="did you mean 'pause_after_seconds'"):
        load_move_config(
            write_config(
                tmp_path,
                moves=[
                    {"name": "a", "direction": "forward", "degrees": 90.0,
                     "pause_afterr_seconds": 2.0}
                ],
            )
        )


def test_a_misspelled_execution_key_is_rejected(tmp_path: Path) -> None:
    # The exact typo Agent 2 demonstrated being silently ignored.
    with pytest.raises(MotionConfigError, match="unknown configuration key"):
        load_move_config(
            write_config(tmp_path, execution={"pause_between_move_seconds": 99})
        )


def test_an_unknown_top_level_section_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="unknown configuration key"):
        load_move_config(write_config(tmp_path, softwar_limits={"enabled": True}))


def test_a_misspelled_top_level_section_suggests_the_right_one(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="did you mean 'software_limits'"):
        load_move_config(write_config(tmp_path, software_limit={"enabled": True}))


def test_an_unknown_serial_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="unknown configuration key"):
        load_move_config(write_config(tmp_path, serial={"bard": 115200}))


# --- Duplicate names ---------------------------------------------------------


def test_duplicate_move_names_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="already used by move 1"):
        plan_for(
            tmp_path,
            moves=[
                {"name": "same", "direction": "forward", "degrees": 90.0},
                {"name": "same", "direction": "backward", "degrees": 90.0},
            ],
        )


def test_duplicate_names_are_detected_after_whitespace_stripping(
    tmp_path: Path,
) -> None:
    with pytest.raises(MotionConfigError, match="already used"):
        plan_for(
            tmp_path,
            moves=[
                {"name": "lower", "direction": "forward", "degrees": 90.0},
                {"name": "  lower  ", "direction": "backward", "degrees": 90.0},
            ],
        )


# --- Error messages name the offending file ---------------------------------


def test_a_missing_field_error_names_the_config_file(tmp_path: Path) -> None:
    config = copy.deepcopy(BASE_CONFIG)
    del config["execution"]["require_zero_net_steps"]
    path = tmp_path / "my_sequence.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(MotionConfigError, match="my_sequence.yaml"):
        load_move_config(path)


def test_a_bad_direction_error_names_the_config_file(tmp_path: Path) -> None:
    config = copy.deepcopy(BASE_CONFIG)
    config["moves"] = [{"name": "a", "direction": "sideways", "degrees": 90.0}]
    path = tmp_path / "my_sequence.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(MotionConfigError, match="my_sequence.yaml:moves\\[1\\]"):
        build_move_plan(load_move_config(path))


# --- The shipped configuration ----------------------------------------------


def test_the_shipped_config_is_the_documented_five_move_sequence(
    package_root: Path,
) -> None:
    config = load_move_config(package_root / "configs" / "01_needle_move.yaml")
    plan = build_move_plan(config)

    assert config.execution.movement_mode == MODE_DEGREES  # not calibrated yet
    assert [m.direction for m in plan] == [
        "forward", "forward", "backward", "backward", "forward"
    ]
    assert [m.signed_steps for m in plan] == [200, 100, -300, -100, 100]
    assert net_steps(plan) == 0
    assert config.deprecations == ()
    assert config.software_limits.enabled is True


def test_the_shipped_config_uses_per_move_pauses(package_root: Path) -> None:
    plan = build_move_plan(
        load_move_config(package_root / "configs" / "01_needle_move.yaml")
    )

    assert [m.pause_after_seconds for m in plan] == [2.0, 1.0, 2.0, 1.0, 0.0]
