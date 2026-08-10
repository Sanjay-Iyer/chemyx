"""Timeout scaling and relative software plan bounds.

Two independent failure modes are covered here:

1. A fixed timeout that is fine for a 100-step hello-world move but aborts a
   healthy 5000-step move part-way, leaving the needle somewhere unknown.
2. A sequence that sums to zero but travels many revolutions away from the
   start before coming back, on a rig with no limit switches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from motion_utils import (
    DEFENSIBLE_PULSE_HALF_PERIOD_US_RANGE,
    FIRMWARE_PULSE_HALF_PERIOD_US,
    MotionConfigError,
    SoftwareLimitError,
    SoftwareLimits,
    TimingSettings,
    build_move_plan,
    load_move_config,
    net_steps,
    plan_excursion,
    total_expected_runtime_seconds,
    validate_plan_within_limits,
    validate_timeouts_cover_plan,
    validate_zero_net_steps,
)

from test_config_schema import plan_for, write_config


# --- Timeouts scale with the step count -------------------------------------


@pytest.mark.parametrize(
    ("steps", "expected_motion"), [(100, 1.0), (800, 8.0), (5000, 50.0)]
)
def test_expected_motion_matches_the_firmware_pulse_rate(
    steps: int, expected_motion: float
) -> None:
    # 5 ms HIGH + 5 ms LOW = 10 ms per step = 100 steps/second.
    timing = TimingSettings()

    assert timing.expected_motion_seconds(steps) == pytest.approx(expected_motion)


@pytest.mark.parametrize("steps", [1, 100, 800, 2000, 5000])
def test_the_timeout_always_exceeds_the_expected_motion(steps: int) -> None:
    timing = TimingSettings()

    assert timing.timeout_seconds(steps) > timing.expected_motion_seconds(steps)


def test_a_5000_step_move_does_not_use_a_hello_world_timeout() -> None:
    """The regression this whole section exists to prevent."""
    timing = TimingSettings()

    timeout = timing.timeout_seconds(5000)

    assert timing.expected_motion_seconds(5000) == pytest.approx(50.0)
    assert timeout > 15.0, "a 5000-step move must not use the 15 s CYCLE timeout"
    assert timeout > 10.0, "a 5000-step move must not use the 10 s FWD timeout"
    # 1 startup + 50 motion + 12.5 margin + 1 completion
    assert timeout == pytest.approx(64.5)


def test_the_timeout_is_the_documented_formula() -> None:
    timing = TimingSettings(
        pulse_half_period_us=5000,
        startup_allowance_seconds=1.0,
        completion_allowance_seconds=1.0,
        safety_margin_fraction=0.25,
    )
    expected = timing.expected_motion_seconds(800)

    assert timing.timeout_seconds(800) == pytest.approx(
        1.0 + expected + expected * 0.25 + 1.0
    )


def test_the_timeout_is_identical_for_both_directions() -> None:
    timing = TimingSettings()

    assert timing.timeout_seconds(-800) == timing.timeout_seconds(800)


def test_a_slower_sketch_produces_a_longer_timeout() -> None:
    fast = TimingSettings(pulse_half_period_us=5000)
    slow = TimingSettings(pulse_half_period_us=10000)

    assert slow.timeout_seconds(800) > fast.timeout_seconds(800)
    assert slow.expected_motion_seconds(800) == pytest.approx(16.0)


# --- Floor and ceiling -------------------------------------------------------


def test_a_tiny_move_still_gets_the_minimum_timeout() -> None:
    timing = TimingSettings(minimum_timeout_seconds=5.0)

    # One step is 10 ms of motion; the floor must dominate.
    assert timing.timeout_seconds(1) == pytest.approx(5.0)


def test_the_minimum_is_configurable() -> None:
    assert TimingSettings(minimum_timeout_seconds=20.0).timeout_seconds(1) == 20.0


def test_the_maximum_caps_an_otherwise_enormous_timeout() -> None:
    timing = TimingSettings(pulse_half_period_us=5000, maximum_timeout_seconds=30.0)

    # 800 steps would want 12 s, which is under the cap.
    assert timing.timeout_seconds(800) == pytest.approx(12.0)
    assert timing.timeout_seconds(800) <= 30.0


def test_the_maximum_never_cuts_below_what_a_move_genuinely_needs() -> None:
    """Clamping must not create the exact bug the scaling was added to fix."""
    timing = TimingSettings(pulse_half_period_us=5000, maximum_timeout_seconds=5.0)

    timeout = timing.timeout_seconds(5000)

    assert timeout > timing.expected_motion_seconds(5000)
    assert timeout == pytest.approx(timing.required_floor_seconds(5000))


def test_a_ceiling_that_would_abort_a_planned_move_is_refused(tmp_path: Path) -> None:
    # 360 degrees = 800 steps = 8 s of motion, which genuinely needs ~11 s.
    config = load_move_config(
        write_config(
            tmp_path,
            moves=[
                {"name": "long_out", "direction": "forward", "degrees": 360.0},
                {"name": "long_back", "direction": "backward", "degrees": 360.0},
            ],
            timing={"minimum_timeout_seconds": 1.0, "maximum_timeout_seconds": 3.0},
        )
    )
    plan = build_move_plan(config)

    with pytest.raises(MotionConfigError, match="would abort a healthy move"):
        validate_timeouts_cover_plan(plan, config.timing)


def test_a_generous_ceiling_passes_validation(tmp_path: Path) -> None:
    config = load_move_config(write_config(tmp_path))
    plan = build_move_plan(config)

    validate_timeouts_cover_plan(plan, config.timing)   # must not raise


def test_a_maximum_below_the_minimum_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MotionConfigError, match="below"):
        load_move_config(
            write_config(
                tmp_path,
                timing={"minimum_timeout_seconds": 30.0, "maximum_timeout_seconds": 5.0},
            )
        )


# --- Firmware/Python timing agreement ---------------------------------------


def test_the_default_pulse_period_matches_the_firmware_constant() -> None:
    assert TimingSettings().pulse_half_period_us == FIRMWARE_PULSE_HALF_PERIOD_US


def test_an_absurd_pulse_half_period_is_refused(tmp_path: Path) -> None:
    low, high = DEFENSIBLE_PULSE_HALF_PERIOD_US_RANGE

    with pytest.raises(MotionConfigError, match="pulse_half_period_us"):
        load_move_config(
            write_config(tmp_path, timing={"pulse_half_period_us": low / 10})
        )
    with pytest.raises(MotionConfigError, match="pulse_half_period_us"):
        load_move_config(
            write_config(tmp_path, timing={"pulse_half_period_us": high * 10})
        )


def test_a_pulse_period_of_one_microsecond_cannot_shrink_the_timeout(
    tmp_path: Path,
) -> None:
    """Agent 1's scenario: a bogus 1 us period producing a doomed 5 s timeout."""
    with pytest.raises(MotionConfigError):
        load_move_config(write_config(tmp_path, timing={"pulse_half_period_us": 1}))


