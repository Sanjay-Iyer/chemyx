"""Script 99: measure how far the needle moves for a known motor rotation.

Special/diagnostic scripts use high numbers (99, 98, ...). Ordinary needle
motion uses low sequential numbers (01, 02, ...). See README.md.

Each trial is an independent round trip that starts and ends at commanded zero:

    commanded zero -> +N degrees -> measure -> -N degrees -> measure -> zero

Trials are never chained, so a measurement error in one trial cannot contaminate
the next one's starting point.

Run:

    python .\\99_needle_calibration.py --config .\\configs\\99_needle_calibration.yaml
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import serial

from calibration_utils import (
    CalibrationError,
    CalibrationFit,
    CalibrationPoint,
    build_calibration_document,
    build_calibration_fit,
    build_results_document,
    describe_trial,
    measured_return_errors,
    nonlinearity_warnings,
    parse_trial_degrees,
    validate_measurement,
    validate_return_error,
    write_calibration_document,
    write_results_csv,
    write_results_yaml,
)
from motion_utils import (
    FIRMWARE_MAX_ABSOLUTE_STEPS,
    MotionConfigError,
    TimingSettings,
    countdown_pause,
    degrees_to_steps,
    iso_timestamp,
    load_yaml_mapping,
    parse_serial_settings,
    parse_steps_per_revolution,
    parse_timing_settings,
    print_no_feedback_warning,
    reject_unknown_keys,
    require_bool,
    require_integer,
    require_number,
    require_section,
    require_typed_run_confirmation,
    send_move,
    unique_output_path,
)
from serial_test_utils import (
    ArduinoTestError,
    StopOutcome,
    open_arduino_serial,
    print_motion_safety_checklist,
    report_stop_outcome,
    request_software_stop,
    send_command_and_wait,
    serial_error_message,
)


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "configs" / "99_needle_calibration.yaml"
DEFAULT_CALIBRATION_FILE = HERE / "configs" / "needle_calibration.yaml"
RESULTS_DIRECTORY = HERE / "calibration_results"

MOTION_KEYS = (
    "pause_before_measurement_seconds",
    "pause_after_return_seconds",
    "require_typed_confirmation",
    "maximum_absolute_degrees",
)
MOTION_DEPRECATED = {
    "pulse_half_period_us": "moved to the timing: section (timing.pulse_half_period_us)"
}
CALIBRATION_KEYS = (
    "trial_degrees",
    "repetitions",
    "fit_through_origin",
    "update_authoritative_calibration_file",
    "measure_return_error",
)
TOP_LEVEL_KEYS = ("serial", "driver", "motion", "timing", "calibration")


@dataclass
class CalibrationState:
    """Live bookkeeping shared between the executor and the error handlers."""

    commanded_position: int = 0
    motion_in_progress: bool = False
    any_command_written: bool = False
    points: list[CalibrationPoint] = field(default_factory=list)
    stop_outcome: StopOutcome | None = None


class CalibrationPlan:
    """Everything validated from the YAML, before any port is opened."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        config = load_yaml_mapping(config_path)
        label = config_path.name
        reject_unknown_keys(config, TOP_LEVEL_KEYS, where=label)

        self.serial = parse_serial_settings(config, where=f"{label}:serial")
        self.steps_per_revolution = parse_steps_per_revolution(
            config, where=f"{label}:driver"
        )
        self.timing: TimingSettings = parse_timing_settings(
            config, where=f"{label}:timing"
        )

        motion = require_section(config, "motion")
        where_motion = f"{label}:motion"
        self.deprecations = reject_unknown_keys(
            motion, MOTION_KEYS, where=where_motion, deprecated=MOTION_DEPRECATED
        )
        self.pause_before_measurement_seconds = require_number(
            motion, "pause_before_measurement_seconds", where=where_motion, minimum=0.0
        )
        self.pause_after_return_seconds = require_number(
            motion, "pause_after_return_seconds", where=where_motion, minimum=0.0
        )
        self.require_typed_confirmation = require_bool(
            motion, "require_typed_confirmation", where=where_motion
        )
        self.maximum_absolute_degrees = require_number(
            motion, "maximum_absolute_degrees", where=where_motion
        )

        calibration = require_section(config, "calibration")
        where_cal = f"{label}:calibration"
        reject_unknown_keys(calibration, CALIBRATION_KEYS, where=where_cal)
        self.repetitions = require_integer(
            calibration, "repetitions", where=where_cal, minimum=1
        )
        self.fit_through_origin = require_bool(
            calibration, "fit_through_origin", where=where_cal
        )
        self.update_authoritative_calibration_file = require_bool(
            calibration, "update_authoritative_calibration_file", where=where_cal
        )
        self.measure_return_error = (
            require_bool(calibration, "measure_return_error", where=where_cal)
            if "measure_return_error" in calibration
            else True
        )

        self.trial_degrees = parse_trial_degrees(
            config,
            steps_per_revolution=self.steps_per_revolution,
            maximum_absolute_degrees=self.maximum_absolute_degrees,
        )

        # Expand repetitions into the flat, ordered list actually executed.
        # The list length is authoritative; nothing else counts the trials.
        self.trials: list[tuple[int, float, int]] = []
        for repetition in range(1, self.repetitions + 1):
            for degrees in self.trial_degrees:
                self.trials.append(
                    (
                        repetition,
                        degrees,
                        degrees_to_steps(degrees, self.steps_per_revolution),
                    )
                )

    def print_plan(self) -> None:
        print("\nPlanned calibration sequence")
        print("=" * 78)
        print(f"Config file:          {self.config_path}")
        print(f"Serial port:          {self.serial.port} at {self.serial.baud} baud")
        print(f"Steps per revolution: {self.steps_per_revolution}")
        print(f"Repetitions:          {self.repetitions}")
        print(f"Trials in total:      {len(self.trials)}")
        print(
            "Fit method:           "
            + ("through origin" if self.fit_through_origin else "free intercept")
        )
        print(
            "Measure return:       "
            + ("yes" if self.measure_return_error else "NO (backlash only inferred)")
        )
        print("-" * 78)
        total_seconds = 0.0
        for index, (repetition, degrees, steps) in enumerate(self.trials, start=1):
            forward_timeout = self.timing.timeout_seconds(steps)
            total_seconds += 2 * self.timing.expected_motion_seconds(steps)
            total_seconds += (
                self.pause_before_measurement_seconds + self.pause_after_return_seconds
            )
            print(
                f"Trial {index:>2} (rep {repetition}): "
                + describe_trial(degrees, steps, self.steps_per_revolution)
            )
            print(
                f"           each leg: about "
                f"{self.timing.expected_motion_seconds(steps):.1f} s of motion, "
                f"timeout {forward_timeout:.1f} s"
            )
        print("-" * 78)
        print(
            f"Total expected motion plus pauses: {total_seconds:.1f} s "
            "(excluding your measuring time)"
        )

    def verify_every_trial_returns_to_zero(self) -> None:
        """Walk the ACTUAL leg-by-leg sequence and prove it closes at zero.

        This tracks a running position across every leg of every trial rather
        than asserting that ``+n - n == 0``, so it can genuinely fail if the
        planned sequence is ever changed to something that does not close.
        """
        position = 0
        peak = 0
        for index, (repetition, degrees, steps) in enumerate(self.trials, start=1):
            for leg_name, leg in (("forward", steps), ("return", -steps)):
                position += leg
                peak = max(peak, abs(position))
                if abs(position) > FIRMWARE_MAX_ABSOLUTE_STEPS:
                    raise CalibrationError(
                        f"Trial {index} (rep {repetition}, {degrees:g} deg) "
                        f"reaches {position:+d} steps after its {leg_name} leg, "
                        f"beyond the {FIRMWARE_MAX_ABSOLUTE_STEPS}-step firmware "
                        "limit."
                    )
            if position != 0:
                raise CalibrationError(
                    f"Trial {index} (rep {repetition}, {degrees:g} deg) does not "
                    f"return to commanded zero: ended at {position:+d} steps."
                )
        if position != 0:
            raise CalibrationError(
                f"The calibration sequence ends at {position:+d} commanded steps, "
                "not zero."
            )
        print(
            f"Checked: all {len(self.trials)} trials close at commanded zero; "
            f"peak excursion {peak} steps."
        )


