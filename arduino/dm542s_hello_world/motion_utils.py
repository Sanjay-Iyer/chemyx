"""Authoritative motion mathematics, YAML validation, and MOVE execution.

Every degree/millimetre/step conversion in this package goes through this
module. Nothing here opens a serial port on import, and nothing here moves a
motor by itself, so the whole file is safe to unit-test.

Vocabulary used consistently across these scripts:

commanded position
    The signed step total this software has asked the driver to execute.
    It is bookkeeping, not a measurement.
estimated position
    Commanded position converted to degrees or millimetres. Only as good as
    the calibration and only valid if no step was ever missed.
measured physical position
    A number a human read off a ruler, dial indicator, or gauge. This is the
    only real position information in the system.

There are no limit switches, no home switch, and no encoder on this rig, so a
stall, a slipping coupler, or a missed pulse makes commanded position silently
wrong.
"""

from __future__ import annotations

import difflib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml


# The DM542S DIP switches on this rig are set to 800 pulses per revolution.
DEFAULT_STEPS_PER_REVOLUTION = 800
DEGREES_PER_REVOLUTION = 360.0

# Must equal MAX_MOVE_STEPS in arduino_dm542s_bridge.ino. Python refuses first
# so an over-long move never reaches the wire.
# Pinned to the sketch by tests/test_firmware_protocol.py.
FIRMWARE_MAX_ABSOLUTE_STEPS = 5000

# Must equal HALF_PULSE_US in arduino_dm542s_bridge.ino. The FIRMWARE owns the
# real pulse timing; this is Python's copy of it, used only to size serial
# timeouts. It is pinned to the sketch by tests/test_firmware_protocol.py so
# the two cannot silently drift apart. A config may override it (for a modified
# sketch), but only within DEFENSIBLE_PULSE_HALF_PERIOD_US_RANGE.
FIRMWARE_PULSE_HALF_PERIOD_US = 5000
DEFENSIBLE_PULSE_HALF_PERIOD_US_RANGE = (50.0, 100_000.0)

MODE_MM = "mm"
MODE_DEGREES = "degrees"
MODE_STEPS = "steps"
SUPPORTED_MOVEMENT_MODES = (MODE_MM, MODE_DEGREES, MODE_STEPS)

FORWARD = "forward"
BACKWARD = "backward"
SUPPORTED_DIRECTIONS = (FORWARD, BACKWARD)

# Width of the preflight table, so callers can draw matching rules.
PLAN_TABLE_WIDTH = 112


class MotionConfigError(ValueError):
    """Raised when a YAML file or a computed motion plan is unusable."""


class ZeroNetMotionError(MotionConfigError):
    """Raised when a sequence does not return to zero commanded steps."""


class SoftwareLimitError(MotionConfigError):
    """Raised when a plan's cumulative position leaves the allowed window."""


# ---------------------------------------------------------------------------
# Rounding and unit conversion
# ---------------------------------------------------------------------------


