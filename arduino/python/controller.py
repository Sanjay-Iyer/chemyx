"""Reusable host controller for the needle-controller firmware."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .errors import (
    AckTimeout,
    CommandNotDispatched,
    DeviceError,
    DoneTimeout,
    IdentityMismatch,
    MotionInterlockError,
    PositionUncertainError,
    ProtocolError,
    ReadyTimeout,
    SequenceMismatch,
    TransportError,
)
from .protocol import Response, bool_field, parse_response
from .transport import LineTransport

LOGGER = logging.getLogger("chemyx_lab.arduino")
MOTION_COMMANDS = {"ENABLE", "HOME", "JOG", "MOVE_ABS"}


@dataclass(frozen=True)
class CommandResult:
    sequence: int
    command: str
    fields: dict[str, str]
    detail: str = ""


class NeedleController:
    """Finite-time, sequence-checked Arduino controller.

    ``allow_motion`` is intentionally false by default. Scripts may set it true
    only after their live prerequisites have passed or when using a fake.
    """

    def __init__(
        self,
        transport: LineTransport,
        *,
        expected_device: str = "needle_controller",
        expected_board: str = "uno_r4_minima",
        expected_version: str | None = None,
        ready_timeout_s: float = 5.0,
        command_timeout_s: float = 10.0,
        overall_timeout_s: float = 60.0,
        allow_motion: bool = False,
        motion_guard: Callable[[], bool] | None = None,
        motion_dispatch_callback: Callable[[], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.transport = transport
        self.expected_device = expected_device
        self.expected_board = expected_board
        self.expected_version = expected_version
        self.ready_timeout_s = float(ready_timeout_s)
        self.command_timeout_s = float(command_timeout_s)
        self.overall_timeout_s = float(overall_timeout_s)
        self.allow_motion = bool(allow_motion)
        self.motion_guard = motion_guard
        self.motion_dispatch_callback = motion_dispatch_callback
        self.logger = logger or LOGGER
        self.identity: dict[str, str] = {}
        self.events: list[Response] = []
        self.position_certain = False
        self._sequence = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        return bool(self.transport.is_open)

    def __enter__(self) -> "NeedleController":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def open(self) -> "NeedleController":
        if self.is_open:
            return self
        self.transport.open()
        self._opened_at = time.monotonic()
        deadline = self._deadline(self.ready_timeout_s)
        try:
            while time.monotonic() < deadline:
                response = self._read_response(deadline)
                if response is None:
                    continue
                if response.kind == "EVENT":
                    self._record_event(response)
                    continue
                if response.kind != "READY":
                    raise ProtocolError(f"Expected READY, received {response.raw!r}")
                self._validate_identity(response.fields)
                self.identity = dict(response.fields)
                self.logger.info("arduino_ready", extra={"device_fields": self.identity})
                return self
        except BaseException:
            try:
                self.transport.close()
            finally:
                self._opened_at = None
            raise
        try:
            self.transport.close()
        finally:
            self._opened_at = None
        raise ReadyTimeout("Timed out waiting for Arduino READY identity")

    def close(self) -> None:
        if self.is_open:
            self.transport.close()
        self._opened_at = None

    def _validate_identity(self, fields: dict[str, str]) -> None:
        required = {"device": self.expected_device, "board": self.expected_board}
        if self.expected_version is not None:
            required["version"] = self.expected_version
        for field, expected in required.items():
            actual = fields.get(field)
            if actual != expected:
                raise IdentityMismatch(
                    f"READY {field} mismatch: expected {expected!r}, received {actual!r}"
                )
        if not fields.get("version"):
            raise IdentityMismatch("READY did not include a firmware version")

    def _deadline(self, duration_s: float) -> float:
        deadline = time.monotonic() + max(0.01, float(duration_s))
        if self._opened_at is not None:
            deadline = min(deadline, self._opened_at + self.overall_timeout_s)
        return deadline

    def _read_response(self, deadline: float) -> Response | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        line = self.transport.read_line(min(0.1, remaining))
        return None if line is None else parse_response(line)

    def _record_event(self, response: Response) -> None:
        self.events.append(response)
        if len(self.events) > 100:
            del self.events[:-100]
        self.logger.info("arduino_event", extra={"response": response.raw})

    def command(self, command: str, *, timeout_s: float | None = None) -> CommandResult:
        if not self.is_open:
            raise TransportError("Arduino controller is not open")
        normalized = " ".join(command.strip().split())
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise ProtocolError("Command must be one non-empty line")
        verb = normalized.split()[0].upper()
        if verb in MOTION_COMMANDS:
            self._check_motion_allowed()
        self._sequence += 1
        sequence = self._sequence
        deadline = self._deadline(timeout_s or self.command_timeout_s)
        write_remaining = deadline - time.monotonic()
        if write_remaining <= 0:
            raise CommandNotDispatched(
                f"Overall controller deadline expired before dispatching {sequence} {normalized}"
            )
        self.logger.info(
            "arduino_command_dispatch",
            extra={"sequence": sequence, "command": normalized},
        )
        dispatch_callback = (
            self.motion_dispatch_callback if verb in MOTION_COMMANDS else None
        )
        self.transport.write_line(
            f"{sequence} {normalized}",
            min(1.0, write_remaining),
            dispatch_callback,
        )
        ack_seen = False
        while time.monotonic() < deadline:
            response = self._read_response(deadline)
            if response is None:
                continue
            if response.kind == "EVENT":
                self._record_event(response)
                continue
            if response.kind == "READY":
                raise ProtocolError("Unexpected READY during an active command")
            if response.sequence != sequence:
                raise SequenceMismatch(
                    f"Expected sequence {sequence}, received {response.sequence}: {response.raw}"
                )
            if response.kind == "ERR":
                raise DeviceError(sequence, response.code or "UNKNOWN", response.detail)
            if response.kind == "ACK":
                if ack_seen:
                    raise ProtocolError(f"Duplicate ACK for sequence {sequence}")
                acknowledged_verb = str(response.command or "").split()[0].upper()
                if acknowledged_verb != verb:
                    raise ProtocolError(
                        f"ACK command mismatch for sequence {sequence}: "
                        f"expected verb {verb!r}, received {response.command!r}"
                    )
                ack_seen = True
                continue
            if response.kind == "DONE":
                if not ack_seen:
                    raise ProtocolError(f"DONE arrived before ACK for sequence {sequence}")
                self.logger.info(
                    "arduino_command_done",
                    extra={"sequence": sequence, "command": normalized, "fields": response.fields},
                )
                return CommandResult(sequence, normalized, response.fields, response.detail)
        if not ack_seen:
            raise AckTimeout(f"Timed out waiting for ACK {sequence} {normalized}")
        raise DoneTimeout(f"Timed out waiting for DONE {sequence} {normalized}")

    def _check_motion_allowed(self) -> None:
        if not self.allow_motion:
            raise MotionInterlockError("Motor command blocked by host live-motion interlock")
        if self.motion_guard is not None and not self.motion_guard():
            raise MotionInterlockError("Motor command blocked while another instrument is active")

    def _motion(self, command: str) -> CommandResult:
        try:
            result = self.command(command)
        except (MotionInterlockError, CommandNotDispatched):
            # The host rejected the command before dispatch, so no physical
            # state changed and a STOP attempt would be a hidden action.
            raise
        except BaseException as exc:
            self.position_certain = False
            self.stop_best_effort()
            if isinstance(exc, PositionUncertainError):
                raise
            raise PositionUncertainError(
                f"Motion did not complete; commanded position is uncertain: {exc}"
            ) from exc
        if "position_known" in result.fields:
            self.position_certain = bool_field(result.fields, "position_known")
        return result

    def ping(self) -> CommandResult:
        result = self.command("PING")
        if result.detail.upper() != "PONG" and result.fields.get("reply", "").upper() != "PONG":
            raise ProtocolError(f"PING did not return PONG: {result}")
        return result

    def status(self) -> dict[str, str]:
        fields = self.command("STATUS").fields
        for name in ("enabled", "moving", "led", "homed", "position_known", "limit_up", "limit_down"):
            bool_field(fields, name)
        if not bool_field(fields, "position_is_commanded_only"):
            raise ProtocolError("Firmware must label position as commanded-only")
        try:
            int(fields["commanded_position_steps"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("STATUS lacks a valid commanded_position_steps") from exc
        if not fields.get("fault"):
            raise ProtocolError("STATUS lacks a fault field")
        self.position_certain = bool_field(fields, "position_known")
        return fields

    def led_on(self) -> CommandResult:
        return self.command("LED ON")

    def led_off(self) -> CommandResult:
        return self.command("LED OFF")

    def blink(self) -> CommandResult:
        return self.command("BLINK")

    def enable(self) -> CommandResult:
        return self.command("ENABLE")

    def disable(self) -> CommandResult:
        return self.command("DISABLE")

    def home(self) -> CommandResult:
        return self._motion("HOME")

    def jog(self, signed_steps: int, speed_steps_s: int) -> CommandResult:
        return self._motion(f"JOG {int(signed_steps)} {int(speed_steps_s)}")

    def move_absolute(self, position_steps: int, speed_steps_s: int) -> CommandResult:
        return self._motion(f"MOVE_ABS {int(position_steps)} {int(speed_steps_s)}")

    def stop(self) -> CommandResult:
        return self.command("STOP", timeout_s=min(2.0, self.command_timeout_s))

    def stop_best_effort(self) -> bool:
        if not self.is_open:
            return False
        try:
            self.stop()
            return True
        except BaseException as exc:
            self.logger.error("arduino_stop_failed", extra={"error": str(exc)})
            return False

    def clear_fault(self) -> CommandResult:
        return self.command("CLEAR_FAULT")