def _ask(prompt: str) -> str:
    """Read one line, turning a closed stdin into a clean cancellation."""
    try:
        return input(prompt).strip()
    except EOFError as error:
        raise CalibrationError(
            "Cancelled: no input could be read (stdin is closed). The needle "
            "may be away from its starting position -- check it deliberately "
            "before running anything else."
        ) from error


def prompt_for_measurement(trial_number: int, degrees: float) -> float:
    """Ask for one forward displacement in millimetres until it is valid."""
    while True:
        raw = _ask(
            f"Trial {trial_number}: measured needle displacement for "
            f"{degrees:g} deg forward, in mm (or 'abort'): "
        )
        if raw.lower() == "abort":
            raise CalibrationError("Cancelled: operator aborted at the measurement prompt.")
        # CalibrationError is a ValueError subclass, so it MUST be caught first
        # or the generic "not a number" branch would swallow its real message.
        try:
            return validate_measurement(float(raw))
        except CalibrationError as error:
            print(f"  {error}")
        except ValueError:
            print("  Not a number. Enter a decimal value such as 2.5")


def prompt_for_return_error(trial_number: int) -> float | None:
    """Ask how far the needle actually is from its starting mark after returning.

    This is the only directly measured evidence that commanded zero and
    physical zero are the same place.
    """
    while True:
        raw = _ask(
            f"Trial {trial_number}: with the needle back at commanded zero, how "
            "far is it from its starting mark, in mm?\n"
            "  (signed: + = past the mark in the forward direction, "
            "0 = exactly back, 'skip' to omit): "
        )
        if raw.lower() == "skip":
            print("  Skipped. Backlash can then only be inferred, never observed.")
            return None
        if raw.lower() == "abort":
            raise CalibrationError("Cancelled: operator aborted at the return prompt.")
        try:
            return validate_return_error(float(raw))
        except CalibrationError as error:
            print(f"  {error}")
        except ValueError:
            print("  Not a number. Enter a signed decimal such as 0, 0.05, or -0.02")