def round_half_away_from_zero(value: float) -> int:
    """Round to the nearest whole number, with .5 going away from zero.

    Python's built-in :func:`round` uses banker's rounding, so ``round(2.5)``
    is ``2`` and ``round(3.5)`` is ``4``. That is a surprising rule to debug on
    a motor, so this package documents and uses the school rounding rule
    instead: 2.5 -> 3, -2.5 -> -3.
    """
    if value != value or value in (float("inf"), float("-inf")):
        raise MotionConfigError(f"Cannot round non-finite value: {value!r}")
    return int(Decimal(str(float(value))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def exact_degrees_to_steps(
    degrees: float,
    steps_per_revolution: int = DEFAULT_STEPS_PER_REVOLUTION,
) -> float:
    """Fractional steps for an angle, before rounding."""
    _require_positive_steps_per_revolution(steps_per_revolution)
    return degrees / DEGREES_PER_REVOLUTION * steps_per_revolution


def exact_mm_to_steps(mm: float, mm_per_step: float) -> float:
    """Fractional steps for a distance, before rounding."""
    _require_positive_mm_per_step(mm_per_step)
    return mm / mm_per_step


def degrees_to_steps(
    degrees: float,
    steps_per_revolution: int = DEFAULT_STEPS_PER_REVOLUTION,
) -> int:
    """steps = round(degrees / 360 x steps_per_revolution).

    At 800 pulses/revolution: 90 deg -> 200, 180 deg -> 400, 360 deg -> 800.
    """
    return round_half_away_from_zero(
        exact_degrees_to_steps(degrees, steps_per_revolution)
    )


def steps_to_degrees(
    steps: int,
    steps_per_revolution: int = DEFAULT_STEPS_PER_REVOLUTION,
) -> float:
    """Convert a whole step count back to degrees."""
    _require_positive_steps_per_revolution(steps_per_revolution)
    return steps / steps_per_revolution * DEGREES_PER_REVOLUTION


def mm_to_steps(mm: float, mm_per_step: float) -> int:
    """steps = round(mm / mm_per_step). Requires a completed calibration."""
    return round_half_away_from_zero(exact_mm_to_steps(mm, mm_per_step))


def steps_to_mm(steps: int, mm_per_step: float) -> float:
    """Convert a whole step count back to millimetres."""
    _require_positive_mm_per_step(mm_per_step)
    return steps * mm_per_step


def derive_calibration_factors(
    mm_per_degree: float,
    steps_per_revolution: int = DEFAULT_STEPS_PER_REVOLUTION,
) -> dict[str, float]:
    """Expand a fitted mm/degree slope into the three published factors."""
    _require_positive_steps_per_revolution(steps_per_revolution)
    if not mm_per_degree > 0:
        raise MotionConfigError(
            f"mm_per_degree must be greater than zero, received {mm_per_degree!r}"
        )

    degrees_per_step = DEGREES_PER_REVOLUTION / steps_per_revolution
    mm_per_step = mm_per_degree * degrees_per_step
    return {
        "mm_per_degree": mm_per_degree,
        "mm_per_step": mm_per_step,
        "steps_per_mm": 1.0 / mm_per_step,
    }


def _require_positive_steps_per_revolution(steps_per_revolution: Any) -> None:
    if not isinstance(steps_per_revolution, int) or isinstance(
        steps_per_revolution, bool
    ):
        raise MotionConfigError(
            "driver.steps_per_revolution must be a whole number, received "
            f"{steps_per_revolution!r}"
        )
    if steps_per_revolution <= 0:
        raise MotionConfigError(
            "driver.steps_per_revolution must be greater than zero, received "
            f"{steps_per_revolution!r}"
        )


def _require_positive_mm_per_step(mm_per_step: Any) -> None:
    if not isinstance(mm_per_step, (int, float)) or isinstance(mm_per_step, bool):
        raise MotionConfigError(
            f"mm_per_step must be a number, received {mm_per_step!r}"
        )
    if not mm_per_step > 0:
        raise MotionConfigError(
            f"mm_per_step must be greater than zero, received {mm_per_step!r}"
        )


# ---------------------------------------------------------------------------
# Small strict YAML helpers shared by both workflows
# ---------------------------------------------------------------------------


def load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    """Read a YAML file that must contain a top-level mapping."""
    config_path = Path(path)
    if not config_path.is_file():
        raise MotionConfigError(f"Configuration file not found: {config_path}")

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise MotionConfigError(f"{config_path} is not valid YAML: {error}") from error

    if loaded is None:
        raise MotionConfigError(f"{config_path} is empty")
    if not isinstance(loaded, dict):
        raise MotionConfigError(
            f"{config_path} must contain a YAML mapping, found "
            f"{type(loaded).__name__}"
        )
    return loaded


def reject_unknown_keys(
    section: Mapping[str, Any],
    allowed: Iterable[str],
    *,
    where: str,
    deprecated: Mapping[str, str] | None = None,
) -> list[str]:
    """Refuse keys this package does not understand, suggesting close matches.

    A silently ignored key is the worst kind of configuration bug: the file
    loads, the preflight looks right, and the machine does something else.
    Returns the list of deprecation warnings to print.
    """
    allowed_set = set(allowed)
    deprecated = dict(deprecated or {})
    warnings: list[str] = []

    unknown = sorted(set(section) - allowed_set - set(deprecated))
    if unknown:
        described = []
        for key in unknown:
            close = difflib.get_close_matches(
                key, sorted(allowed_set | set(deprecated)), n=1
            )
            hint = f" (did you mean {close[0]!r}?)" if close else ""
            described.append(f"{key!r}{hint}")
        raise MotionConfigError(
            f"{where}: unknown configuration key(s): {'; '.join(described)}.\n"
            f"  Allowed keys here: {', '.join(sorted(allowed_set))}"
        )

    for key, message in deprecated.items():
        if key in section:
            warnings.append(f"{where}.{key} is deprecated: {message}")
    return warnings


def require_section(config: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Fetch a required mapping section such as ``serial`` or ``motion``."""
    if name not in config:
        raise MotionConfigError(f"Missing required configuration section: {name}")
    section = config[name]
    if not isinstance(section, dict):
        raise MotionConfigError(
            f"Configuration section {name!r} must be a mapping, found "
            f"{type(section).__name__}"
        )
    return section


def require_number(
    section: Mapping[str, Any],
    key: str,
    *,
    where: str,
    minimum: float | None = None,
    maximum: float | None = None,
    allow_zero: bool = False,
) -> float:
    """Fetch a required numeric value, rejecting booleans and bad ranges."""
    if key not in section:
        raise MotionConfigError(f"Missing required value: {where}.{key}")
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MotionConfigError(
            f"{where}.{key} must be a number, received {value!r}"
        )
    value = float(value)
    if value != value:
        raise MotionConfigError(f"{where}.{key} must be a real number, received NaN")
    if minimum is None:
        floor = 0.0
        if value < floor or (value == floor and not allow_zero):
            comparison = "zero or greater" if allow_zero else "greater than zero"
            raise MotionConfigError(
                f"{where}.{key} must be {comparison}, received {value!r}"
            )
    elif value < minimum:
        raise MotionConfigError(
            f"{where}.{key} must be {minimum} or greater, received {value!r}"
        )
    if maximum is not None and value > maximum:
        raise MotionConfigError(
            f"{where}.{key} must be {maximum} or less, received {value!r}"
        )
    return value


def optional_number(
    section: Mapping[str, Any],
    key: str,
    *,
    where: str,
    default: float,
    minimum: float = 0.0,
) -> float:
    """Fetch an optional numeric value, validating it when present."""
    if key not in section or section[key] is None:
        return default
    return require_number(
        section, key, where=where, minimum=minimum, allow_zero=minimum <= 0
    )


def require_integer(
    section: Mapping[str, Any],
    key: str,
    *,
    where: str,
    minimum: int = 1,
) -> int:
    """Fetch a required whole number, rejecting floats and booleans."""
    if key not in section:
        raise MotionConfigError(f"Missing required value: {where}.{key}")
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise MotionConfigError(
            f"{where}.{key} must be a whole number, received {value!r}"
        )
    if value < minimum:
        raise MotionConfigError(
            f"{where}.{key} must be {minimum} or greater, received {value!r}"
        )
    return value


def require_bool(section: Mapping[str, Any], key: str, *, where: str) -> bool:
    """Fetch a required boolean. YAML ``true``/``false`` only."""
    if key not in section:
        raise MotionConfigError(f"Missing required value: {where}.{key}")
    value = section[key]
    if not isinstance(value, bool):
        raise MotionConfigError(
            f"{where}.{key} must be true or false, received {value!r}"
        )
    return value


def require_text(section: Mapping[str, Any], key: str, *, where: str) -> str:
    """Fetch a required non-empty string such as a COM port name."""
    if key not in section:
        raise MotionConfigError(f"Missing required value: {where}.{key}")
    value = section[key]
    if not isinstance(value, str) or not value.strip():
        raise MotionConfigError(
            f"{where}.{key} must be a non-empty string, received {value!r}"
        )
    return value.strip()


@dataclass(frozen=True)
class SerialSettings:
    """The ``serial:`` section shared by both workflow configurations."""

    port: str
    baud: int
    reset_wait_seconds: float


def parse_serial_settings(config: Mapping[str, Any], *, where: str = "serial") -> SerialSettings:
    section = require_section(config, "serial")
    reject_unknown_keys(
        section, ("port", "baud", "reset_wait_seconds"), where=where
    )
    return SerialSettings(
        port=require_text(section, "port", where=where),
        baud=require_integer(section, "baud", where=where, minimum=1),
        reset_wait_seconds=require_number(
            section, "reset_wait_seconds", where=where, minimum=0.0
        ),
    )


def parse_steps_per_revolution(config: Mapping[str, Any], *, where: str = "driver") -> int:
    section = require_section(config, "driver")
    reject_unknown_keys(section, ("steps_per_revolution",), where=where)
    return require_integer(
        section, "steps_per_revolution", where=where, minimum=1
    )


# ---------------------------------------------------------------------------
# Timing: one authoritative timeout calculation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimingSettings:
    """Everything needed to size a serial timeout for one MOVE.

    The firmware owns the real pulse rate. ``pulse_half_period_us`` is Python's
    copy of the sketch's ``HALF_PULSE_US``; if you slow the motor down in the
    sketch you MUST raise this too, or Python will give up before a valid long
    move finishes.
    """

    pulse_half_period_us: float = float(FIRMWARE_PULSE_HALF_PERIOD_US)
    startup_allowance_seconds: float = 1.0
    completion_allowance_seconds: float = 1.0
    safety_margin_fraction: float = 0.25
    minimum_timeout_seconds: float = 5.0
    maximum_timeout_seconds: float = 300.0
    stop_timeout_seconds: float = 3.0

    @property
    def pulse_period_seconds(self) -> float:
        """One complete STEP pulse: a HIGH half plus a LOW half."""
        return 2.0 * self.pulse_half_period_us / 1_000_000.0

    def expected_motion_seconds(self, signed_steps: int) -> float:
        """How long the firmware should take to emit this pulse train."""
        return abs(signed_steps) * self.pulse_period_seconds

    def required_floor_seconds(self, signed_steps: int) -> float:
        """The shortest defensible timeout: motion plus its proportional margin.

        A timeout below this WILL abort a healthy long move. Nothing is allowed
        to clamp under it.
        """
        expected = self.expected_motion_seconds(signed_steps)
        return (
            expected
            + expected * self.safety_margin_fraction
            + self.completion_allowance_seconds
        )

    def timeout_seconds(self, signed_steps: int) -> float:
        """The authoritative MOVE timeout. Every caller uses this one function."""
        raw = self.startup_allowance_seconds + self.required_floor_seconds(signed_steps)
        clamped = max(raw, self.minimum_timeout_seconds)
        clamped = min(clamped, self.maximum_timeout_seconds)
        # The maximum must never cut below what a healthy move genuinely needs.
        return max(clamped, self.required_floor_seconds(signed_steps))

    def describe(self, signed_steps: int) -> str:
        return (
            f"{abs(signed_steps)} steps, expect about "
            f"{self.expected_motion_seconds(signed_steps):.1f} s of motion; "
            f"giving up after {self.timeout_seconds(signed_steps):.1f} s"
        )


TIMING_KEYS = (
    "pulse_half_period_us",
    "startup_allowance_seconds",
    "completion_allowance_seconds",
    "safety_margin_fraction",
    "minimum_timeout_seconds",
    "maximum_timeout_seconds",
    "stop_timeout_seconds",
)


def parse_timing_settings(
    config: Mapping[str, Any], *, where: str = "timing", required: bool = False
) -> TimingSettings:
    """Read the optional ``timing:`` section, falling back to safe defaults."""
    if "timing" not in config:
        if required:
            raise MotionConfigError("Missing required configuration section: timing")
        return TimingSettings()

    section = require_section(config, "timing")
    reject_unknown_keys(section, TIMING_KEYS, where=where)
    defaults = TimingSettings()

    low, high = DEFENSIBLE_PULSE_HALF_PERIOD_US_RANGE
    pulse_half_period_us = require_number(
        section,
        "pulse_half_period_us",
        where=where,
        minimum=low,
        maximum=high,
    ) if "pulse_half_period_us" in section else defaults.pulse_half_period_us

    timing = TimingSettings(
        pulse_half_period_us=pulse_half_period_us,
        startup_allowance_seconds=optional_number(
            section, "startup_allowance_seconds", where=where,
            default=defaults.startup_allowance_seconds,
        ),
        completion_allowance_seconds=optional_number(
            section, "completion_allowance_seconds", where=where,
            default=defaults.completion_allowance_seconds,
        ),
        safety_margin_fraction=optional_number(
            section, "safety_margin_fraction", where=where,
            default=defaults.safety_margin_fraction,
        ),
        minimum_timeout_seconds=optional_number(
            section, "minimum_timeout_seconds", where=where,
            default=defaults.minimum_timeout_seconds,
        ),
        maximum_timeout_seconds=optional_number(
            section, "maximum_timeout_seconds", where=where,
            default=defaults.maximum_timeout_seconds,
        ),
        stop_timeout_seconds=optional_number(
            section, "stop_timeout_seconds", where=where,
            default=defaults.stop_timeout_seconds, minimum=0.1,
        ),
    )

    if timing.maximum_timeout_seconds < timing.minimum_timeout_seconds:
        raise MotionConfigError(
            f"{where}.maximum_timeout_seconds "
            f"({timing.maximum_timeout_seconds:g}) is below "
            f"{where}.minimum_timeout_seconds ({timing.minimum_timeout_seconds:g})."
        )
    if timing.pulse_half_period_us != FIRMWARE_PULSE_HALF_PERIOD_US:
        print(
            f"NOTE: {where}.pulse_half_period_us is "
            f"{timing.pulse_half_period_us:g} us, but this package was built "
            f"against a sketch with HALF_PULSE_US = {FIRMWARE_PULSE_HALF_PERIOD_US}. "
            "Timeouts will be sized for the configured value. Make sure the "
            "sketch actually flashed on the board matches."
        )
    return timing


def validate_timeouts_cover_plan(
    plan: Sequence["PlannedMove"], timing: TimingSettings, *, where: str = "timing"
) -> None:
    """Refuse a configuration whose ceiling would abort a healthy long move."""
    for move in plan:
        floor = timing.required_floor_seconds(move.signed_steps)
        if timing.maximum_timeout_seconds < floor:
            raise MotionConfigError(
                f"{where}.maximum_timeout_seconds "
                f"({timing.maximum_timeout_seconds:g} s) is below what move "
                f"{move.number} ({move.name}) genuinely needs "
                f"({floor:.1f} s for {abs(move.signed_steps)} steps at "
                f"{timing.pulse_half_period_us:g} us per half pulse).\n"
                "  That ceiling would abort a healthy move. Raise it, shorten "
                "the move, or speed the sketch up."
            )


# ---------------------------------------------------------------------------
# Relative software plan bounds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SoftwareLimits:
    """RELATIVE bounds on commanded position.

    Measured from wherever the needle happens to be when the script starts.
    This rig has NO homing, so these are NOT absolute machine limits: they
    cannot detect a wrong starting position, and they cannot stop a crash if
    the needle began the run somewhere unexpected. They only stop a *plan*
    from wandering further than you intended before it comes back.
    """

    enabled: bool = False
    minimum_steps: int = -FIRMWARE_MAX_ABSOLUTE_STEPS
    maximum_steps: int = FIRMWARE_MAX_ABSOLUTE_STEPS


def parse_software_limits(
    config: Mapping[str, Any], *, where: str = "software_limits"
) -> SoftwareLimits:
    if "software_limits" not in config:
        return SoftwareLimits()
    section = require_section(config, "software_limits")
    reject_unknown_keys(
        section, ("enabled", "minimum_steps", "maximum_steps"), where=where
    )
    enabled = require_bool(section, "enabled", where=where)
    minimum = require_integer(
        section, "minimum_steps", where=where, minimum=-10_000_000
    )
    maximum = require_integer(
        section, "maximum_steps", where=where, minimum=-10_000_000
    )
    if minimum > 0:
        raise MotionConfigError(
            f"{where}.minimum_steps must be zero or negative (it is measured "
            f"relative to the starting position), received {minimum}"
        )
    if maximum < 0:
        raise MotionConfigError(
            f"{where}.maximum_steps must be zero or positive (it is measured "
            f"relative to the starting position), received {maximum}"
        )
    if minimum >= maximum:
        raise MotionConfigError(
            f"{where}.minimum_steps ({minimum}) must be below "
            f"{where}.maximum_steps ({maximum})"
        )
    return SoftwareLimits(enabled=enabled, minimum_steps=minimum, maximum_steps=maximum)


def plan_excursion(plan: Sequence["PlannedMove"]) -> tuple[int, int]:
    """Lowest and highest commanded position the plan passes through."""
    if not plan:
        return (0, 0)
    positions = [0] + [move.cumulative_steps for move in plan]
    return (min(positions), max(positions))


def validate_plan_within_limits(
    plan: Sequence["PlannedMove"], limits: SoftwareLimits
) -> None:
    """Check EVERY intermediate cumulative position, not just the final one.

    A sequence can sum to zero and still travel many revolutions away from
    where it started. On a rig with no limit switches that is exactly the
    failure the final-total check cannot see.
    """
    if not limits.enabled:
        return
    lowest, highest = plan_excursion(plan)
    for move in plan:
        if not (limits.minimum_steps <= move.cumulative_steps <= limits.maximum_steps):
            raise SoftwareLimitError(
                f"Move {move.number} ({move.name}) reaches "
                f"{move.cumulative_steps:+d} commanded steps, outside the "
                f"relative software plan bounds "
                f"[{limits.minimum_steps:+d}, {limits.maximum_steps:+d}].\n"
                f"  Plan excursion: {lowest:+d} to {highest:+d} steps.\n"
                "  These bounds are RELATIVE to the position at script start. "
                "This rig has no homing, so they CANNOT detect a wrong starting "
                "position and are not machine limits.\n"
                "  Shorten the moves, reorder them so the excursion stays "
                "smaller, or widen software_limits if you are certain the "
                "travel is available."
            )


# ---------------------------------------------------------------------------
# Movement plan for 01_needle_move.py
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedMove:
    """One fully resolved move, after rounding to whole steps."""

    number: int
    name: str
    direction: str
    requested_value: float
    unit: str
    exact_steps: float
    signed_steps: int
    cumulative_steps: int
    pause_after_seconds: float = 0.0
    expected_seconds: float = 0.0
    timeout_seconds: float = 0.0

    @property
    def commanded_value(self) -> float:
        """What the rounded step count will actually produce, same unit."""
        if self.exact_steps == 0:
            return 0.0
        scale = self.requested_value / abs(self.exact_steps)
        return abs(self.signed_steps) * scale

    @property
    def rounding_error(self) -> float:
        """Commanded minus requested, in the requested unit."""
        return self.commanded_value - self.requested_value


@dataclass(frozen=True)
class MoveExecutionSettings:
    """The ``execution:`` section of ``configs/01_needle_move.yaml``."""

    movement_mode: str
    require_zero_net_steps: bool
    require_typed_confirmation: bool
    default_pause_between_moves_seconds: float
    maximum_absolute_steps_per_move: int

    @property
    def use_mm_calibration(self) -> bool:
        """Backwards-compatible view of the mode for older callers."""
        return self.movement_mode == MODE_MM


@dataclass(frozen=True)
class MoveConfig:
    """A validated ``01_needle_move.yaml``, before calibration is applied."""

    path: Path
    serial: SerialSettings
    steps_per_revolution: int
    execution: MoveExecutionSettings
    timing: TimingSettings
    software_limits: SoftwareLimits
    raw_moves: tuple[dict[str, Any], ...]
    deprecations: tuple[str, ...] = ()


EXECUTION_KEYS = (
    "movement_mode",
    "require_zero_net_steps",
    "require_typed_confirmation",
    "default_pause_between_moves_seconds",
    "maximum_absolute_steps_per_move",
)
EXECUTION_DEPRECATED = {
    "use_mm_calibration": (
        "use execution.movement_mode: mm | degrees | steps instead"
    ),
    "pause_between_moves_seconds": (
        "renamed to execution.default_pause_between_moves_seconds; per-move "
        "pause_after_seconds now overrides it"
    ),
    "stop_on_serial_error": (
        "removed. A serial error ALWAYS aborts the sequence: continuing past "
        "one on a rig with no homing would lose track of the needle"
    ),
}
MOVE_KEYS = ("name", "direction", "value", "mm", "degrees", "steps", "pause_after_seconds")
TOP_LEVEL_KEYS = ("serial", "driver", "execution", "timing", "software_limits", "moves")


def _resolve_movement_mode(section: Mapping[str, Any], *, where: str) -> str:
    """Accept the new enum, the old boolean, or fail loudly if they disagree."""
    has_mode = "movement_mode" in section
    has_legacy = "use_mm_calibration" in section

    if not has_mode and not has_legacy:
        raise MotionConfigError(
            f"Missing required value: {where}.movement_mode "
            f"(one of {list(SUPPORTED_MOVEMENT_MODES)})"
        )

    legacy_mode: str | None = None
    if has_legacy:
        legacy = section["use_mm_calibration"]
        if not isinstance(legacy, bool):
            raise MotionConfigError(
                f"{where}.use_mm_calibration must be true or false, "
                f"received {legacy!r}"
            )
        legacy_mode = MODE_MM if legacy else MODE_DEGREES

    if not has_mode:
        assert legacy_mode is not None
        return legacy_mode

    mode = section["movement_mode"]
    if mode not in SUPPORTED_MOVEMENT_MODES:
        raise MotionConfigError(
            f"{where}.movement_mode must be one of "
            f"{list(SUPPORTED_MOVEMENT_MODES)}, received {mode!r}"
        )
    if legacy_mode is not None and legacy_mode != mode:
        raise MotionConfigError(
            f"{where} sets movement_mode: {mode} and use_mm_calibration: "
            f"{section['use_mm_calibration']}, which mean different things "
            f"({mode} vs {legacy_mode}). Delete use_mm_calibration; "
            "movement_mode replaces it."
        )
    return mode


def load_move_config(path: str | Path) -> MoveConfig:
    """Load and fully validate a movement configuration file."""
    config_path = Path(path)
    config = load_yaml_mapping(config_path)
    label = config_path.name
    deprecations: list[str] = []

    deprecations += reject_unknown_keys(config, TOP_LEVEL_KEYS, where=label)

    execution_section = require_section(config, "execution")
    where_exec = f"{label}:execution"
    deprecations += reject_unknown_keys(
        execution_section,
        EXECUTION_KEYS,
        where=where_exec,
        deprecated=EXECUTION_DEPRECATED,
    )

    movement_mode = _resolve_movement_mode(execution_section, where=where_exec)

    if "default_pause_between_moves_seconds" in execution_section:
        default_pause = require_number(
            execution_section,
            "default_pause_between_moves_seconds",
            where=where_exec,
            minimum=0.0,
        )
    elif "pause_between_moves_seconds" in execution_section:
        default_pause = require_number(
            execution_section,
            "pause_between_moves_seconds",
            where=where_exec,
            minimum=0.0,
        )
    else:
        raise MotionConfigError(
            f"Missing required value: {where_exec}.default_pause_between_moves_seconds"
        )

    execution = MoveExecutionSettings(
        movement_mode=movement_mode,
        require_zero_net_steps=require_bool(
            execution_section, "require_zero_net_steps", where=where_exec
        ),
        require_typed_confirmation=require_bool(
            execution_section, "require_typed_confirmation", where=where_exec
        ),
        default_pause_between_moves_seconds=default_pause,
        maximum_absolute_steps_per_move=require_integer(
            execution_section,
            "maximum_absolute_steps_per_move",
            where=where_exec,
            minimum=1,
        ),
    )

    if execution.maximum_absolute_steps_per_move > FIRMWARE_MAX_ABSOLUTE_STEPS:
        raise MotionConfigError(
            f"{where_exec}.maximum_absolute_steps_per_move "
            f"({execution.maximum_absolute_steps_per_move}) exceeds the firmware "
            f"limit of {FIRMWARE_MAX_ABSOLUTE_STEPS} steps per MOVE command. "
            "Lower the configuration value or raise MAX_MOVE_STEPS in the sketch "
            "and re-upload it."
        )

    if "moves" not in config:
        raise MotionConfigError(f"{label}: missing required configuration section: moves")
    moves = config["moves"]
    if not isinstance(moves, list) or not moves:
        raise MotionConfigError(f"{label}: moves must be a non-empty YAML list")
    for index, move in enumerate(moves, start=1):
        if not isinstance(move, dict):
            raise MotionConfigError(
                f"{label}:moves[{index}] must be a mapping, found "
                f"{type(move).__name__}"
            )
        reject_unknown_keys(move, MOVE_KEYS, where=f"{label}:moves[{index}]")

    return MoveConfig(
        path=config_path,
        serial=parse_serial_settings(config, where=f"{label}:serial"),
        steps_per_revolution=parse_steps_per_revolution(
            config, where=f"{label}:driver"
        ),
        execution=execution,
        timing=parse_timing_settings(config, where=f"{label}:timing"),
        software_limits=parse_software_limits(
            config, where=f"{label}:software_limits"
        ),
        raw_moves=tuple(moves),
        deprecations=tuple(deprecations),
    )


def _resolve_move_value(
    move: Mapping[str, Any], mode: str, *, where: str
) -> float:
    """Read the one field that this movement mode actually uses.

    ``value:`` is the mode-agnostic spelling. ``mm:`` / ``degrees:`` / ``steps:``
    are the explicit spellings, which let one file carry both units. Supplying
    both spellings for the active mode is ambiguous and is refused.
    """
    has_value = "value" in move
    has_named = mode in move

    if has_value and has_named:
        raise MotionConfigError(
            f"{where} sets both value: {move['value']!r} and {mode}: "
            f"{move[mode]!r}. Use one or the other, not both."
        )
    if not has_value and not has_named:
        raise MotionConfigError(
            f"Missing required value: {where}.{mode} (or {where}.value) -- "
            f"movement_mode is {mode!r}, so that is the field this move needs."
        )

    key = "value" if has_value else mode
    if mode == MODE_STEPS:
        return float(require_integer(move, key, where=where, minimum=1))
    return require_number(move, key, where=where)


def build_move_plan(
    config: MoveConfig,
    *,
    mm_per_step: float | None = None,
) -> list[PlannedMove]:
    """Turn validated YAML moves into signed whole-step instructions.

    ``mm_per_step`` is required when the movement mode is ``mm`` and ignored
    otherwise.
    """
    mode = config.execution.movement_mode
    unit = mode
    label = config.path.name

    if mode == MODE_MM:
        _require_positive_mm_per_step(mm_per_step)

    plan: list[PlannedMove] = []
    cumulative = 0
    seen_names: dict[str, int] = {}

    for number, move in enumerate(config.raw_moves, start=1):
        where = f"{label}:moves[{number}]"

        name = move.get("name")
        if not isinstance(name, str) or not name.strip():
            raise MotionConfigError(
                f"{where}.name must be a non-empty string, received {name!r}"
            )
        name = name.strip()
        if name in seen_names:
            raise MotionConfigError(
                f"{where}.name is {name!r}, already used by move "
                f"{seen_names[name]}. Move names must be unique so the preflight "
                "table and the execution log are unambiguous."
            )
        seen_names[name] = number

        direction = move.get("direction")
        if direction not in SUPPORTED_DIRECTIONS:
            raise MotionConfigError(
                f"{where} ({name}): direction must be one of "
                f"{list(SUPPORTED_DIRECTIONS)}, received {direction!r}"
            )

        requested = _resolve_move_value(move, mode, where=f"{where} ({name})")

        if mode == MODE_MM:
            assert mm_per_step is not None  # guaranteed above
            exact_steps = exact_mm_to_steps(requested, mm_per_step)
        elif mode == MODE_DEGREES:
            exact_steps = exact_degrees_to_steps(
                requested, config.steps_per_revolution
            )
        else:
            # Direct step mode: no conversion through any other unit.
            exact_steps = float(requested)

        magnitude = round_half_away_from_zero(exact_steps)
        if magnitude == 0:
            raise MotionConfigError(
                f"{where} ({name}): {requested:g} {unit} rounds to 0 steps, which "
                "the firmware rejects. Increase the value or recalibrate."
            )

        limit = config.execution.maximum_absolute_steps_per_move
        if magnitude > limit:
            raise MotionConfigError(
                f"{where} ({name}): {requested:g} {unit} needs {magnitude} steps, "
                f"which exceeds execution.maximum_absolute_steps_per_move ({limit})."
            )

        pause_after = optional_number(
            move,
            "pause_after_seconds",
            where=f"{where} ({name})",
            default=config.execution.default_pause_between_moves_seconds,
        )

        signed_steps = magnitude if direction == FORWARD else -magnitude
        cumulative += signed_steps
        plan.append(
            PlannedMove(
                number=number,
                name=name,
                direction=direction,
                requested_value=requested,
                unit=unit,
                exact_steps=exact_steps,
                signed_steps=signed_steps,
                cumulative_steps=cumulative,
                pause_after_seconds=pause_after,
                expected_seconds=config.timing.expected_motion_seconds(signed_steps),
                timeout_seconds=config.timing.timeout_seconds(signed_steps),
            )
        )

    return plan


def net_steps(plan: Sequence[PlannedMove]) -> int:
    """Final commanded position in whole steps."""
    return sum(move.signed_steps for move in plan)


def total_forward_steps(plan: Sequence[PlannedMove]) -> int:
    return sum(move.signed_steps for move in plan if move.signed_steps > 0)


def total_backward_steps(plan: Sequence[PlannedMove]) -> int:
    return -sum(move.signed_steps for move in plan if move.signed_steps < 0)


def count_forward_moves(plan: Sequence[PlannedMove]) -> int:
    return sum(1 for move in plan if move.signed_steps > 0)


def count_backward_moves(plan: Sequence[PlannedMove]) -> int:
    return sum(1 for move in plan if move.signed_steps < 0)


def total_expected_runtime_seconds(plan: Sequence[PlannedMove]) -> float:
    """Motion plus pauses, ignoring the operator's own thinking time."""
    return sum(move.expected_seconds + move.pause_after_seconds for move in plan)


def validate_zero_net_steps(
    plan: Sequence[PlannedMove], *, required: bool = True
) -> int:
    """Check the plan closes at zero commanded steps and explain if it does not.

    The check runs on whole steps *after* rounding, so a sequence that looks
    balanced in millimetres or degrees can still fail by one step. That is
    deliberate: fix the YAML rather than let the script paper over it.
    """
    remaining = net_steps(plan)
    if not required or remaining == 0:
        return remaining

    forward = total_forward_steps(plan)
    backward = total_backward_steps(plan)
    unit = plan[0].unit if plan else "steps"
    direction_word = "forward" if remaining > 0 else "backward"

    detail = [
        "Zero-net validation failed. The planned sequence does not return to "
        "the commanded starting position.",
        f"  total forward steps:  {forward}",
        f"  total backward steps: {backward}",
        f"  net commanded steps:  {remaining:+d} ({abs(remaining)} steps "
        f"{direction_word} of zero)",
    ]

    nominal = sum(
        move.requested_value if move.signed_steps > 0 else -move.requested_value
        for move in plan
    )
    if abs(nominal) < 1e-9:
        detail.append(
            f"  The sequence sums to 0 {unit} before rounding, so this is a "
            "rounding imbalance: individual moves rounded to whole steps in a "
            "way that does not cancel."
        )
    else:
        detail.append(f"  The sequence also sums to {nominal:+g} {unit} before rounding.")

    detail += _suggest_zero_net_correction(plan, remaining, unit)
    detail.append(
        "  Edit the moves so the whole-step total is exactly zero. No hidden "
        "correction move will be added for you."
    )
    raise ZeroNetMotionError("\n".join(detail))


def _suggest_zero_net_correction(
    plan: Sequence[PlannedMove], remaining: int, unit: str
) -> list[str]:
    """Name a concrete edit that would balance the plan."""
    if not plan:
        return []
    opposite = BACKWARD if remaining > 0 else FORWARD
    candidates = [m for m in plan if m.direction == opposite]
    if not candidates:
        return [
            f"  Suggestion: add a {opposite} move of {abs(remaining)} steps, or "
            f"reduce the {'forward' if remaining > 0 else 'backward'} total by "
            f"{abs(remaining)} steps."
        ]
    target = max(candidates, key=lambda m: abs(m.signed_steps))
    new_magnitude = abs(target.signed_steps) + abs(remaining)
    lines = [
        f"  Suggestion: enlarge move {target.number} ({target.name}, {opposite}) "
        f"from {abs(target.signed_steps)} to {new_magnitude} steps."
    ]
    if unit != MODE_STEPS and target.exact_steps:
        scale = target.requested_value / abs(target.exact_steps)
        lines.append(
            f"    That is about {new_magnitude * scale:.4f} {unit} instead of "
            f"{target.requested_value:g} {unit}."
        )
    return lines


def format_plan_table(plan: Sequence[PlannedMove]) -> str:
    """Render the preflight table printed before the COM port is opened."""
    header = (
        f"{'#':>3} {'name':<18} {'direction':<10} {'requested':>14} "
        f"{'exact':>10} {'steps':>8} {'cumul':>9} {'motion s':>9} "
        f"{'timeout s':>10} {'pause s':>8}"
    )
    lines = [header, "-" * PLAN_TABLE_WIDTH]
    for move in plan:
        name = move.name if len(move.name) <= 18 else move.name[:15] + "..."
        requested = f"{move.requested_value:g} {move.unit}"
        lines.append(
            f"{move.number:>3} {name:<18} {move.direction:<10} "
            f"{requested:>14} {move.exact_steps:>10.2f} "
            f"{move.signed_steps:>+8d} {move.cumulative_steps:>+9d} "
            f"{move.expected_seconds:>9.1f} {move.timeout_seconds:>10.1f} "
            f"{move.pause_after_seconds:>8.1f}"
        )
    return "\n".join(lines)


def describe_rounding(move: PlannedMove) -> str:
    """Report the requested, calculated, and actually commanded values."""
    sign = "+" if move.signed_steps > 0 else "-"
    return (
        f"Requested: {move.requested_value:.3f} {move.unit} ({move.direction})\n"
        f"Calculated: {move.exact_steps:.1f} steps\n"
        f"Commanded: {move.signed_steps:+d} steps\n"
        f"Predicted actual movement: {sign}{move.commanded_value:.4f} {move.unit}"
    )


# ---------------------------------------------------------------------------
# Firmware MOVE protocol
# ---------------------------------------------------------------------------


def move_command(signed_steps: int) -> str:
    """Build one ``MOVE <signed_steps>`` line, refusing anything unsafe."""
    if isinstance(signed_steps, bool) or not isinstance(signed_steps, int):
        raise MotionConfigError(
            f"MOVE needs a whole step count, received {signed_steps!r}"
        )
    if signed_steps == 0:
        raise MotionConfigError("MOVE step count must not be zero")
    if abs(signed_steps) > FIRMWARE_MAX_ABSOLUTE_STEPS:
        raise MotionConfigError(
            f"MOVE step count {signed_steps} exceeds the firmware maximum of "
            f"{FIRMWARE_MAX_ABSOLUTE_STEPS}"
        )
    return f"MOVE {signed_steps}"


def move_done_response(signed_steps: int) -> str:
    """The exact completion line the firmware sends for this move."""
    return f"DONE MOVE {signed_steps}"


def send_move(
    board: Any,
    signed_steps: int,
    *,
    timing: TimingSettings,
    send_command_and_wait: Any,
    announce: bool = True,
) -> list[str]:
    """Send one MOVE and block until the matching DONE line arrives.

    ``send_command_and_wait`` is injected so this module never imports pyserial
    and stays importable (and testable) on a machine with no serial support.
    """
    command = move_command(signed_steps)
    if announce:
        print(f"  {timing.describe(signed_steps)}")
    return send_command_and_wait(
        board,
        command,
        move_done_response(signed_steps),
        timeout=timing.timeout_seconds(signed_steps),
    )


# ---------------------------------------------------------------------------
# Atomic file writing
# ---------------------------------------------------------------------------


def atomic_write_text(
    path: str | Path, text: str, *, keep_backup: bool = False
) -> Path:
    """Write a file so a crash can never leave it half-written.

    The authoritative calibration is the reason this exists: a truncated but
    still-parseable ``mm_per_step`` would silently produce wrong millimetres
    forever, with the previous good value unrecoverable.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if keep_backup and target.is_file():
        shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))

    handle_fd, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)  # atomic on Windows and POSIX
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


# ---------------------------------------------------------------------------
# Shared console output
# ---------------------------------------------------------------------------


def print_no_feedback_warning() -> None:
    """State plainly what this rig cannot know about its own position."""
    print("\n" + "=" * 72)
    print("POSITION SAFETY WARNING")
    print("=" * 72)
    for line in (
        "There are no limit switches on this rig.",
        "There is no home switch and no homing routine.",
        "There is no encoder and no position feedback of any kind.",
        "Position is tracked ONLY by counting the steps this script commanded.",
        "A stalled motor, a missed pulse, a slipping coupler, or a loose grub",
        "  screw makes the commanded position silently wrong.",
        "A DONE MOVE reply means the firmware finished sending pulses. It does",
        "  NOT mean the motor turned.",
        "Returning to zero commanded steps does NOT prove the needle physically",
        "  returned to where it started. Only a physical measurement shows that.",
        "Ctrl+C sends a software STOP, which halts the pulse train but leaves",
        "  the DM542S energised. It is NOT an emergency stop.",
        "THE EMERGENCY STOP ON THIS RIG IS THE 24 V SUPPLY SWITCH. Keep it",
        "  within reach and use it if anything looks wrong.",
    ):
        print(f"  {line}")
    print("=" * 72)


def require_typed_run_confirmation(prompt: str = "\nType exactly RUN to begin: ") -> None:
    """Require the operator to type RUN, with no shortcuts accepted.

    A closed or empty stdin counts as "no". These scripts must never start a
    move because they were run non-interactively, from a pipe, or from cron.
    """
    try:
        answer = input(prompt)
    except EOFError as error:
        raise MotionConfigError(
            "Cancelled: no confirmation could be read (stdin is closed). These "
            "scripts must be run interactively."
        ) from error
    if answer != "RUN":
        raise MotionConfigError("Cancelled: exact RUN confirmation was not entered.")


def timestamp_slug(when: float | None = None) -> str:
    """UTC ``YYYYmmdd-HHMMSS`` stamp used in generated result filenames."""
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime(when))


def iso_timestamp(when: float | None = None) -> str:
    """UTC ISO-8601 timestamp recorded inside generated files."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))


def unique_output_path(directory: str | Path, prefix: str, suffix: str) -> Path:
    """A timestamped path that never silently overwrites an earlier run."""
    folder = Path(directory)
    stamp = timestamp_slug()
    candidate = folder / f"{prefix}_{stamp}{suffix}"
    serial_number = 2
    while candidate.exists():
        candidate = folder / f"{prefix}_{stamp}-{serial_number}{suffix}"
        serial_number += 1
    return candidate


def countdown_pause(seconds: float, message: str) -> None:
    """Sleep between moves, telling the operator why the script is idle."""
    if seconds <= 0:
        return
    print(f"{message} ({seconds:g} s)")
    time.sleep(seconds)
