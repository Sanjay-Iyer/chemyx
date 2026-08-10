"""Zero-net validation, including the one-step rounding imbalance case."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from motion_utils import (
    MotionConfigError,
    ZeroNetMotionError,
    build_move_plan,
    format_plan_table,
    load_move_config,
    net_steps,
    total_backward_steps,
    total_forward_steps,
    validate_zero_net_steps,
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
    "moves": [],
}


def plan_from(
    tmp_path: Path,
    moves: list[dict[str, Any]],
    *,
    use_mm: bool = False,
    mm_per_step: float | None = None,
):
    config = copy.deepcopy(BASE_CONFIG)
    config["moves"] = moves
    config["execution"]["movement_mode"] = "mm" if use_mm else "degrees"
    path = tmp_path / "move.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return build_move_plan(load_move_config(path), mm_per_step=mm_per_step)


def degrees_move(name: str, direction: str, degrees: float) -> dict[str, Any]:
    return {"name": name, "direction": direction, "degrees": degrees}


def mm_move(name: str, direction: str, mm: float) -> dict[str, Any]:
    return {"name": name, "direction": direction, "mm": mm}


# --- 5. Zero-net validation --------------------------------------------------


def test_the_documented_example_sequence_nets_to_zero(tmp_path: Path) -> None:
    # forward 5, forward 3, backward 8, backward 2, forward 2 -> 0 mm
    plan = plan_from(
        tmp_path,
        [
            mm_move("forward_1", "forward", 5.0),
            mm_move("forward_2", "forward", 3.0),
            mm_move("backward_1", "backward", 8.0),
            mm_move("backward_2", "backward", 2.0),
            mm_move("forward_3", "forward", 2.0),
        ],
        use_mm=True,
        mm_per_step=0.005,
    )

    assert [move.signed_steps for move in plan] == [1000, 600, -1600, -400, 400]
    assert net_steps(plan) == 0
    assert validate_zero_net_steps(plan) == 0


def test_cumulative_position_is_tracked_move_by_move(tmp_path: Path) -> None:
    plan = plan_from(
        tmp_path,
        [
            degrees_move("f1", "forward", 90.0),
            degrees_move("f2", "forward", 45.0),
            degrees_move("b1", "backward", 135.0),
        ],
    )

    assert [move.cumulative_steps for move in plan] == [200, 300, 0]
    assert total_forward_steps(plan) == 300
    assert total_backward_steps(plan) == 300


def test_an_unbalanced_sequence_is_refused(tmp_path: Path) -> None:
    plan = plan_from(
        tmp_path,
        [
            degrees_move("f1", "forward", 90.0),
            degrees_move("b1", "backward", 45.0),
        ],
    )

    with pytest.raises(ZeroNetMotionError) as error:
        validate_zero_net_steps(plan)

    message = str(error.value)
    assert "+100" in message                    # exact mismatch is stated
    assert "total forward steps:  200" in message
    assert "total backward steps: 100" in message
    assert "+90" in message or "45" in message  # nominal mismatch is stated


def test_a_backward_biased_sequence_reports_the_direction(tmp_path: Path) -> None:
    plan = plan_from(
        tmp_path,
        [
            degrees_move("f1", "forward", 45.0),
            degrees_move("b1", "backward", 90.0),
        ],
    )

    with pytest.raises(ZeroNetMotionError, match="backward of zero"):
        validate_zero_net_steps(plan)


def test_validation_can_be_switched_off(tmp_path: Path) -> None:
    plan = plan_from(tmp_path, [degrees_move("f1", "forward", 90.0)])

    # Returns the imbalance instead of raising, so the caller can report it.
    assert validate_zero_net_steps(plan, required=False) == 200


def test_no_hidden_correction_move_is_appended(tmp_path: Path) -> None:
    moves = [
        degrees_move("f1", "forward", 90.0),
        degrees_move("b1", "backward", 45.0),
    ]
    plan = plan_from(tmp_path, moves)

    assert len(plan) == len(moves)
    with pytest.raises(ZeroNetMotionError, match="No hidden correction move"):
        validate_zero_net_steps(plan)


# --- 6. One-step rounding imbalance detection --------------------------------


def test_a_sequence_balanced_in_mm_can_still_fail_on_whole_steps(
    tmp_path: Path,
) -> None:
    # 0.3 mm/step: 1.0 mm -> 3.33 -> 3 steps, and 2.0 mm -> 6.67 -> 7 steps.
    # +3 +3 -7 = -1 step even though +1.0 +1.0 -2.0 = 0.0 mm exactly.
    plan = plan_from(
        tmp_path,
        [
            mm_move("f1", "forward", 1.0),
            mm_move("f2", "forward", 1.0),
            mm_move("b1", "backward", 2.0),
        ],
        use_mm=True,
        mm_per_step=0.3,
    )

    assert [move.signed_steps for move in plan] == [3, 3, -7]
    assert net_steps(plan) == -1

    with pytest.raises(ZeroNetMotionError) as error:
        validate_zero_net_steps(plan)

    message = str(error.value)
    assert "rounding imbalance" in message
    assert "sums to 0 mm before rounding" in message
    assert "-1" in message


def test_validation_happens_after_rounding_not_before(tmp_path: Path) -> None:
    plan = plan_from(
        tmp_path,
        [
            mm_move("f1", "forward", 1.0),
            mm_move("f2", "forward", 1.0),
            mm_move("b1", "backward", 2.0),
        ],
        use_mm=True,
        mm_per_step=0.3,
    )

    nominal = sum(
        move.requested_value if move.signed_steps > 0 else -move.requested_value
        for move in plan
    )

    assert nominal == pytest.approx(0.0)   # balanced in millimetres
    assert net_steps(plan) != 0            # not balanced in whole steps


def test_the_operator_can_fix_the_imbalance_explicitly(tmp_path: Path) -> None:
    # Same intent as above, but the backward move is split so the steps cancel.
    plan = plan_from(
        tmp_path,
        [
            mm_move("f1", "forward", 1.0),
            mm_move("f2", "forward", 1.0),
            mm_move("b1", "backward", 1.0),
            mm_move("b2", "backward", 1.0),
        ],
        use_mm=True,
        mm_per_step=0.3,
    )

    assert [move.signed_steps for move in plan] == [3, 3, -3, -3]
    assert validate_zero_net_steps(plan) == 0


def test_rounding_error_is_exposed_per_move(tmp_path: Path) -> None:
    plan = plan_from(
        tmp_path, [mm_move("f1", "forward", 2.5)], use_mm=True, mm_per_step=0.0182
    )
    move = plan[0]

    assert move.signed_steps == 137
    assert move.exact_steps == pytest.approx(137.36, abs=0.01)
    assert move.commanded_value == pytest.approx(2.4934, abs=1e-4)
    assert move.rounding_error == pytest.approx(-0.0066, abs=1e-4)


def test_preflight_table_shows_every_column(tmp_path: Path) -> None:
    plan = plan_from(
        tmp_path,
        [
            degrees_move("forward_1", "forward", 90.0),
            degrees_move("backward_1", "backward", 90.0),
        ],
    )

    table = format_plan_table(plan)

    for expected in ("forward_1", "backward_1", "+200", "-200", "cumul", "timeout"):
        assert expected in table


def test_empty_plans_cannot_reach_validation(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="non-empty"):
        plan_from(tmp_path, [])