def test_the_plan_carries_the_timeout_for_every_move(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)

    for move in plan:
        assert move.timeout_seconds > 0
        assert move.expected_seconds > 0
        assert move.timeout_seconds > move.expected_seconds


def test_total_runtime_includes_motion_and_pauses(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        moves=[
            {"name": "a", "direction": "forward", "degrees": 90.0,
             "pause_after_seconds": 3.0},
            {"name": "b", "direction": "backward", "degrees": 90.0,
             "pause_after_seconds": 1.0},
        ],
    )

    # 200 steps = 2 s each, plus 3 s and 1 s of pauses.
    assert total_expected_runtime_seconds(plan) == pytest.approx(2.0 + 3.0 + 2.0 + 1.0)


# --- Relative software plan bounds ------------------------------------------


def wander_plan(tmp_path: Path, **overrides):
    """Five moves out and five back: nets to zero, travels 30 revolutions."""
    moves = [
        {"name": f"out{i}", "direction": "forward", "degrees": 2160.0}
        for i in range(5)
    ] + [
        {"name": f"back{i}", "direction": "backward", "degrees": 2160.0}
        for i in range(5)
    ]
    return plan_for(tmp_path, moves=moves, **overrides)


def test_a_zero_net_plan_can_still_wander_far_from_the_start(tmp_path: Path) -> None:
    """The gap the final-total check cannot see."""
    plan = wander_plan(tmp_path)

    assert net_steps(plan) == 0
    assert validate_zero_net_steps(plan) == 0        # zero-net is satisfied
    lowest, highest = plan_excursion(plan)
    assert highest == 24000                          # 30 revolutions away
    assert lowest == 0