def run_trials(
    board: object, plan: CalibrationPlan, state: CalibrationState
) -> None:
    """Execute every round trip, collecting one or two measurements per trial."""
    for trial_number, (repetition, degrees, steps) in enumerate(plan.trials, start=1):
        print(
            f"\n--- Trial {trial_number} of {len(plan.trials)} "
            f"(repetition {repetition}): {degrees:g} deg ---"
        )
        print(f"Commanded position before this trial: {state.commanded_position:+d} steps")

        _move(board, steps, plan, state)
        state.commanded_position += steps
        print(f"Commanded position after forward move: {state.commanded_position:+d} steps")

        countdown_pause(
            plan.pause_before_measurement_seconds,
            "Letting the mechanism settle before measurement",
        )
        measured_mm = prompt_for_measurement(trial_number, degrees)

        _move(board, -steps, plan, state)
        state.commanded_position -= steps
        print(f"Commanded position after return move: {state.commanded_position:+d} steps")

        if state.commanded_position != 0:
            raise CalibrationError(
                f"Trial {trial_number} ended at {state.commanded_position:+d} "
                "commanded steps instead of zero. Stopping before the error "
                "accumulates."
            )
        print(
            "Commanded position is back at zero. This does NOT prove the needle "
            "physically returned to its starting point."
        )

        return_error_mm = None
        if plan.measure_return_error:
            countdown_pause(
                plan.pause_after_return_seconds, "Letting the mechanism settle"
            )
            return_error_mm = prompt_for_return_error(trial_number)
        else:
            countdown_pause(
                plan.pause_after_return_seconds, "Pausing before the next trial"
            )

        state.points.append(
            CalibrationPoint(
                trial=trial_number,
                repetition=repetition,
                degrees=degrees,
                steps=steps,
                measured_mm=measured_mm,
                return_error_mm=return_error_mm,
                measured_at=iso_timestamp(),
            )
        )


def _move(
    board: object, signed_steps: int, plan: CalibrationPlan, state: CalibrationState
) -> None:
    """One MOVE, with the abort path attached while the port is still open."""
    state.motion_in_progress = True
    state.any_command_written = True
    try:
        send_move(
            board,
            signed_steps,
            timing=plan.timing,
            send_command_and_wait=send_command_and_wait,
        )
    except KeyboardInterrupt:
        state.stop_outcome = request_software_stop(
            board, timeout=plan.timing.stop_timeout_seconds
        )
        raise
    finally:
        state.motion_in_progress = False


