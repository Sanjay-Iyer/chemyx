"""Shared logic for the one-shot, single-direction needle scripts.

``04_needle_up.py`` and ``05_needle_down.py`` are deliberately the simplest
thing that can move the needle: one direction, one distance, one number to
edit. **The direction is fixed by which script you run**, so it can never be
changed by editing a configuration file, and a negative distance is rejected
rather than silently reversing the direction the script name promises.

Two vocabularies meet here, and both are always printed together:

``up`` / ``down``
    What the operator sees. How this rig's mechanism actually moves the needle.
``forward`` / ``backward``
    What :mod:`motion_utils` and the firmware use. ``forward`` is a positive
    step count (``MOVE +200``), ``backward`` is negative (``MOVE -200``).

``up`` maps to ``forward`` and ``down`` maps to ``backward``. That mapping is
a property of how the motor is coupled to the needle, not of the software:
swap two wires of one motor coil and it inverts, so the preflight states both
names rather than hiding one behind the other.

Everything else -- unit conversion, rounding, timeout sizing, the relative
software plan bounds, the preflight table, the software STOP path, and the
execution log -- is the same machinery ``01_needle_move.py`` uses, imported
from :mod:`motion_utils`. There is no second implementation of the motion
mathematics in this package.

The one guarantee ``01_needle_move.py`` gives that these scripts CANNOT is
zero-net motion. A single one-way move never returns to its starting point, so
``require_zero_net_steps`` does not exist in these configurations, and nothing
here knows whether the matching return move was ever run. Use script 01 for a
sequence that must close at zero.

Nothing in this module opens a serial port on import, so it is safe to unit
test on a machine with no hardware attached.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path

import serial
import yaml

from calibration_utils import CalibrationError, load_calibration
from motion_utils import (
    FIRMWARE_MAX_ABSOLUTE_STEPS,
    MODE_MM,
    MODE_STEPS,
    PLAN_TABLE_WIDTH,
    SUPPORTED_MOVEMENT_MODES,
    MotionConfigError,
    MoveConfig,
    MoveExecutionSettings,
    PlannedMove,
    SerialSettings,
    SoftwareLimits,
    TimingSettings,
    atomic_write_text,
    build_move_plan,
    describe_rounding,
    format_plan_table,
    iso_timestamp,
    load_yaml_mapping,
    parse_serial_settings,
    parse_software_limits,
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
    validate_plan_within_limits,
    validate_timeouts_cover_plan,
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


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CALIBRATION_FILE = PACKAGE_ROOT / "configs" / "needle_calibration.yaml"
LOG_DIRECTORY = PACKAGE_ROOT / "calibration_results"

TOP_LEVEL_KEYS = ("serial", "driver", "movement", "timing", "software_limits")
MOVEMENT_KEYS = (
    "movement_mode",
    "distance",
    "require_typed_confirmation",
    "maximum_absolute_steps_per_move",
)

# Keys that are not merely unknown but actively wrong here, each with a specific
# explanation. A generic "did you mean" hint would send an operator copying
# settings across from 01_needle_move.yaml in the wrong direction.
FORBIDDEN_TOP_LEVEL_KEYS = {
    "moves": (
        "these scripts execute exactly one move, sized by movement.distance. "
        "Use 01_needle_move.py for a moves: sequence."
    ),
    "execution": (
        "this file uses movement: instead, holding a single distance rather "
        "than a list of moves."
    ),
}
FORBIDDEN_MOVEMENT_KEYS = {
    "direction": (
        "direction is fixed by which script you run -- 04_needle_forward.py "
        "always moves forward and 05_needle_backward.py always moves backward. "
        "Remove this key; it can never be honoured from a configuration file."
    ),
    "value": "this file spells the distance 'distance:'.",
    "mm": (
        "set movement_mode: mm and put the number in distance:. This file "
        "carries one distance in one unit, not one field per unit."
    ),
    "degrees": (
        "set movement_mode: degrees and put the number in distance:. This file "
        "carries one distance in one unit, not one field per unit."
    ),
    "steps": (
        "set movement_mode: steps and put the number in distance:. This file "
        "carries one distance in one unit, not one field per unit."
    ),
    "require_zero_net_steps": (
        "a single one-way move never returns to its starting point, so zero-net "
        "validation cannot apply here. Use 01_needle_move.py for a sequence "
        "that must close at zero."
    ),
    "default_pause_between_moves_seconds": (
        "there is only one move, so there is nothing to pause between."
    ),
}


def _reject_forbidden(section, forbidden: dict[str, str], *, where: str) -> None:
    """Refuse a key that is wrong here for a reason worth spelling out."""
    for key, reason in forbidden.items():
        if key in section:
            raise MotionConfigError(f"{where}: {key!r} is not valid here: {reason}")


@dataclass(frozen=True)
class SingleMoveConfig:
    """A validated ``04_needle_forward.yaml`` or ``05_needle_backward.yaml``.

    Deliberately holds no direction: that comes from the script, not the file.
    """

    path: Path
    serial: SerialSettings
    steps_per_revolution: int
    movement_mode: str
    distance: float
    require_typed_confirmation: bool
    maximum_absolute_steps_per_move: int
    timing: TimingSettings
    software_limits: SoftwareLimits


def load_single_move_config(path: str | Path) -> SingleMoveConfig:
    """Load and fully validate a single-move configuration file."""
    config_path = Path(path)
    config = load_yaml_mapping(config_path)
    label = config_path.name

    _reject_forbidden(config, FORBIDDEN_TOP_LEVEL_KEYS, where=label)
    reject_unknown_keys(config, TOP_LEVEL_KEYS, where=label)

    movement = require_section(config, "movement")
    where = f"{label}:movement"
    _reject_forbidden(movement, FORBIDDEN_MOVEMENT_KEYS, where=where)
    reject_unknown_keys(movement, MOVEMENT_KEYS, where=where)

    if "movement_mode" not in movement:
        raise MotionConfigError(
            f"Missing required value: {where}.movement_mode "
            f"(one of {list(SUPPORTED_MOVEMENT_MODES)})"
        )
    movement_mode = movement["movement_mode"]
    if movement_mode not in SUPPORTED_MOVEMENT_MODES:
        raise MotionConfigError(
            f"{where}.movement_mode must be one of "
            f"{list(SUPPORTED_MOVEMENT_MODES)}, received {movement_mode!r}"
        )

    # A magnitude, never a signed value: the sign is owned by the script name.
    # require_number with no minimum rejects zero and anything negative.
    if movement_mode == MODE_STEPS:
        distance = float(
            require_integer(movement, "distance", where=where, minimum=1)
        )
    else:
        distance = require_number(movement, "distance", where=where)

    maximum_absolute_steps_per_move = require_integer(
        movement, "maximum_absolute_steps_per_move", where=where, minimum=1
    )
    if maximum_absolute_steps_per_move > FIRMWARE_MAX_ABSOLUTE_STEPS:
        raise MotionConfigError(
            f"{where}.maximum_absolute_steps_per_move "
            f"({maximum_absolute_steps_per_move}) exceeds the firmware limit of "
            f"{FIRMWARE_MAX_ABSOLUTE_STEPS} steps per MOVE command. Lower the "
            "configuration value or raise MAX_MOVE_STEPS in the sketch and "
            "re-upload it."
        )

    return SingleMoveConfig(
        path=config_path,
        serial=parse_serial_settings(config, where=f"{label}:serial"),
        steps_per_revolution=parse_steps_per_revolution(
            config, where=f"{label}:driver"
        ),
        movement_mode=movement_mode,
        distance=distance,
        require_typed_confirmation=require_bool(
            movement, "require_typed_confirmation", where=where
        ),
        maximum_absolute_steps_per_move=maximum_absolute_steps_per_move,
        timing=parse_timing_settings(config, where=f"{label}:timing"),
        software_limits=parse_software_limits(
            config, where=f"{label}:software_limits"
        ),
    )


def plan_single_move(
    config: SingleMoveConfig,
    *,
    direction: str,
    move_name: str,
    mm_per_step: float | None = None,
) -> tuple[MoveConfig, PlannedMove]:
    """Resolve the configured distance into one signed whole-step instruction.

    Builds the same :class:`MoveConfig` that ``01_needle_move.py`` uses and runs
    it through the same :func:`build_move_plan`, so rounding, the zero-step
    check, the per-move ceiling, and the timeout sizing behave identically.
    """
    requested = (
        int(config.distance) if config.movement_mode == MODE_STEPS else config.distance
    )
    move_config = MoveConfig(
        path=config.path,
        serial=config.serial,
        steps_per_revolution=config.steps_per_revolution,
        execution=MoveExecutionSettings(
            movement_mode=config.movement_mode,
            # A single one-way move can never return to zero commanded steps.
            require_zero_net_steps=False,
            require_typed_confirmation=config.require_typed_confirmation,
            # There is no following move to pause before.
            default_pause_between_moves_seconds=0.0,
            maximum_absolute_steps_per_move=config.maximum_absolute_steps_per_move,
        ),
        timing=config.timing,
        software_limits=config.software_limits,
        raw_moves=(
            {
                "name": move_name,
                "direction": direction,
                config.movement_mode: requested,
            },
        ),
    )
    plan = build_move_plan(move_config, mm_per_step=mm_per_step)
    return move_config, plan[0]


def print_single_move_preflight(
    move: PlannedMove,
    config: SingleMoveConfig,
    *,
    direction: str,
    direction_label: str,
    mm_per_step: float | None,
    counterpart_hint: str,
) -> None:
    """Show the fully resolved move, before the COM port is opened."""
    timing = config.timing
    sign = "positive" if move.signed_steps > 0 else "negative"
    print("\nPreflight: one ONE-WAY move")
    print("=" * PLAN_TABLE_WIDTH)
    print(f"Config file:          {config.path}")
    print(
        f"Direction:            {direction_label.upper()} "
        "(fixed by this script; the config file cannot change it)"
    )
    print(
        f"                      commanded as {direction!r} = {sign} steps "
        f"(MOVE {move.signed_steps:+d})"
    )
    print(f"Movement mode:        {config.movement_mode}")
    print(f"Steps per revolution: {config.steps_per_revolution}")
    if mm_per_step is not None:
        print(f"mm per step:          {mm_per_step:.9g}")
    print(
        f"Pulse timing:         {timing.pulse_half_period_us:g} us per half "
        f"pulse => {1.0 / timing.pulse_period_seconds:.1f} steps/s"
    )
    print("-" * PLAN_TABLE_WIDTH)
    print(format_plan_table([move]))
    print("-" * PLAN_TABLE_WIDTH)

    print("\nRounding detail:")
    for line in describe_rounding(move).splitlines():
        print(f"  {line}")

    print("\nPlan summary")
    print(f"  commanded steps:              {move.signed_steps:+d}")
    print(f"  position after this move:     {move.cumulative_steps:+d} steps")
    print(f"  expected motion time:         {move.expected_seconds:.1f} s")
    print(f"  serial timeout:               {move.timeout_seconds:.1f} s")
    if config.software_limits.enabled:
        print(
            f"  relative software bounds:     "
            f"[{config.software_limits.minimum_steps:+d}, "
            f"{config.software_limits.maximum_steps:+d}] steps "
            "(RELATIVE to the start position; this rig has no homing)"
        )
    else:
        print(
            "  relative software bounds:     DISABLED -- this move's excursion "
            "is not checked"
        )

    print("\n  THIS IS A ONE-WAY MOVE. It does not return to the starting")
    print("  position, and this script does not track position between runs.")
    print(f"  {counterpart_hint}")


@dataclass
class SingleMoveState:
    """Live bookkeeping shared between the executor and the error handlers."""

    commanded_position: int = 0
    # True only between writing the MOVE and receiving its DONE line. This is
    # the ONLY window in which a software STOP is meaningful.
    motion_in_progress: bool = False
    any_command_written: bool = False
    responses: list[str] | None = None
    completed_at: str | None = None
    stop_outcome: StopOutcome | None = None


def execute_single_move(
    board: object,
    move: PlannedMove,
    timing: TimingSettings,
    state: SingleMoveState,
) -> None:
    """Send the one MOVE and block until its DONE line arrives.

    Owns the abort path: this is the only scope holding both the open serial
    connection and the knowledge that a move is actually under way.
    """
    print(f"\n--- {move.name} ---")
    print(
        f"{move.requested_value:g} {move.unit} = {move.signed_steps:+d} steps "
        f"(motion core direction {move.direction!r})"
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
        # The motor is still stepping. Abort on THIS connection, now, while the
        # port is still open -- a second process cannot take it over.
        state.stop_outcome = request_software_stop(
            board, timeout=timing.stop_timeout_seconds
        )
        raise
    finally:
        state.motion_in_progress = False

    state.commanded_position += move.signed_steps
    state.responses = responses
    state.completed_at = iso_timestamp()
    print(f"Commanded position after this move: {state.commanded_position:+d} steps")


def build_log_document(
    config_file: str,
    config: SingleMoveConfig,
    move: PlannedMove,
    state: SingleMoveState,
    *,
    direction: str,
    started_at: str,
    outcome: str,
    calibration_summary: dict | None,
) -> dict:
    """Assemble the execution record, including the fully resolved settings."""
    return {
        "run": {
            "started_at": started_at,
            "finished_at": iso_timestamp(),
            "outcome": outcome,
            "script_direction": direction,
            "config_file": config_file,
            "calibration": calibration_summary,
        },
        # Everything needed to reproduce the run, not just a file path.
        "resolved_configuration": {
            "serial": asdict(config.serial),
            "driver": {"steps_per_revolution": config.steps_per_revolution},
            "movement": {
                "movement_mode": config.movement_mode,
                "distance": config.distance,
                "require_typed_confirmation": config.require_typed_confirmation,
                "maximum_absolute_steps_per_move": (
                    config.maximum_absolute_steps_per_move
                ),
            },
            "timing": asdict(config.timing),
            "software_limits": asdict(config.software_limits),
        },
        "plan": {
            "name": move.name,
            "direction": move.direction,
            "requested_value": move.requested_value,
            "unit": move.unit,
            "exact_steps": round(move.exact_steps, 4),
            "commanded_steps": move.signed_steps,
            "expected_seconds": round(move.expected_seconds, 3),
            "timeout_seconds": round(move.timeout_seconds, 3),
        },
        "executed": {
            "commanded_steps": state.commanded_position,
            "responses": state.responses,
            "completed_at": state.completed_at,
        },
        "software_stop": (
            state.stop_outcome.as_log_entry()
            if state.stop_outcome is not None
            else {"attempted": False, "summary": "no software STOP was needed"}
        ),
        "summary": {
            "one_way_move": True,
            "returned_to_start": False,
            "final_commanded_steps_this_run": state.commanded_position,
            "physical_position_known": False,
            "position_basis": (
                "commanded steps only; no encoder, no limit switches, no homing"
            ),
            "cross_run_position_tracking": (
                "none; this script does not know what any earlier run commanded"
            ),
        },
    }


def save_log(document: dict, *, prefix: str) -> Path:
    path = unique_output_path(LOG_DIRECTORY, prefix, ".yaml")
    return atomic_write_text(
        path, yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
    )


def run_single_move_cli(
    *,
    direction: str,
    direction_label: str,
    move_name: str,
    description: str,
    default_config: Path,
    log_prefix: str,
    counterpart_hint: str,
    argv: list[str] | None = None,
) -> None:
    """The complete command-line flow shared by scripts 04 and 05.

    ``direction`` is the motion-core name (``forward``/``backward``) and
    ``direction_label`` is the operator-facing one (``up``/``down``). Both are
    supplied by the calling script and neither is ever read from the
    configuration file, so running ``04_needle_up.py`` cannot move the needle
    down no matter what the YAML says.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--config",
        default=str(default_config),
        help=f"Single-move configuration YAML (default: {default_config}).",
    )
    parser.add_argument(
        "--calibration-file",
        default=str(DEFAULT_CALIBRATION_FILE),
        help=(
            "Authoritative calibration file, used only when movement_mode is "
            f"mm (default: {DEFAULT_CALIBRATION_FILE})."
        ),
    )
    args = parser.parse_args(argv)

    # ---- Everything below happens before the serial port is opened ----------
    mm_per_step: float | None = None
    calibration_summary: dict | None = None
    try:
        config = load_single_move_config(args.config)

        if config.movement_mode == MODE_MM:
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
                f"movement_mode is {config.movement_mode!r}: no millimetre "
                "calibration is used."
            )

        move_config, move = plan_single_move(
            config, direction=direction, move_name=move_name, mm_per_step=mm_per_step
        )
        print_single_move_preflight(
            move,
            config,
            direction=direction,
            direction_label=direction_label,
            mm_per_step=mm_per_step,
            counterpart_hint=counterpart_hint,
        )

        validate_timeouts_cover_plan(
            [move], config.timing, where=f"{config.path.name}:timing"
        )
        validate_plan_within_limits([move], config.software_limits)
        if config.software_limits.enabled:
            print(
                "\nChecked: this move stays inside the relative software bounds."
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
    state = SingleMoveState()
    outcome = "cancelled_before_motion"
    exit_code = 0

    try:
        if config.require_typed_confirmation:
            require_typed_run_confirmation(
                f"\nType exactly RUN to move the needle {direction_label.upper()} "
                f"{move.requested_value:g} {move.unit} "
                f"({move.signed_steps:+d} steps): "
            )
        with open_arduino_serial(
            config.serial.port,
            config.serial.baud,
            reset_wait=config.serial.reset_wait_seconds,
        ) as board:
            execute_single_move(board, move, config.timing, state)
        outcome = "completed"

    except KeyboardInterrupt:
        outcome = "interrupted"
        exit_code = 130
        if not state.any_command_written:
            print("\nCANCELLED: no motion command was sent. Nothing moved.")
            raise SystemExit(exit_code)
        if state.stop_outcome is None:
            print(
                "\nCANCELLED after the move finished. No move was in progress, "
                "so no software STOP was needed."
            )
            print(f"  Commanded position: {state.commanded_position:+d} steps")
        else:
            report_stop_outcome(state.stop_outcome, state.commanded_position)

    except serial.SerialException as error:
        outcome = "serial_error"
        exit_code = 1
        print(f"\nFAIL: {serial_error_message(error, config.serial.port)}")

    except (ArduinoTestError, MotionConfigError) as error:
        exit_code = 1
        print(f"\nFAIL: {error}")
        # A declined confirmation and a move that died mid-pulse-train arrive
        # here through the same door. Only one of them left the needle in an
        # unknown place, so only one of them gets that warning.
        if state.any_command_written:
            outcome = "aborted"
            print(
                "The move did not complete. The needle is somewhere between its "
                "starting point and the requested distance. Work out where it "
                "actually is before commanding anything else."
            )
        else:
            outcome = "cancelled_before_motion"
            print("No motion command was sent. Nothing moved.")

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
                str(args.config),
                config,
                move,
                state,
                direction=direction,
                started_at=started_at,
                outcome=outcome,
                calibration_summary=calibration_summary,
            ),
            prefix=log_prefix,
        )
    else:
        log_path = None

    if outcome == "completed":
        print("\n" + "=" * 72)
        print("MOVE COMPLETE")
        print("=" * 72)
        print(
            f"  commanded:                  {direction_label.upper()} "
            f"{move.requested_value:g} {move.unit} ({move.signed_steps:+d} steps)"
        )
        print(f"  commanded position change:  {state.commanded_position:+d} steps")
        print(
            "\n  This was a ONE-WAY move. The needle has NOT returned to where "
            "it\n  started, and this script does not track position between "
            f"runs.\n  {counterpart_hint}"
        )

    if log_path is not None:
        print(f"\nExecution log saved to:\n  {log_path}")
    if exit_code:
        raise SystemExit(exit_code)
    print("\nThe move is not repeated automatically. Re-run the script to repeat it.")
