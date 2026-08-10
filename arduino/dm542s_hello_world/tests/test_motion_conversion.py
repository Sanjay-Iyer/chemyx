"""Unit conversion, rounding, and direction handling. No hardware involved."""

from __future__ import annotations

import pytest

from motion_utils import (
    DEFAULT_STEPS_PER_REVOLUTION,
    FIRMWARE_MAX_ABSOLUTE_STEPS,
    MotionConfigError,
    TimingSettings,
    degrees_to_steps,
    derive_calibration_factors,
    mm_to_steps,
    move_command,
    move_done_response,
    round_half_away_from_zero,
    steps_to_degrees,
    steps_to_mm,
)


# --- 1. Degree conversion at 800 steps/revolution ---------------------------


@pytest.mark.parametrize(
    ("degrees", "expected_steps"),
    [(90, 200), (180, 400), (360, 800)],
)
def test_documented_angles_convert_at_800_steps_per_revolution(
    degrees: float, expected_steps: int
) -> None:
    assert degrees_to_steps(degrees, DEFAULT_STEPS_PER_REVOLUTION) == expected_steps


def test_default_steps_per_revolution_matches_the_dip_switch_setting() -> None:
    assert DEFAULT_STEPS_PER_REVOLUTION == 800
    assert degrees_to_steps(90) == 200


def test_degrees_round_trip_through_steps() -> None:
    assert steps_to_degrees(degrees_to_steps(180)) == pytest.approx(180.0)
    assert steps_to_degrees(800) == pytest.approx(360.0)


@pytest.mark.parametrize("bad", [0, -800, 1.5, True, "800", None])
def test_bad_steps_per_revolution_is_rejected(bad: object) -> None:
    with pytest.raises(MotionConfigError):
        degrees_to_steps(90, bad)  # type: ignore[arg-type]


# --- 2. Positive and negative direction handling ----------------------------


@pytest.mark.parametrize(
    ("degrees", "expected_steps"),
    [(-90, -200), (-180, -400), (-360, -800), (-45, -100)],
)
def test_negative_degrees_produce_negative_steps(
    degrees: float, expected_steps: int
) -> None:
    assert degrees_to_steps(degrees) == expected_steps


def test_forward_and_backward_of_equal_size_cancel_exactly() -> None:
    assert degrees_to_steps(137.3) + degrees_to_steps(-137.3) == 0


def test_negative_millimetres_produce_negative_steps() -> None:
    assert mm_to_steps(-5.0, 0.005625) == -mm_to_steps(5.0, 0.005625)


# --- 3. Millimetre conversion ----------------------------------------------


def test_mm_to_steps_uses_mm_per_step() -> None:
    # 0.005 mm/step means 200 steps per millimetre.
    assert mm_to_steps(1.0, 0.005) == 200
    assert mm_to_steps(2.5, 0.005) == 500


def test_steps_to_mm_is_the_inverse_of_mm_to_steps() -> None:
    assert steps_to_mm(200, 0.005) == pytest.approx(1.0)


@pytest.mark.parametrize("bad", [0, -0.005, True, "0.005", None])
def test_bad_mm_per_step_is_rejected(bad: object) -> None:
    with pytest.raises(MotionConfigError):
        mm_to_steps(1.0, bad)  # type: ignore[arg-type]


def test_derive_calibration_factors_are_mutually_consistent() -> None:
    factors = derive_calibration_factors(0.0125, 800)

    assert factors["mm_per_degree"] == pytest.approx(0.0125)
    assert factors["mm_per_step"] == pytest.approx(0.0125 * 360 / 800)
    assert factors["steps_per_mm"] == pytest.approx(1.0 / factors["mm_per_step"])


def test_derive_calibration_factors_rejects_a_non_positive_slope() -> None:
    with pytest.raises(MotionConfigError):
        derive_calibration_factors(0.0, 800)


# --- 4. Integer rounding behaviour ------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.4, 0),
        (0.5, 1),      # half away from zero, NOT Python's banker's rounding
        (1.5, 2),
        (2.5, 3),      # built-in round(2.5) would give 2
        (-0.5, -1),
        (-2.5, -3),
        (137.4, 137),
        (137.6, 138),
    ],
)
def test_rounding_is_half_away_from_zero(value: float, expected: int) -> None:
    assert round_half_away_from_zero(value) == expected


def test_rounding_never_silently_truncates() -> None:
    # Truncation would give 137; the documented rule gives 138.
    assert round_half_away_from_zero(137.9) == 138
    assert int(137.9) == 137


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected_rather_than_rounded(bad: float) -> None:
    with pytest.raises(MotionConfigError):
        round_half_away_from_zero(bad)


def test_fractional_millimetres_report_the_actual_commanded_distance() -> None:
    mm_per_step = 0.0182
    steps = mm_to_steps(2.5, mm_per_step)

    assert steps == 137                                   # 137.36 rounds down
    assert steps_to_mm(steps, mm_per_step) == pytest.approx(2.4934)


# --- MOVE protocol helpers ---------------------------------------------------


def test_move_command_formats_both_signs() -> None:
    assert move_command(200) == "MOVE 200"
    assert move_command(-200) == "MOVE -200"
    assert move_done_response(-200) == "DONE MOVE -200"


def test_move_command_rejects_zero_steps() -> None:
    with pytest.raises(MotionConfigError, match="must not be zero"):
        move_command(0)


@pytest.mark.parametrize("bad", [1.5, "200", None, True])
def test_move_command_rejects_non_integer_step_counts(bad: object) -> None:
    with pytest.raises(MotionConfigError):
        move_command(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "steps", [FIRMWARE_MAX_ABSOLUTE_STEPS + 1, -(FIRMWARE_MAX_ABSOLUTE_STEPS + 1)]
)
def test_move_command_rejects_steps_beyond_the_firmware_maximum(steps: int) -> None:
    with pytest.raises(MotionConfigError, match="firmware maximum"):
        move_command(steps)


def test_move_timeout_covers_the_whole_pulse_train() -> None:
    # 800 steps at a 5000 us half period is 8 s of pulses.
    timing = TimingSettings()
    timeout = timing.timeout_seconds(800)

    # 1.0 startup + 8.0 motion + 2.0 margin (25 %) + 1.0 completion
    assert timeout == pytest.approx(12.0)
    assert timing.timeout_seconds(-800) == timeout
    assert timeout > timing.expected_motion_seconds(800)