def test_the_wandering_plan_is_refused_by_the_software_bounds(tmp_path: Path) -> None:
    plan = wander_plan(tmp_path)
    limits = SoftwareLimits(enabled=True, minimum_steps=-1000, maximum_steps=1000)

    with pytest.raises(SoftwareLimitError) as error:
        validate_plan_within_limits(plan, limits)

    message = str(error.value)
    assert "24000" in message              # the peak excursion is named
    assert "RELATIVE" in message           # not presented as a machine limit
    assert "no homing" in message


def test_bounds_are_checked_at_every_intermediate_position(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        moves=[
            {"name": "out", "direction": "forward", "degrees": 360.0},   # +800
            {"name": "back", "direction": "backward", "degrees": 360.0},
        ],
    )
    limits = SoftwareLimits(enabled=True, minimum_steps=-500, maximum_steps=500)

    with pytest.raises(SoftwareLimitError, match="Move 1"):
        validate_plan_within_limits(plan, limits)


def test_a_plan_inside_the_bounds_is_accepted(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    limits = SoftwareLimits(enabled=True, minimum_steps=-1000, maximum_steps=1000)

    validate_plan_within_limits(plan, limits)   # must not raise


def test_asymmetric_bounds_are_honoured(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        moves=[
            {"name": "back", "direction": "backward", "degrees": 90.0},   # -200
            {"name": "fwd", "direction": "forward", "degrees": 90.0},
        ],
    )
    generous_forward = SoftwareLimits(
        enabled=True, minimum_steps=-100, maximum_steps=5000
    )

    with pytest.raises(SoftwareLimitError, match="Move 1"):
        validate_plan_within_limits(plan, generous_forward)

    validate_plan_within_limits(
        plan, SoftwareLimits(enabled=True, minimum_steps=-500, maximum_steps=0)
    )


def test_disabled_bounds_skip_the_check(tmp_path: Path) -> None:
    plan = wander_plan(tmp_path)

    validate_plan_within_limits(plan, SoftwareLimits(enabled=False))   # no raise


def test_bounds_default_to_disabled_when_the_section_is_absent(
    tmp_path: Path,
) -> None:
    import copy

    import yaml

    from test_config_schema import BASE_CONFIG

    config = copy.deepcopy(BASE_CONFIG)
    path = tmp_path / "no_limits.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    assert load_move_config(path).software_limits.enabled is False


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(500, 1000), (-1000, -500), (1000, -1000), (0, 0)],
)
def test_nonsensical_bounds_are_refused(
    tmp_path: Path, minimum: int, maximum: int
) -> None:
    with pytest.raises(MotionConfigError):
        load_move_config(
            write_config(
                tmp_path,
                software_limits={
                    "enabled": True,
                    "minimum_steps": minimum,
                    "maximum_steps": maximum,
                },
            )
        )


def test_the_excursion_helper_includes_the_starting_position(tmp_path: Path) -> None:
    plan = plan_for(
        tmp_path,
        moves=[
            {"name": "back", "direction": "backward", "degrees": 90.0},
            {"name": "fwd", "direction": "forward", "degrees": 90.0},
        ],
    )

    assert plan_excursion(plan) == (-200, 0)
    assert plan_excursion([]) == (0, 0)
