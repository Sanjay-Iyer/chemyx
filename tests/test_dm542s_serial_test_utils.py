"""Hardware-independent tests for the minimal DM542S serial helper."""

from __future__ import annotations

import importlib.util
import sys
from collections import deque
from pathlib import Path

import pytest


HELPER_PATH = (
    Path(__file__).parents[1]
    / "arduino"
    / "dm542s_hello_world"
    / "serial_test_utils.py"
)
MODULE_NAME = "dm542s_serial_test_utils"
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
utils = importlib.util.module_from_spec(SPEC)
# @dataclass resolves annotations via sys.modules[cls.__module__], so the module
# must be registered before its body runs.
sys.modules[MODULE_NAME] = utils
SPEC.loader.exec_module(utils)


class FakeSerial:
    def __init__(self, replies: list[bytes]) -> None:
        self.replies = deque(replies)
        self.writes: list[bytes] = []
        self.is_open = True
        self.flushed = False
        self.input_reset = False
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        self.flushed = True

    def readline(self) -> bytes:
        return self.replies.popleft() if self.replies else b""

    def reset_input_buffer(self) -> None:
        self.input_reset = True

    def close(self) -> None:
        self.closed = True
        self.is_open = False


def test_ping_accepts_expected_prefix() -> None:
    board = FakeSerial([b"PONG Arduino serial and LED test passed\r\n"])

    replies = utils.send_command_and_wait(
        board,
        "PING",
        "PONG",
        timeout=0.1,
        expected_is_prefix=True,
    )

    assert board.writes == [b"PING\n"]
    assert board.flushed
    assert replies == ["PONG Arduino serial and LED test passed"]


def test_motion_waits_for_exact_completion() -> None:
    board = FakeSerial([b"START FWD 100\n", b"DONE FWD 100\n"])

    replies = utils.send_command_and_wait(
        board, "FWD", "DONE FWD 100", timeout=0.1
    )

    assert board.writes == [b"FWD\n"]
    assert replies == ["START FWD 100", "DONE FWD 100"]


def test_led_blink_waits_for_ten_blink_completion() -> None:
    board = FakeSerial(
        [b"START LED BLINK 10\n", b"DONE LED BLINK 10\n"]
    )

    replies = utils.send_command_and_wait(
        board, "BLINK10", "DONE LED BLINK 10", timeout=0.1
    )

    assert board.writes == [b"BLINK10\n"]
    assert replies == ["START LED BLINK 10", "DONE LED BLINK 10"]


def test_arduino_error_fails_immediately() -> None:
    board = FakeSerial([b"ERROR unknown command\n"])

    with pytest.raises(utils.ArduinoReportedError, match="unknown command"):
        utils.send_command_and_wait(board, "FWD", "DONE FWD 100", timeout=0.1)


def test_closed_port_is_reported_as_serial_error() -> None:
    board = FakeSerial([])
    board.is_open = False

    with pytest.raises(Exception, match="disconnected") as caught:
        utils.send_command_and_wait(board, "PING", "PONG", timeout=0.1)

    assert caught.type.__module__.startswith("serial")


def test_open_context_resets_input_and_always_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    board = FakeSerial([])
    monkeypatch.setattr(utils.serial, "Serial", lambda **_kwargs: board)

    with utils.open_arduino_serial("COM_TEST", 115200, reset_wait=0) as opened:
        assert opened is board
        assert board.input_reset

    assert board.closed


@pytest.mark.parametrize("answer", ["run", "RUN ", "", "YES"])
def test_motion_confirmation_requires_exact_run(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)

    with pytest.raises(utils.ArduinoTestError, match="Cancelled"):
        utils.require_run_confirmation()
