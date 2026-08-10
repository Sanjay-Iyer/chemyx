"""Calibration planning, repetitions, measured return error, and atomic writes.

No COM port is opened. The calibration script is imported by path (its module
name starts with a digit) so its planner and prompts can be tested directly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from calibration_utils import (
    CalibrationError,
    CalibrationPoint,
    build_calibration_fit,
    build_results_document,
    load_calibration,
    measured_return_errors,
    nonlinearity_warnings,
    parse_trial_degrees,
    return_error_warnings,
    validate_return_error,
    write_calibration_document,
    write_results_csv,
    write_results_yaml,
)
from motion_utils import (
    FIRMWARE_MAX_ABSOLUTE_STEPS,
    MotionConfigError,
    atomic_write_text,
)


BASE_CALIBRATION_CONFIG = {
    "serial": {"port": "COM_TEST", "baud": 115200, "reset_wait_seconds": 2.0},
    "driver": {"steps_per_revolution": 800},
    "motion": {
        "pause_before_measurement_seconds": 3.0,
        "pause_after_return_seconds": 2.0,
        "require_typed_confirmation": True,
        "maximum_absolute_degrees": 360,
    },
    "calibration": {
        "trial_degrees": [90, 180, 360],
        "repetitions": 1,
        "fit_through_origin": True,
        "measure_return_error": True,
        "update_authoritative_calibration_file": True,
    },
}


def write_calibration_config(tmp_path: Path, **overrides) -> Path:
    import copy

    config = copy.deepcopy(BASE_CALIBRATION_CONFIG)
    for section, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(section), dict):
            config[section].update(value)
        else:
            config[section] = value
    path = tmp_path / "calibration.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


# --- Trial planning ----------------------------------------------------------


def test_a_trial_above_the_firmware_limit_is_refused_before_any_port(
    calibration_script, tmp_path: Path
) -> None:
    """Agent 2's C-2: this used to open the port and only then fail."""
    path = write_calibration_config(
        tmp_path,
        motion={"maximum_absolute_degrees": 100000},
        calibration={"trial_degrees": [5000]},
    )

    with pytest.raises(CalibrationError, match="firmware maximum"):
        calibration_script.CalibrationPlan(path)


def test_the_firmware_ceiling_is_checked_in_the_shared_parser() -> None:
    with pytest.raises(CalibrationError) as error:
        parse_trial_degrees(
            {"calibration": {"trial_degrees": [5000]}},
            steps_per_revolution=800,
            maximum_absolute_degrees=100000,
        )

    assert str(FIRMWARE_MAX_ABSOLUTE_STEPS) in str(error.value)


def test_repetitions_expand_into_independent_ordered_trials(
    calibration_script, tmp_path: Path
) -> None:
    plan = calibration_script.CalibrationPlan(
        write_calibration_config(
            tmp_path, calibration={"trial_degrees": [90, 180], "repetitions": 3}
        )
    )

    assert len(plan.trials) == 6
    assert [degrees for _, degrees, _ in plan.trials] == [90, 180, 90, 180, 90, 180]
    assert [rep for rep, _, _ in plan.trials] == [1, 1, 2, 2, 3, 3]
    assert [steps for _, _, steps in plan.trials] == [200, 400, 200, 400, 200, 400]


def test_the_trial_list_length_is_authoritative(
    calibration_script, tmp_path: Path
) -> None:
    plan = calibration_script.CalibrationPlan(
        write_calibration_config(
            tmp_path, calibration={"trial_degrees": [45, 90, 135, 180], "repetitions": 2}
        )
    )

    assert len(plan.trials) == 8


def test_the_return_to_zero_check_walks_the_real_sequence(
    calibration_script, tmp_path: Path, capsys
) -> None:
    """The old version computed +n-n and could never fail. This one can."""
    plan = calibration_script.CalibrationPlan(write_calibration_config(tmp_path))
    plan.verify_every_trial_returns_to_zero()

    printed = capsys.readouterr().out
    assert "peak excursion 800 steps" in printed


def test_the_return_to_zero_check_can_actually_fail(
    calibration_script, tmp_path: Path
) -> None:
    """Guard against the tautology regressing.

    The old implementation computed ``position = 0; position += n; position -= n``
    and then asserted it was zero, which is true for every possible input. This
    proves the replacement genuinely walks the planned sequence: a trial whose
    forward leg exceeds the firmware ceiling is caught.
    """
    plan = calibration_script.CalibrationPlan(write_calibration_config(tmp_path))
    plan.trials = [(1, 9999.0, FIRMWARE_MAX_ABSOLUTE_STEPS + 1)]

    with pytest.raises(CalibrationError, match="firmware limit"):
        plan.verify_every_trial_returns_to_zero()


