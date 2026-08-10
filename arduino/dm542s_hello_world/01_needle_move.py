"""Script 01: execute an ordered sequence of needle moves from a YAML file.

Ordinary needle-motion scripts use low sequential numbers (01, 02, 03, ...).
Calibration, maintenance, and diagnostic scripts use high numbers (99, 98, ...).
See README.md and OPERATOR_GUIDE.md.

Every move is converted to whole signed steps and the entire plan is validated
*before* the COM port is opened. If validation fails, nothing is opened and
nothing moves.

Ctrl+C during a move sends a software STOP on this process's own serial
connection. That halts the pulse train; it does NOT de-energise the DM542S and
is NOT an emergency stop.

Run:

    python .\\01_needle_move.py --config .\\configs\\01_needle_move.yaml
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from pathlib import Path

import serial
import yaml

from calibration_utils import CalibrationError, load_calibration
from motion_utils import (
    MODE_MM,
    PLAN_TABLE_WIDTH,
    MotionConfigError,
    MoveConfig,
    PlannedMove,
    TimingSettings,
    atomic_write_text,
    build_move_plan,
    count_backward_moves,
    count_forward_moves,
    countdown_pause,
    describe_rounding,
    format_plan_table,
    iso_timestamp,
    load_move_config,
    net_steps,
    plan_excursion,
    print_no_feedback_warning,
    require_typed_run_confirmation,
    send_move,
    total_backward_steps,
    total_expected_runtime_seconds,
    total_forward_steps,
    unique_output_path,
    validate_plan_within_limits,
    validate_timeouts_cover_plan,
    validate_zero_net_steps,
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
DEFAULT_CONFIG = HERE / "configs" / "01_needle_move.yaml"
DEFAULT_CALIBRATION_FILE = HERE / "configs" / "needle_calibration.yaml"
LOG_DIRECTORY = HERE / "calibration_results"


@dataclass
class ExecutionState:
    """Live bookkeeping shared between the executor and the error handlers."""

    commanded_position: int = 0
    # True only between writing a MOVE and receiving its DONE line. This is the
    # ONLY window in which a software STOP is meaningful.
    motion_in_progress: bool = False
    any_command_written: bool = False
    log: list[dict] = field(default_factory=list)
    stop_outcome: StopOutcome | None = None


def print_preflight(
    plan: list[PlannedMove],
    config: MoveConfig,
    *,
    mm_per_step: float | None,
) -> None:
    """Show the full resolved plan, before the COM port is opened."""
    timing = config.timing
    print("\nPreflight movement plan")
    print("=" * PLAN_TABLE_WIDTH)
    print(f"Config file:          {config.path}")
    print(f"Movement mode:        {config.execution.movement_mode}")
    print(f"Steps per revolution: {config.steps_per_revolution}")
    if mm_per_step is not None:
        print(f"mm per step:          {mm_per_step:.9g}")
    print(
        f"Pulse timing:         {timing.pulse_half_period_us:g} us per half "
        f"pulse => {1.0 / timing.pulse_period_seconds:.1f} steps/s"
    )
    print("-" * PLAN_TABLE_WIDTH)
    print(format_plan_table(plan))
    print("-" * PLAN_TABLE_WIDTH)

    print("\nRounding detail for each move:")
    for move in plan:
        print(f"\n  Move {move.number} ({move.name})")
        for line in describe_rounding(move).splitlines():
            print(f"    {line}")

    lowest, highest = plan_excursion(plan)
    runtime = total_expected_runtime_seconds(plan)
    print("\nPlan summary")
    print(f"  moves in sequence:            {len(plan)}")
    print(f"  forward moves:                {count_forward_moves(plan)}")
    print(f"  backward moves:               {count_backward_moves(plan)}")
    print(f"  total forward steps:          {total_forward_steps(plan)}")
    print(f"  total backward steps:         {total_backward_steps(plan)}")
    print(f"  final net commanded steps:    {net_steps(plan):+d}")
    print(f"  minimum cumulative position:  {lowest:+d} steps")
    print(f"  maximum cumulative position:  {highest:+d} steps")
    print(f"  total expected runtime:       {runtime:.1f} s (motion plus pauses)")
    if config.software_limits.enabled:
        print(
            f"  relative software bounds:     "
            f"[{config.software_limits.minimum_steps:+d}, "
            f"{config.software_limits.maximum_steps:+d}] steps "
            "(RELATIVE to the start position; this rig has no homing)"
        )
    else:
        print(
            "  relative software bounds:     DISABLED -- intermediate excursion "
            "is not checked"
        )


def execute_plan(
    board: object,
    plan: list[PlannedMove],
    timing: TimingSettings,
    state: ExecutionState,
) -> None:
    """Run the moves one at a time, waiting for each DONE MOVE line.

    Owns the abort path: this is the only scope that holds both the open serial
    connection and the knowledge of whether a move is actually under way.
    """
    for move in plan:
        print(f"\n--- Move {move.number} of {len(plan)}: {move.name} ---")
        print(
            f"{move.direction} {move.requested_value:g} {move.unit} "
            f"= {move.signed_steps:+d} steps"
        )

        state.motion_in_progress = True
        state.any_command_written = True
        try:
            responses = send_move(
                board,
                move.signed_steps,
                timing=timing,
                send_command_and_wait=send_command_and_wait,
            )
        except KeyboardInterrupt:
            # The motor is still stepping. Abort on THIS connection, now, while
            # the port is still open -- a second process cannot take it over.
            state.stop_outcome = request_software_stop(
                board, timeout=timing.stop_timeout_seconds
            )
            raise
        finally:
            state.motion_in_progress = False

        state.commanded_position += move.signed_steps
        if state.commanded_position != move.cumulative_steps:
            raise MotionConfigError(
                f"Internal bookkeeping mismatch after move {move.number}: "
                f"executed {state.commanded_position:+d} steps but the plan "
                f"expected {move.cumulative_steps:+d}."
            )

        print(f"Cumulative commanded position: {state.commanded_position:+d} steps")
        state.log.append(
            {
                "move": move.number,
                "name": move.name,
                "direction": move.direction,
                "requested_value": move.requested_value,
                "unit": move.unit,
                "commanded_steps": move.signed_steps,
                "cumulative_commanded_steps": state.commanded_position,
                "timeout_seconds": round(move.timeout_seconds, 3),
                "responses": responses,
                "completed_at": iso_timestamp(),
            }
        )

        if move.number < len(plan):
            countdown_pause(move.pause_after_seconds, "Pausing before the next move")


def build_log_document(
    args: argparse.Namespace,
    config: MoveConfig,
    plan: list[PlannedMove],
    state: ExecutionState,
    started_at: str,
    outcome: str,
    calibration_summary: dict | None,
) -> dict:
    """Assemble the execution record, including the fully resolved settings."""
    executed = sum(entry["commanded_steps"] for entry in state.log)
    lowest, highest = plan_excursion(plan)
    return {
        "run": {
            "started_at": started_at,
            "finished_at": iso_timestamp(),
            "outcome": outcome,
            "config_file": str(args.config),
            "calibration": calibration_summary,
        },
        # Everything needed to reproduce the run, not just a file path.
        "resolved_configuration": {
            "serial": asdict(config.serial),
            "driver": {"steps_per_revolution": config.steps_per_revolution},
            "execution": asdict(config.execution),
            "timing": asdict(config.timing),
            "software_limits": asdict(config.software_limits),
        },
        "plan": [
            {
                "move": move.number,
                "name": move.name,
                "direction": move.direction,
                "requested_value": move.requested_value,
                "unit": move.unit,
                "exact_steps": round(move.exact_steps, 4),
                "commanded_steps": move.signed_steps,
                "cumulative_commanded_steps": move.cumulative_steps,
                "pause_after_seconds": move.pause_after_seconds,
                "expected_seconds": round(move.expected_seconds, 3),
                "timeout_seconds": round(move.timeout_seconds, 3),
            }
            for move in plan
        ],
        "executed": state.log,
        "software_stop": (
            state.stop_outcome.as_log_entry()
            if state.stop_outcome is not None
            else {"attempted": False, "summary": "no software STOP was needed"}
        ),
        "summary": {
            "moves_planned": len(plan),
            "moves_executed": len(state.log),
            "total_forward_steps": total_forward_steps(plan),
            "total_backward_steps": total_backward_steps(plan),
            "minimum_cumulative_steps": lowest,
            "maximum_cumulative_steps": highest,
            "final_commanded_steps": executed,
            "zero_net_commanded": executed == 0 and outcome == "completed",
            "physical_position_known": False,
            "position_basis": (
                "commanded steps only; no encoder, no limit switches, no homing"
            ),
        },
    }


def save_log(document: dict) -> Path:
    path = unique_output_path(LOG_DIRECTORY, "needle_move", ".yaml")
    return atomic_write_text(
        path, yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YAML-driven needle movement sequence for the DM542S rig."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"Movement configuration YAML (default: {DEFAULT_CONFIG}).",
    )
    parser.add_argument(
        "--calibration-file",
        default=str(DEFAULT_CALIBRATION_FILE),
        help=(
            "Authoritative calibration file, used only when movement_mode is "
            f"mm (default: {DEFAULT_CALIBRATION_FILE})."
        ),
    )
    args = parser.parse_args()

    # ---- Everything below happens before the serial port is opened ----------
    mm_per_step: float | None = None
    calibration_summary: dict | None = None
    try:
        config = load_move_config(args.config)
        for warning in config.deprecations:
            print(f"DEPRECATED: {warning}")

        if config.execution.movement_mode == MODE_MM:
            calibration = load_calibration(args.calibration_file).require_calibrated()
            mm_per_step = calibration.mm_per_step
            calibration_summary = {
                "source": str(args.calibration_file),
                "created_at": calibration.created_at,
                "mm_per_step": calibration.mm_per_step,
                "fit_method": calibration.fit_method,
            }
            print(
                f"Loaded calibration from {args.calibration_file}\n"
                f"  mm_per_step {calibration.mm_per_step:.9g}, "
                f"created {calibration.created_at}"
            )
        else:
            print(
                f"movement_mode is {config.execution.movement_mode!r}: no "
                "millimetre calibration is used."
            )

        plan = build_move_plan(config, mm_per_step=mm_per_step)
        print_preflight(plan, config, mm_per_step=mm_per_step)

        validate_timeouts_cover_plan(plan, config.timing, where=f"{config.path.name}:timing")

        remaining = validate_zero_net_steps(
            plan, required=config.execution.require_zero_net_steps
        )
        if config.execution.require_zero_net_steps:
            print(
                "\nChecked: the plan returns to exactly zero commanded steps "
                "after rounding."
            )
        elif remaining != 0:
            print(
                f"\nNOTE: require_zero_net_steps is false and this plan ends "
                f"{remaining:+d} steps away from its starting point."
            )

        validate_plan_within_limits(plan, config.software_limits)
        if config.software_limits.enabled:
            print(
                "Checked: every intermediate cumulative position stays inside "
                "the relative software bounds."
            )
    except (MotionConfigError, CalibrationError) as error:
        print(f"\nFAIL: configuration rejected, serial port not opened.\n{error}")
        raise SystemExit(1) from error

    print_no_feedback_warning()
    print_motion_safety_checklist()
    print(
        "\nDuring motion, Ctrl+C sends a software STOP on this same connection. "
        "It halts the pulse train but leaves the DM542S energised. If the motor "
        "does not stop, turn off the 24 V supply."
    )

    started_at = iso_timestamp()
    state = ExecutionState()
    outcome = "cancelled_before_motion"
    exit_code = 0

    try:
        if config.execution.require_typed_confirmation:
            require_typed_run_confirmation(
                f"\nType exactly RUN to execute {len(plan)} moves: "
            )
        with open_arduino_serial(
            config.serial.port,
            config.serial.baud,
            reset_wait=config.serial.reset_wait_seconds,
        ) as board:
            execute_plan(board, plan, config.timing, state)
        outcome = "completed"

    except KeyboardInterrupt:
        outcome = "interrupted"
        exit_code = 130
        if not state.any_command_written:
            print("\nCANCELLED: no motion command was sent. Nothing moved.")
            raise SystemExit(exit_code)
        if state.stop_outcome is None:
            # Interrupted between moves: no pulse train was running, so there
            # was nothing to stop.
            print(
                "\nCANCELLED between moves. No move was in progress, so no "
                "software STOP was needed."
            )
            print(
                f"  Last commanded position: {state.commanded_position:+d} steps"
            )
            print(
                "  The sequence did NOT finish. The needle is not back at its "
                "starting point."
            )
        else:
            report_stop_outcome(state.stop_outcome, state.commanded_position)

    except serial.SerialException as error:
        outcome = "serial_error"
        exit_code = 1
        print(f"\nFAIL: {serial_error_message(error, config.serial.port)}")

    except (ArduinoTestError, MotionConfigError) as error:
        outcome = "aborted"
        exit_code = 1
        print(f"\nFAIL: {error}")
        print(
            "The sequence stopped part-way. The needle is NOT at its starting "
            "position. Do not simply re-run this configuration: work out where "
            "the needle actually is first."
        )

    # Placed AFTER except KeyboardInterrupt so it can never swallow an interrupt.
    except Exception as error:  # noqa: BLE001 - last-resort operator safety net
        outcome = "unexpected_error"
        exit_code = 1
        print(f"\nFAIL: unexpected error during the run: {error!r}")
        print(
            "If motion is still occurring, turn off DM542S 24 V power "
            "immediately. Physical position is unknown."
        )

    # ---- Always write a log for anything that reached the hardware ----------
    if state.any_command_written or outcome == "completed":
        log_path = save_log(
            build_log_document(
                args, config, plan, state, started_at, outcome, calibration_summary
            )
        )
    else:
        log_path = None

    if outcome == "completed":
        executed = sum(entry["commanded_steps"] for entry in state.log)
        print("\n" + "=" * 72)
        print("SEQUENCE COMPLETE")
        print("=" * 72)
        print(f"  moves executed:             {len(state.log)} of {len(plan)}")
        print(f"  total forward steps:        {total_forward_steps(plan)}")
        print(f"  total backward steps:       {total_backward_steps(plan)}")
        print(f"  final commanded position:   {executed:+d} steps")
        print(f"  zero-net motion achieved:   {'yes' if executed == 0 else 'NO'}")
        if executed == 0:
            print(
                "\n  This means the commanded step total is zero. It does NOT "
                "prove the\n  needle physically returned to its starting point. "
                "Measure it if that\n  matters."
            )

    if log_path is not None:
        print(f"\nExecution log saved to:\n  {log_path}")
    if exit_code:
        raise SystemExit(exit_code)
    print("\nThe sequence is not repeated automatically. Re-run the script to repeat it.")


if __name__ == "__main__":
    main()