def report_fit(fit: CalibrationFit, warnings: list[str]) -> None:
    """Print the fitted factors, per-point residuals, and any warnings."""
    print("\nCalibration result")
    print("=" * 92)
    print(f"Fit method:      {fit.fit_method}")
    print(f"mm per degree:   {fit.mm_per_degree:.9g}")
    print(f"mm per step:     {fit.mm_per_step:.9g}")
    print(f"steps per mm:    {fit.steps_per_mm:.9g}")
    print(f"R-squared:       {fit.r_squared:.6f}")
    print(f"Measurements:    {fit.number_of_measurements}")
    print("-" * 92)
    header = (
        f"{'trial':>5} {'rep':>4} {'degrees':>9} {'steps':>7} {'measured mm':>12} "
        f"{'predicted mm':>13} {'residual mm':>12} {'return err mm':>14}"
    )
    print(header)
    print("-" * len(header))
    for point, predicted, residual in zip(fit.points, fit.predicted_mm, fit.residuals_mm):
        return_text = (
            "not measured"
            if point.return_error_mm is None
            else f"{point.return_error_mm:+.4f}"
        )
        print(
            f"{point.trial:>5} {point.repetition:>4} {point.degrees:>9g} "
            f"{point.steps:>7} {point.measured_mm:>12.4f} {predicted:>13.4f} "
            f"{residual:>+12.4f} {return_text:>14}"
        )
    print("-" * 92)

    errors = measured_return_errors(fit.points)
    if errors:
        mean_absolute = sum(abs(value) for value in errors) / len(errors)
        print(
            f"\nMEASURED physical return error: mean magnitude "
            f"{mean_absolute:.4f} mm over {len(errors)} trial(s). This is direct "
            "evidence of lost motion, unlike the fitted intercept below."
        )
    print(
        f"Fitted intercept of an unconstrained line: "
        f"{fit.free_fit_intercept_mm:+.4f} mm (inferred, not measured)."
    )

    if warnings:
        print("\nWARNINGS about this calibration:")
        for warning in warnings:
            print(f"  ! {warning}")
        print(
            "\nA warned calibration is still saved to calibration_results/, but "
            "think carefully before making it authoritative."
        )
    else:
        print("\nNo linearity or consistency warnings were raised.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YAML-driven needle calibration for the DM542S rig."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"Calibration configuration YAML (default: {DEFAULT_CONFIG}).",
    )
    parser.add_argument(
        "--calibration-file",
        default=str(DEFAULT_CALIBRATION_FILE),
        help=(
            "Authoritative calibration file to update on success "
            f"(default: {DEFAULT_CALIBRATION_FILE})."
        ),
    )
    args = parser.parse_args()

    # ---- Everything below happens before the serial port is opened ----------
    try:
        plan = CalibrationPlan(Path(args.config))
        for warning in plan.deprecations:
            print(f"DEPRECATED: {warning}")
        plan.print_plan()
        plan.verify_every_trial_returns_to_zero()
    except MotionConfigError as error:
        print(f"FAIL: configuration rejected, serial port not opened.\n{error}")
        raise SystemExit(1) from error

    print_no_feedback_warning()
    print_motion_safety_checklist()
    print(
        "\nFor each trial: the needle moves forward, you measure the displacement, "
        "then it moves back by the identical step count and you measure how far "
        "it is from the starting mark. Enter the forward displacement as a "
        "positive magnitude; the return error is signed."
    )
    print(
        "During motion, Ctrl+C sends a software STOP on this same connection. "
        "It halts the pulse train but leaves the DM542S energised."
    )

    started_at = iso_timestamp()
    state = CalibrationState()

    try:
        if plan.require_typed_confirmation:
            require_typed_run_confirmation(
                "\nType exactly RUN to start the calibration sequence: "
            )
        with open_arduino_serial(
            plan.serial.port,
            plan.serial.baud,
            reset_wait=plan.serial.reset_wait_seconds,
        ) as board:
            run_trials(board, plan, state)

    except KeyboardInterrupt:
        if not state.any_command_written:
            print("\nCANCELLED: no motion command was sent. Nothing moved.")
            raise SystemExit(130)
        if state.stop_outcome is not None:
            report_stop_outcome(state.stop_outcome, state.commanded_position)
        else:
            print("\nCANCELLED between moves. No move was in progress.")
            print(f"  Last commanded position: {state.commanded_position:+d} steps")
        if state.commanded_position != 0:
            print(
                "\n  THE NEEDLE WAS LEFT AWAY FROM ITS STARTING POSITION "
                f"({state.commanded_position:+d} commanded steps).\n"
                "  Return it deliberately before running anything else."
            )
        _report_discarded(state)
        raise SystemExit(130)

    except serial.SerialException as error:
        print(f"FAIL: {serial_error_message(error, plan.serial.port)}")
        _report_discarded(state)
        raise SystemExit(1) from error

    except (ArduinoTestError, MotionConfigError) as error:
        print(f"FAIL: {error}")
        if state.commanded_position != 0:
            print(
                f"  The needle was left at {state.commanded_position:+d} commanded "
                "steps. Return it deliberately before running anything else."
            )
        _report_discarded(state)
        raise SystemExit(1) from error

    # Placed AFTER except KeyboardInterrupt so it can never swallow an interrupt.
    except Exception as error:  # noqa: BLE001 - last-resort operator safety net
        print(f"FAIL: unexpected error during the run: {error!r}")
        print(
            "If motion is still occurring, turn off DM542S 24 V power "
            "immediately. Physical position is unknown."
        )
        _report_discarded(state)
        raise SystemExit(1) from error

    # ---- Fit, save raw results, then ask before touching the authority ------
    finished_at = iso_timestamp()
    try:
        fit = build_calibration_fit(
            state.points,
            steps_per_revolution=plan.steps_per_revolution,
            through_origin=plan.fit_through_origin,
        )
    except CalibrationError as error:
        print(f"FAIL: the measurements could not be fitted.\n{error}")
        raise SystemExit(1) from error

    warnings = nonlinearity_warnings(fit)
    report_fit(fit, warnings)

    results_yaml = unique_output_path(RESULTS_DIRECTORY, "needle_calibration", ".yaml")
    results_csv = results_yaml.with_suffix(".csv")
    write_results_yaml(
        results_yaml,
        build_results_document(
            fit,
            config_path=plan.config_path,
            started_at=started_at,
            finished_at=finished_at,
            metadata={
                "serial_port": plan.serial.port,
                "repetitions": plan.repetitions,
                "trial_degrees": plan.trial_degrees,
                "return_error_measured": plan.measure_return_error,
            },
        ),
    )
    write_results_csv(results_csv, fit)
    print(f"\nRaw observations saved to:\n  {results_yaml}\n  {results_csv}")

    if not plan.update_authoritative_calibration_file:
        print(
            "\ncalibration.update_authoritative_calibration_file is false, so "
            f"{args.calibration_file} was left unchanged."
        )
        return

    print(
        f"\nAbout to overwrite the authoritative calibration file:\n"
        f"  {args.calibration_file}\n"
        "Every later millimetre-mode move will use these numbers. The previous "
        "version is kept alongside it as a .bak file."
    )
    try:
        answer = input("Type exactly UPDATE to overwrite it, or anything else to skip: ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
        print()
    if answer != "UPDATE":
        print(
            "Skipped. The authoritative calibration file was NOT changed.\n"
            f"The timestamped raw results remain at {results_yaml}"
        )
        return

    try:
        relative_source = results_yaml.relative_to(HERE).as_posix()
    except ValueError:
        relative_source = str(results_yaml)

    write_calibration_document(
        args.calibration_file,
        build_calibration_document(
            fit, source_results_file=relative_source, created_at=finished_at
        ),
    )
    print(f"Updated {args.calibration_file}")
    print(
        "\nReminder: this is a software calibration derived from hand "
        "measurements. It assumes no step was missed during the run."
    )


def _report_discarded(state: CalibrationState) -> None:
    if state.points:
        print(
            f"{len(state.points)} completed measurement(s) were discarded: a "
            "partial calibration is not fitted."
        )


if __name__ == "__main__":
    main()