def test_the_return_to_zero_check_is_not_a_tautology(package_root: Path) -> None:
    """The +n -n formulation must not come back."""
    source = (package_root / "99_needle_calibration.py").read_text(encoding="utf-8")
    body = source[source.index("def verify_every_trial_returns_to_zero") :]
    body = body[: body.index("\n    def ") if "\n    def " in body else len(body)]

    assert "position = 0\n            position += steps" not in body
    assert "for leg_name, leg in" in body   # walks both legs of every trial


def test_unknown_calibration_keys_are_rejected(
    calibration_script, tmp_path: Path
) -> None:
    path = write_calibration_config(tmp_path, calibration={"repetitons": 2})

    # reject_unknown_keys raises MotionConfigError, the base of CalibrationError.
    with pytest.raises(MotionConfigError, match="did you mean 'repetitions'"):
        calibration_script.CalibrationPlan(path)


def test_unknown_motion_keys_are_rejected(
    calibration_script, tmp_path: Path
) -> None:
    path = write_calibration_config(tmp_path, motion={"maximum_absolute_degree": 90})

    with pytest.raises(MotionConfigError, match="unknown configuration key"):
        calibration_script.CalibrationPlan(path)


def test_the_moved_pulse_period_key_is_reported_as_deprecated(
    calibration_script, tmp_path: Path
) -> None:
    path = write_calibration_config(tmp_path, motion={"pulse_half_period_us": 5000})

    plan = calibration_script.CalibrationPlan(path)

    assert any("timing.pulse_half_period_us" in w for w in plan.deprecations)


def test_the_shipped_calibration_config_loads(
    calibration_script, package_root: Path
) -> None:
    plan = calibration_script.CalibrationPlan(
        package_root / "configs" / "99_needle_calibration.yaml"
    )

    assert [d for _, d, _ in plan.trials] == [90.0, 180.0, 360.0]
    assert plan.measure_return_error is True
    assert plan.require_typed_confirmation is True
    assert plan.deprecations == []


# --- Measured physical return error -----------------------------------------


@pytest.mark.parametrize("value", [0.0, 0.05, -0.05, 1.5, -1.5])
def test_a_signed_return_error_including_zero_is_accepted(value: float) -> None:
    assert validate_return_error(value) == pytest.approx(value)


@pytest.mark.parametrize("bad", ["0.1", None, True, 5000.0, -5000.0])
def test_an_invalid_return_error_is_refused(bad: object) -> None:
    with pytest.raises(CalibrationError):
        validate_return_error(bad)


def test_return_errors_are_recorded_in_yaml_and_csv(tmp_path: Path) -> None:
    points = [
        CalibrationPoint(
            trial=index,
            repetition=1,
            degrees=degrees,
            steps=int(degrees / 360 * 800),
            measured_mm=degrees * 0.0125,
            return_error_mm=offset,
            measured_at="2026-08-06T10:00:00Z",
        )
        for index, (degrees, offset) in enumerate(
            [(90.0, 0.02), (180.0, 0.03), (360.0, 0.01)], start=1
        )
    ]
    fit = build_calibration_fit(points)

    document = build_results_document(
        fit,
        config_path="configs/99_needle_calibration.yaml",
        started_at="2026-08-06T10:00:00Z",
        finished_at="2026-08-06T10:10:00Z",
    )
    yaml_path = write_results_yaml(tmp_path / "r.yaml", document)
    csv_path = write_results_csv(tmp_path / "r.csv", fit)

    loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert [o["return_error_mm"] for o in loaded["observations"]] == [0.02, 0.03, 0.01]
    assert [o["repetition"] for o in loaded["observations"]] == [1, 1, 1]
    assert loaded["observations"][0]["measured_at"] == "2026-08-06T10:00:00Z"

    csv_text = csv_path.read_text(encoding="utf-8")
    assert "return_error_mm" in csv_text.splitlines()[0]
    assert "0.020000" in csv_text


def test_a_skipped_return_measurement_is_recorded_as_null(tmp_path: Path) -> None:
    points = [
        CalibrationPoint(
            trial=1, degrees=90.0, steps=200, measured_mm=1.125, return_error_mm=None
        )
    ]
    fit = build_calibration_fit(points)

    path = write_results_csv(tmp_path / "r.csv", fit)

    assert measured_return_errors(points) == []
    # An omitted measurement is an empty CSV cell, never a silent zero.
    assert path.read_text(encoding="utf-8").splitlines()[1].count(",,") >= 1


