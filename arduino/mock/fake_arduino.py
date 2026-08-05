"""Deterministic fake of the needle-controller serial protocol."""

from __future__ import annotations

from collections import deque
from typing import Callable

from arduino.python.errors import TransportError


class FakeArduinoTransport:
    """Line transport with selectable protocol and motion failure scenarios."""

    def __init__(
        self,
        *,
        scenario: str = "normal",
        device: str = "needle_controller",
        board: str = "uno_r4_minima",
        version: str = "0.1.0",
        homed: bool = False,
        motion_commissioned: bool = False,
        limits_commissioned: bool = False,
        maximum_travel_steps: int = 0,
        maximum_speed_steps_s: int = 0,
        maximum_acceleration_steps_s2: int = 0,
        home_speed_steps_s: int = 0,
        initial_position_steps: int = 0,
        initially_enabled: bool = False,
        runtime_configurable: bool = False,
        driver_model: str = "DM542T",
    ) -> None:
        self.scenario = scenario
        self.device = device
        self.board = board
        self.version = version
        self.runtime_configurable = bool(runtime_configurable)
        self.driver_model = str(driver_model)
        self.runtime_configured = not self.runtime_configurable
        self.motion_commissioned = bool(motion_commissioned)
        self.limits_commissioned = bool(limits_commissioned)
        self.maximum_travel_steps = int(maximum_travel_steps)
        self.maximum_speed_steps_s = int(maximum_speed_steps_s)
        self.maximum_acceleration_steps_s2 = int(maximum_acceleration_steps_s2)
        self.home_speed_steps_s = int(home_speed_steps_s)
        self.signal_inverted = False
        self.enable_active_low = True
        self.upper_active_low = False
        self.lower_active_low = False
        self._is_open = False
        self._rx: deque[str] = deque()
        self.tx_log: list[str] = []
        self.led = False
        self.enabled = bool(initially_enabled)
        self.moving = False
        self.homed = bool(homed)
        self.position_known = bool(homed)
        self.position_steps = int(initial_position_steps)
        self.limit_up = False
        self.limit_down = False
        self.fault = "NONE"
        self.disconnected = False
        self._pending_io: tuple[bool, ...] | None = None
        self._pending_limits: tuple[int, ...] | None = None

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        self._is_open = True
        self.disconnected = False
        self._rx.append(
            f"READY device={self.device} board={self.board} version={self.version}"
        )

    def close(self) -> None:
        self._is_open = False
        self.disconnected = True
        self._rx.clear()

    def write_line(
        self,
        line: str,
        timeout_s: float,
        payload_written_callback: Callable[[], None] | None = None,
    ) -> None:
        if not self._is_open or self.disconnected:
            raise TransportError("Fake Arduino is disconnected")
        self.tx_log.append(line)
        if payload_written_callback is not None:
            payload_written_callback()
        if self.scenario == "serial_disconnection":
            self.disconnected = True
            self._is_open = False
            raise TransportError("Simulated serial disconnection")
        try:
            sequence_text, command = line.split(" ", 1)
            sequence = int(sequence_text)
        except (ValueError, TypeError) as exc:
            self._rx.append("ERR 1 MALFORMED_COMMAND")
            raise TransportError("Fake received malformed host command") from exc
        response_sequence = sequence + 1 if self.scenario == "sequence_mismatch" else sequence
        if self.scenario == "malformed_response":
            self._rx.append("THIS IS NOT VALID")
            return
        upper = command.upper()
        verb = upper.split()[0]
        if self.scenario == "firmware_fault" and verb in {"ENABLE", "HOME", "JOG", "MOVE_ABS"}:
            self.fault = "INJECTED"
            self._rx.append(f"ERR {response_sequence} FAULT_LATCHED")
            return
        if self.limits_commissioned and self.limit_up and self.limit_down and verb in {"ENABLE", "HOME", "JOG", "MOVE_ABS"}:
            self.fault = "BOTH_LIMITS_ACTIVE"
            self.position_known = False
            self._rx.append("EVENT FAULT BOTH_LIMITS_ACTIVE")
            self._rx.append(f"ERR {response_sequence} BOTH_LIMITS_ACTIVE")
            return

        if upper == "PING":
            if not self._accept(response_sequence, command):
                return
            self._rx.append(f"DONE {response_sequence} PONG")
        elif upper == "STATUS":
            if not self._accept(response_sequence, command):
                return
            self._rx.append(f"DONE {response_sequence} {self._status_fields()}")
        elif upper == "LED ON":
            if not self._accept(response_sequence, command):
                return
            self.led = True
            self._rx.append(f"DONE {response_sequence} led=on")
        elif upper == "LED OFF":
            if not self._accept(response_sequence, command):
                return
            self.led = False
            self._rx.append(f"DONE {response_sequence} led=off")
        elif upper == "BLINK":
            if not self._accept(response_sequence, command):
                return
            self.led = False
            self._rx.append(f"DONE {response_sequence} blinks=3 led=off")
        elif upper.startswith("CONFIG_IO "):
            self._configure_io(response_sequence, command)
        elif upper.startswith("CONFIG_LIMITS "):
            self._configure_limits(response_sequence, command)
        elif upper == "CONFIG_APPLY":
            self._configure_apply(response_sequence)
        elif upper == "ENABLE":
            if self.runtime_configurable and not self.runtime_configured:
                self._rx.append(f"ERR {response_sequence} CONFIG_REQUIRED")
                return
            if not self.motion_commissioned:
                self._rx.append(f"ERR {response_sequence} MOTION_NOT_COMMISSIONED")
                return
            if self.fault != "NONE":
                self._rx.append(f"ERR {response_sequence} FAULT_LATCHED")
                return
            if not self._accept(response_sequence, command):
                return
            self.enabled = True
            self._rx.append(f"DONE {response_sequence} enabled=true")
        elif upper == "DISABLE":
            if not self._accept(response_sequence, command):
                return
            self.enabled = False
            self.moving = False
            self._rx.append(f"DONE {response_sequence} enabled=false")
        elif upper == "CLEAR_FAULT":
            if not self._accept(response_sequence, command):
                return
            self.fault = "NONE"
            self._rx.append(f"DONE {response_sequence} fault=NONE")
        elif upper == "STOP":
            if not self._accept(response_sequence, command):
                return
            self.moving = False
            if self.scenario == "movement_timeout":
                self.position_known = False
            self._rx.append(f"DONE {response_sequence} stopped=true position_known={str(self.position_known).lower()}")
        elif upper == "HOME":
            self._home(response_sequence)
        elif upper.startswith("JOG "):
            self._jog(response_sequence, command)
        elif upper.startswith("MOVE_ABS "):
            self._move_absolute(response_sequence, command)
        else:
            self._rx.append(f"ERR {response_sequence} UNKNOWN_COMMAND")

    def _accept(self, sequence: int, command: str) -> bool:
        if self.scenario != "missing_ack":
            acknowledged = "WRONG" if self.scenario == "wrong_ack_verb" else command.split()[0]
            self._rx.append(f"ACK {sequence} {acknowledged}")
        return self.scenario != "missing_done"

    def read_line(self, timeout_s: float) -> str | None:
        if not self._is_open:
            raise TransportError("Fake Arduino is disconnected")
        return self._rx.popleft() if self._rx else None

    def _status_fields(self) -> str:
        return " ".join(
            [
                f"enabled={str(self.enabled).lower()}",
                f"moving={str(self.moving).lower()}",
                f"led={'on' if self.led else 'off'}",
                f"homed={str(self.homed).lower()}",
                f"position_known={str(self.position_known).lower()}",
                f"commanded_position_steps={self.position_steps}",
                "position_is_commanded_only=true",
                f"limit_up={str(self.limit_up if self.limits_commissioned else False).lower()}",
                f"limit_down={str(self.limit_down if self.limits_commissioned else False).lower()}",
                f"fault={self.fault}",
                f"motion_commissioned={str(self.motion_commissioned).lower()}",
                f"limits_commissioned={str(self.limits_commissioned).lower()}",
                f"signal_inverted={str(self.signal_inverted).lower()}",
                f"enable_active_low={str(self.enable_active_low).lower()}",
                f"upper_active_low={str(self.upper_active_low).lower()}",
                f"lower_active_low={str(self.lower_active_low).lower()}",
                f"driver_model={self.driver_model}",
                f"maximum_travel_steps={self.maximum_travel_steps}",
                f"maximum_speed_steps_s={self.maximum_speed_steps_s}",
                f"maximum_acceleration_steps_s2={self.maximum_acceleration_steps_s2}",
                f"home_speed_steps_s={self.home_speed_steps_s if self.limits_commissioned else 0}",
                f"runtime_configurable={str(self.runtime_configurable).lower()}",
                f"runtime_configured={str(self.runtime_configured).lower()}",
            ]
        )

    def _configure_io(self, sequence: int, command: str) -> None:
        if not self.runtime_configurable:
            self._rx.append(f"ERR {sequence} UNKNOWN_COMMAND")
            return
        if self.moving or self.enabled:
            self._rx.append(f"ERR {sequence} CONFIG_STATE")
            return
        try:
            _, *tokens = command.split()
            values = tuple(int(token) for token in tokens)
        except ValueError:
            self._rx.append(f"ERR {sequence} MALFORMED_COMMAND")
            return
        if len(values) != 6 or any(value not in (0, 1) for value in values):
            self._rx.append(f"ERR {sequence} MALFORMED_COMMAND")
            return
        if not self._accept(sequence, "CONFIG_IO"):
            return
        self._pending_io = tuple(bool(value) for value in values)
        self._rx.append(f"DONE {sequence} staged=true")

    def _configure_limits(self, sequence: int, command: str) -> None:
        if not self.runtime_configurable:
            self._rx.append(f"ERR {sequence} UNKNOWN_COMMAND")
            return
        if self.moving or self.enabled:
            self._rx.append(f"ERR {sequence} CONFIG_STATE")
            return
        try:
            _, *tokens = command.split()
            values = tuple(int(token) for token in tokens)
        except ValueError:
            self._rx.append(f"ERR {sequence} MALFORMED_COMMAND")
            return
        if (
            len(values) != 4
            or any(value < 0 for value in values)
            or values[0] > 200000
            or values[1] > 5000
            or values[2] > 50000
            or values[3] > 5000
        ):
            self._rx.append(f"ERR {sequence} OUT_OF_RANGE")
            return
        if not self._accept(sequence, "CONFIG_LIMITS"):
            return
        self._pending_limits = values
        self._rx.append(f"DONE {sequence} staged=true")

    def _configure_apply(self, sequence: int) -> None:
        if not self.runtime_configurable:
            self._rx.append(f"ERR {sequence} UNKNOWN_COMMAND")
            return
        if self.moving or self.enabled:
            self._rx.append(f"ERR {sequence} CONFIG_STATE")
            return
        if self._pending_io is None or self._pending_limits is None:
            self._rx.append(f"ERR {sequence} CONFIG_INCOMPLETE")
            return
        motion, limits, signal_inverted, enable_active_low, upper_active_low, lower_active_low = self._pending_io
        maximum_travel, maximum_speed, maximum_acceleration, home_speed = self._pending_limits
        if limits and not motion:
            self._rx.append(f"ERR {sequence} CONFIG_INVALID")
            return
        if motion and (maximum_speed <= 0 or maximum_acceleration <= 0):
            self._rx.append(f"ERR {sequence} CONFIG_INVALID")
            return
        if limits and (maximum_travel <= 0 or home_speed <= 0 or home_speed > maximum_speed):
            self._rx.append(f"ERR {sequence} CONFIG_INVALID")
            return
        if not self._accept(sequence, "CONFIG_APPLY"):
            return
        self.motion_commissioned = motion
        self.limits_commissioned = limits
        self.signal_inverted = signal_inverted
        self.enable_active_low = enable_active_low
        self.upper_active_low = upper_active_low
        self.lower_active_low = lower_active_low
        self.maximum_travel_steps = maximum_travel
        self.maximum_speed_steps_s = maximum_speed
        self.maximum_acceleration_steps_s2 = maximum_acceleration
        self.home_speed_steps_s = home_speed
        self.runtime_configured = True
        self.homed = False
        self.position_known = False
        self.position_steps = 0
        self._pending_io = None
        self._pending_limits = None
        self._rx.append(
            f"DONE {sequence} runtime_configured=true enabled=false position_known=false"
        )

    def _home(self, sequence: int) -> None:
        if not self.enabled:
            self._rx.append(f"ERR {sequence} DRIVER_DISABLED")
            return
        if not self.limits_commissioned:
            self._rx.append(f"ERR {sequence} HOME_NOT_COMMISSIONED")
            return
        if self.limit_down:
            self._rx.append(f"ERR {sequence} LIMIT_DOWN_ACTIVE")
            return
        if not self._accept(sequence, "HOME"):
            return
        if self.scenario == "homing_timeout":
            self.position_known = False
            self.fault = "MOTOR_TIMEOUT"
            self._rx.append("EVENT FAULT MOTOR_TIMEOUT")
            self._rx.append(f"ERR {sequence} MOTOR_TIMEOUT")
            return
        self.limit_up = True
        self.homed = True
        self.position_known = True
        self.position_steps = 0
        self.moving = False
        self._rx.append("EVENT LIMIT_UP ACTIVE")
        self._rx.append(f"DONE {sequence} homed=true position_steps=0 position_known=true")

    def _jog(self, sequence: int, command: str) -> None:
        if not self.enabled:
            self._rx.append(f"ERR {sequence} DRIVER_DISABLED")
            return
        try:
            _, steps_text, _ = command.split()
            steps = int(steps_text)
        except ValueError:
            self._rx.append(f"ERR {sequence} MALFORMED_COMMAND")
            return
        if steps < 0 and self.limit_up:
            self._rx.append(f"ERR {sequence} LIMIT_UP_ACTIVE")
            return
        if steps > 0 and self.limit_down:
            self._rx.append(f"ERR {sequence} LIMIT_DOWN_ACTIVE")
            return
        if steps > 0 and self.limit_up:
            self.limit_up = False
            self._rx.append("EVENT LIMIT_UP INACTIVE")
        if steps < 0 and self.limit_down:
            self.limit_down = False
            self._rx.append("EVENT LIMIT_DOWN INACTIVE")
        if not self._accept(sequence, command):
            return
        if self.scenario == "movement_timeout":
            self.moving = False
            self.position_known = False
            self.fault = "MOTOR_TIMEOUT"
            self._rx.append("EVENT FAULT MOTOR_TIMEOUT")
            self._rx.append(f"ERR {sequence} MOTOR_TIMEOUT")
            return
        if self.scenario == "upper_limit_activation" and steps < 0:
            self.limit_up = True
            self._rx.append("EVENT LIMIT_UP ACTIVE")
            self.position_known = False
            self.fault = "LIMIT_UP_ACTIVE"
            self._rx.append(f"ERR {sequence} LIMIT_UP_ACTIVE")
            return
        elif self.scenario == "lower_limit_activation" and steps > 0:
            self.limit_down = True
            self._rx.append("EVENT LIMIT_DOWN ACTIVE")
            self.position_known = False
            self.fault = "LIMIT_DOWN_ACTIVE"
            self._rx.append(f"ERR {sequence} LIMIT_DOWN_ACTIVE")
            return
        if self.position_known:
            target = self.position_steps + steps
            if self.maximum_travel_steps <= 0 or not 0 <= target <= self.maximum_travel_steps:
                self._rx.append(f"ERR {sequence} TRAVEL_LIMIT")
                return
        if self.position_known:
            self.position_steps += steps
        self._rx.append(
            f"DONE {sequence} position_steps={self.position_steps} "
            f"position_known={str(self.position_known).lower()}"
        )

    def _move_absolute(self, sequence: int, command: str) -> None:
        if not self.homed or not self.position_known:
            self._rx.append(f"ERR {sequence} NOT_HOMED")
            return
        try:
            _, target_text, _ = command.split()
            target = int(target_text)
        except ValueError:
            self._rx.append(f"ERR {sequence} MALFORMED_COMMAND")
            return
        if not self.enabled:
            self._rx.append(f"ERR {sequence} DRIVER_DISABLED")
            return
        if target == self.position_steps:
            if not self._accept(sequence, command):
                return
            self._rx.append(
                f"DONE {sequence} position_steps={target} position_known=true no_motion=true"
            )
            return
        if self.maximum_travel_steps <= 0 or not 0 <= target <= self.maximum_travel_steps:
            self._rx.append(f"ERR {sequence} OUT_OF_RANGE")
            return
        delta = target - self.position_steps
        if delta < 0 and self.limit_up:
            self._rx.append(f"ERR {sequence} LIMIT_UP_ACTIVE")
            return
        if delta > 0 and self.limit_down:
            self._rx.append(f"ERR {sequence} LIMIT_DOWN_ACTIVE")
            return
        if not self._accept(sequence, command):
            return
        self.position_steps = target
        self._rx.append(f"DONE {sequence} position_steps={target} position_known=true")
