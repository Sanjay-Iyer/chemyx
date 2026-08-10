"""Serial response parsing against a mocked device, plus firmware regression.

Nothing here touches a real COM port. The fake device below reimplements the
sketch's reply strings, so a protocol change in the firmware that is not
mirrored in Python shows up as a failing test rather than a stalled motor.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

import serial_test_utils as utils
from motion_utils import (
    MotionConfigError,
    TimingSettings,
    move_command,
    send_move,
)


MAX_MOVE_STEPS = 5000

# Tiny pulse period so mocked round trips do not wait on real timeouts.
FAST_TIMING = TimingSettings(pulse_half_period_us=50, minimum_timeout_seconds=0.5)


class FakeArduino:
    """Minimal stand-in for the bridge sketch, driven over a pyserial-like API."""

    def __init__(self) -> None:
        self.pending: deque[bytes] = deque()
        self.commands: list[str] = []
        self.is_open = True
        self.flushed = False
        self.input_reset = False
        self.closed = False
        self.moving = False
        self.move_requested = 0
        self.silent = False   # emulate a board that never answers

    # -- pyserial surface used by send_command_and_wait ---------------------
    def write(self, data: bytes) -> int:
        command = data.decode("ascii").strip()
        self.commands.append(command)
        if self.silent:
            return len(data)
        for reply in self._respond(command):
            self.pending.append(reply.encode("ascii") + b"\r\n")
        return len(data)

    def flush(self) -> None:
        self.flushed = True

    def readline(self) -> bytes:
        return self.pending.popleft() if self.pending else b""

    def reset_input_buffer(self) -> None:
        self.input_reset = True

    def close(self) -> None:
        self.closed = True
        self.is_open = False

    # -- the sketch's executeCommand(), in Python --------------------------
    def _respond(self, command: str) -> list[str]:
        if command == "STATUS":
            if self.moving:
                return [f"STATUS MOVING 0 OF {self.move_requested}"]
            return ["STATUS IDLE"]

        if command == "STOP":
            if self.moving:
                self.moving = False
                return [f"STOPPED MOVE 0 OF {self.move_requested}"]
            return ["STOPPED IDLE"]

        if self.moving:
            return [f"ERROR controller busy, move in progress: {command}"]

        if command == "PING":
            return ["PONG Arduino serial and LED test passed"]
        if command == "BLINK10":
            return ["START LED BLINK 10", "DONE LED BLINK 10"]
        if command == "FWD":
            return ["START FWD 100", "DONE FWD 100"]
        if command == "CYCLE":
            return ["START CYCLE", "DONE CYCLE"]

        if command.startswith("MOVE "):
            return self._move(command[5:])
        if command == "MOVE":
            return ["ERROR move needs a signed step count, for example: MOVE 200"]

        return [f"ERROR unknown command: {command}"]

    def _move(self, argument: str) -> list[str]:
        try:
            steps = int(argument, 10)
        except ValueError:
            return [f"ERROR move needs one whole signed step count, received: {argument}"]
        if steps == 0:
            return ["ERROR move step count must not be zero"]
        if abs(steps) > MAX_MOVE_STEPS:
            return [f"ERROR move step count exceeds firmware maximum {MAX_MOVE_STEPS}"]
        return [f"START MOVE {steps}", f"DONE MOVE {steps}"]


# --- 16. Existing PING / BLINK10 / FWD / CYCLE behaviour is preserved --------


def test_ping_still_answers_with_pong() -> None:
    board = FakeArduino()

    replies = utils.send_command_and_wait(
        board, "PING", "PONG", timeout=1.0, expected_is_prefix=True
    )

    assert board.commands == ["PING"]
    assert replies == ["PONG Arduino serial and LED test passed"]


def test_blink10_still_brackets_its_work_with_start_and_done() -> None:
    board = FakeArduino()

    replies = utils.send_command_and_wait(
        board, "BLINK10", "DONE LED BLINK 10", timeout=1.0
    )

    assert replies == ["START LED BLINK 10", "DONE LED BLINK 10"]


def test_fwd_still_reports_exactly_100_pulses() -> None:
    board = FakeArduino()

    replies = utils.send_command_and_wait(board, "FWD", "DONE FWD 100", timeout=1.0)

    assert replies == ["START FWD 100", "DONE FWD 100"]


def test_cycle_still_completes() -> None:
    board = FakeArduino()

    replies = utils.send_command_and_wait(board, "CYCLE", "DONE CYCLE", timeout=1.0)

    assert replies == ["START CYCLE", "DONE CYCLE"]


def test_the_sketch_still_contains_the_four_original_commands(
    package_root: Path,
) -> None:
    """Guard the exact reply strings the hello-world scripts wait for."""
    sketch = (
        package_root / "arduino_dm542s_bridge" / "arduino_dm542s_bridge.ino"
    ).read_text(encoding="utf-8")

    for expected in (
        '"PING"',
        '"PONG Arduino serial and LED test passed"',
        '"BLINK10"',
        '"START LED BLINK 10"',
        '"DONE LED BLINK 10"',
        '"FWD"',
        '"START FWD 100"',
        '"DONE FWD 100"',
        '"CYCLE"',
        '"START CYCLE"',
        '"DONE CYCLE"',
        '"ERROR unknown command: "',
    ):
        assert expected in sketch, f"the sketch no longer emits {expected}"


def test_the_sketch_keeps_the_confirmed_pin_assignment(package_root: Path) -> None:
    sketch = (
        package_root / "arduino_dm542s_bridge" / "arduino_dm542s_bridge.ino"
    ).read_text(encoding="utf-8")

    assert "const uint8_t STEP_PIN = 3;" in sketch
    assert "const uint8_t DIR_PIN = 4;" in sketch
    assert "const unsigned long BAUD_RATE = 115200;" in sketch
    # Active-HIGH STEP pulses for the common-cathode wiring.
    assert "digitalWrite(STEP_PIN, HIGH)" in sketch


def test_python_and_firmware_agree_on_the_move_limit(package_root: Path) -> None:
    from motion_utils import FIRMWARE_MAX_ABSOLUTE_STEPS

    sketch = (
        package_root / "arduino_dm542s_bridge" / "arduino_dm542s_bridge.ino"
    ).read_text(encoding="utf-8")

    assert f"const long MAX_MOVE_STEPS = {FIRMWARE_MAX_ABSOLUTE_STEPS};" in sketch


def test_python_and_firmware_agree_on_the_pulse_period(package_root: Path) -> None:
    """Pin the pulse timing across the language boundary.

    If the sketch is slowed down and Python is not, every timeout is computed
    too short and healthy long moves abort part-way. This is the only automated
    defence against that drift.
    """
    from motion_utils import FIRMWARE_PULSE_HALF_PERIOD_US

    sketch = (
        package_root / "arduino_dm542s_bridge" / "arduino_dm542s_bridge.ino"
    ).read_text(encoding="utf-8")

    assert (
        f"const unsigned int HALF_PULSE_US = {FIRMWARE_PULSE_HALF_PERIOD_US};"
        in sketch
    )


def test_the_sketch_range_check_is_overflow_safe(package_root: Path) -> None:
    """Negating the most negative long is UB and would bypass the limit.

    The guard must compare signed bounds directly and reject out-of-range
    strtol results, never compute a magnitude first.
    """
    sketch = (
        package_root / "arduino_dm542s_bridge" / "arduino_dm542s_bridge.ino"
    ).read_text(encoding="utf-8")

    assert "errno = 0;" in sketch
    assert "errno == ERANGE" in sketch
    assert "value == LONG_MIN" in sketch
    assert "requestedSteps < -MAX_MOVE_STEPS || requestedSteps > MAX_MOVE_STEPS" in sketch
    # The old, unsafe formulation must be gone.
    assert "(requestedSteps < 0) ? -requestedSteps : requestedSteps" not in sketch


def test_the_sketch_includes_the_headers_the_guard_needs(package_root: Path) -> None:
    sketch = (
        package_root / "arduino_dm542s_bridge" / "arduino_dm542s_bridge.ino"
    ).read_text(encoding="utf-8")

    assert "#include <errno.h>" in sketch
    assert "#include <limits.h>" in sketch


# --- 13. MOVE response parsing against the mocked device ---------------------


@pytest.mark.parametrize("steps", [200, -200, 1, -1, MAX_MOVE_STEPS])
def test_move_round_trip_matches_the_signed_step_count(steps: int) -> None:
    board = FakeArduino()

    replies = send_move(
        board,
        steps,
        timing=FAST_TIMING,              # keep the computed timeout tiny
        send_command_and_wait=utils.send_command_and_wait,
    )

    assert board.commands == [f"MOVE {steps}"]
    assert replies == [f"START MOVE {steps}", f"DONE MOVE {steps}"]


def test_a_done_line_for_a_different_move_is_not_accepted() -> None:
    board = FakeArduino()
    board.pending.extend([b"START MOVE 200\r\n", b"DONE MOVE 100\r\n"])
    board.commands.append("preloaded")

    with pytest.raises(utils.UnexpectedResponseError):
        utils.send_command_and_wait(board, "STATUS", "DONE MOVE 200", timeout=0.2)


def test_firmware_error_lines_abort_immediately() -> None:
    board = FakeArduino()

    with pytest.raises(utils.ArduinoReportedError, match="must not be zero"):
        utils.send_command_and_wait(board, "MOVE 0", "DONE MOVE 0", timeout=0.5)


def test_a_move_beyond_the_firmware_maximum_is_refused_by_the_device() -> None:
    board = FakeArduino()

    with pytest.raises(utils.ArduinoReportedError, match="firmware maximum"):
        utils.send_command_and_wait(
            board, f"MOVE {MAX_MOVE_STEPS + 1}", "DONE", timeout=0.5
        )


@pytest.mark.parametrize("argument", ["1.5", "12x", "abc", "+", "-"])
def test_non_integer_step_values_are_refused_by_the_device(argument: str) -> None:
    board = FakeArduino()

    with pytest.raises(utils.ArduinoReportedError, match="whole signed step count"):
        utils.send_command_and_wait(
            board, f"MOVE {argument}", "DONE", timeout=0.5
        )


def test_a_bare_move_command_is_refused() -> None:
    board = FakeArduino()

    with pytest.raises(utils.ArduinoReportedError, match="signed step count"):
        utils.send_command_and_wait(board, "MOVE", "DONE", timeout=0.5)


def test_python_refuses_an_unsafe_move_before_anything_is_written() -> None:
    board = FakeArduino()

    with pytest.raises(MotionConfigError):
        send_move(
            board,
            0,
            timing=FAST_TIMING,
            send_command_and_wait=utils.send_command_and_wait,
        )

    assert board.commands == []   # nothing reached the wire


def test_move_command_strings_are_exactly_what_the_device_expects() -> None:
    board = FakeArduino()

    utils.send_command_and_wait(
        board, move_command(-400), "DONE MOVE -400", timeout=0.5
    )

    assert board.commands == ["MOVE -400"]


# --- STATUS and STOP ---------------------------------------------------------


def test_status_reports_idle_when_nothing_is_moving() -> None:
    board = FakeArduino()

    replies = utils.send_command_and_wait(board, "STATUS", "STATUS IDLE", timeout=0.5)

    assert replies == ["STATUS IDLE"]


def test_status_reports_progress_during_a_move() -> None:
    board = FakeArduino()
    board.moving = True
    board.move_requested = 800

    replies = utils.send_command_and_wait(
        board, "STATUS", "STATUS MOVING", timeout=0.5, expected_is_prefix=True
    )

    assert replies == ["STATUS MOVING 0 OF 800"]


def test_stop_interrupts_an_active_move() -> None:
    board = FakeArduino()
    board.moving = True
    board.move_requested = 800

    replies = utils.send_command_and_wait(
        board, "STOP", "STOPPED MOVE", timeout=0.5, expected_is_prefix=True
    )

    assert replies == ["STOPPED MOVE 0 OF 800"]
    assert board.moving is False


def test_stop_while_idle_is_harmless() -> None:
    board = FakeArduino()

    replies = utils.send_command_and_wait(board, "STOP", "STOPPED IDLE", timeout=0.5)

    assert replies == ["STOPPED IDLE"]


def test_other_commands_are_refused_while_a_move_is_active() -> None:
    board = FakeArduino()
    board.moving = True
    board.move_requested = 800

    with pytest.raises(utils.ArduinoReportedError, match="controller busy"):
        utils.send_command_and_wait(board, "FWD", "DONE FWD 100", timeout=0.5)


def test_the_sketch_answers_status_and_stop_before_the_busy_check(
    package_root: Path,
) -> None:
    """STATUS/STOP must be handled above the busy guard, or STOP is useless."""
    sketch = (
        package_root / "arduino_dm542s_bridge" / "arduino_dm542s_bridge.ino"
    ).read_text(encoding="utf-8")
    body = sketch[sketch.index("void executeCommand(") :]
    busy_guard = body.index("ERROR controller busy")

    assert body.index('"STATUS"') < busy_guard
    assert body.index('"STOP"') < busy_guard


def test_no_response_within_the_timeout_is_reported() -> None:
    board = FakeArduino()
    board.silent = True

    with pytest.raises(utils.NoResponseError):
        utils.send_command_and_wait(board, "STATUS", "STATUS IDLE", timeout=0.2)


def test_a_disconnected_port_is_reported_as_a_serial_error() -> None:
    board = FakeArduino()
    board.is_open = False

    with pytest.raises(Exception, match="disconnected") as caught:
        utils.send_command_and_wait(board, "STATUS", "STATUS IDLE", timeout=0.5)

    assert caught.type.__module__.startswith("serial")