def test_consistent_lost_motion_is_warned_about_as_measured() -> None:
    points = [
        CalibrationPoint(
            trial=i, degrees=d, steps=int(d / 360 * 800),
            measured_mm=d * 0.0125, return_error_mm=0.4,
        )
        for i, d in enumerate([90.0, 180.0, 360.0], start=1)
    ]

    warnings = return_error_warnings(points)

    assert any("MEASURED return error" in w for w in warnings)
    assert any("same direction" in w for w in warnings)
    assert any("backlash" in w for w in warnings)


def test_a_small_return_error_raises_no_warning() -> None:
    points = [
        CalibrationPoint(
            trial=i, degrees=d, steps=int(d / 360 * 800),
            measured_mm=d * 0.0125, return_error_mm=0.001,
        )
        for i, d in enumerate([90.0, 180.0, 360.0], start=1)
    ]

    assert return_error_warnings(points) == []


def test_omitting_the_return_measurement_is_itself_flagged() -> None:
    points = [
        CalibrationPoint(trial=1, degrees=90.0, steps=200, measured_mm=1.125)
    ]

    warnings = return_error_warnings(points)

    assert any("never observed" in w for w in warnings)


def test_return_warnings_reach_the_main_warning_list() -> None:
    points = [
        CalibrationPoint(
            trial=i, degrees=d, steps=int(d / 360 * 800),
            measured_mm=d * 0.0125, return_error_mm=0.5,
        )
        for i, d in enumerate([90.0, 180.0, 360.0], start=1)
    ]

    assert any(
        "MEASURED return error" in w
        for w in nonlinearity_warnings(build_calibration_fit(points))
    )


# --- Measurement prompt exception ordering ----------------------------------


def test_the_prompt_shows_the_real_error_for_a_non_positive_measurement(
    calibration_script, monkeypatch, capsys
) -> None:
    """CalibrationError IS a ValueError, so handler order decides the message."""
    answers = iter(["-5", "0", "900", "2.5"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = calibration_script.prompt_for_measurement(1, 90.0)

    printed = capsys.readouterr().out
    assert result == 2.5
    assert "direction is already known" in printed   # the -5 and 0 diagnostics
    assert "plausibility limit" in printed           # the 900 diagnostic
    assert "Not a number" not in printed


def test_the_prompt_still_reports_genuine_non_numbers(
    calibration_script, monkeypatch, capsys
) -> None:
    answers = iter(["banana", "2.5"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    calibration_script.prompt_for_measurement(1, 90.0)

    assert "Not a number" in capsys.readouterr().out


def test_the_return_prompt_accepts_zero_and_skip(
    calibration_script, monkeypatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "0")
    assert calibration_script.prompt_for_return_error(1) == 0.0

    monkeypatch.setattr("builtins.input", lambda _prompt: "skip")
    assert calibration_script.prompt_for_return_error(1) is None

    monkeypatch.setattr("builtins.input", lambda _prompt: "-0.03")
    assert calibration_script.prompt_for_return_error(1) == pytest.approx(-0.03)


# --- Atomic writes -----------------------------------------------------------


def test_a_failed_write_leaves_the_previous_calibration_intact(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "needle_calibration.yaml"
    original = "calibration:\n  calibrated: true\n  mm_per_step: 0.005625\n"
    target.write_text(original, encoding="utf-8")

    def explode(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)

    with pytest.raises(OSError):
        atomic_write_text(target, "calibration:\n  calibrated: WRECKED\n")

    assert target.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp")), "temporary file was left behind"


def test_overwriting_the_calibration_keeps_a_backup(tmp_path: Path) -> None:
    points = [
        CalibrationPoint(
            trial=i, degrees=d, steps=int(d / 360 * 800), measured_mm=d * 0.0125,
            return_error_mm=0.0,
        )
        for i, d in enumerate([90.0, 180.0, 360.0], start=1)
    ]
    fit = build_calibration_fit(points)
    target = tmp_path / "needle_calibration.yaml"
    target.write_text("calibration:\n  calibrated: false\n", encoding="utf-8")

    from calibration_utils import build_calibration_document

    write_calibration_document(
        target, build_calibration_document(fit, source_results_file="x.yaml")
    )

    backup = target.with_suffix(".yaml.bak")
    assert backup.is_file()
    assert "calibrated: false" in backup.read_text(encoding="utf-8")
    assert load_calibration(target).calibrated is True


def test_atomic_write_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "out.yaml"

    atomic_write_text(target, "hello: world\n")

    assert target.read_text(encoding="utf-8") == "hello: world\n"
