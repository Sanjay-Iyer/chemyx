"""Ctrl+C during a move must abort on the SAME serial connection.

Windows allows one owner per COM port, so a second terminal physically cannot
send STOP while a script is running. If the owning process does not do it, the
firmware's abort is unreachable and the motor runs the pulse train to
completion. These tests pin that behaviour down.

Everything here uses a mocked board. No COM port is opened.
"""

from __future__ import annotations

from collections import deque

import pytest

import serial_test_utils as utils
from motion_utils import (
    TimingSettings,
    build_move_plan,
    load_move_config,
)

from test_config_schema import write_config  # shared config builder


FAST_TIMING = TimingSettings(
    pulse_half_period_us=50, minimum_timeout_seconds=0.5, stop_timeout_seconds=0.5
)


class InterruptingBoard:
    """A board that raises KeyboardInterrupt while a MOVE is in flight.

    This reproduces the real situation: the operator presses Ctrl+C during the
    blocking readline that is waiting for ``DONE MOVE n``.
    """

    def __init__(
        self,
        *,
        interrupt_after: int = 1,
        stop_reply: bytes | None = b"STOPPED MOVE 137 OF 200\r\n",
        stop_write_raises: BaseException | None = None,
        stop_readline_raises: BaseException | None = None,
    ) -> None:
        self.pending: deque[bytes] = deque()
        self.commands: list[str] = []
        self.is_open = True
        self.closed = False
        self.close_order: list[str] = []
        self.reads_before_interrupt = interrupt_after
        self.stop_reply = stop_reply
        self.stop_write_raises = stop_write_raises
        self.stop_readline_raises = stop_readline_raises
        self.stop_sent = False

    def write(self, data: bytes) -> int:
        command = data.decode("ascii").strip()
        if command == "STOP":
            if self.stop_write_raises is not None:
                raise self.stop_write_raises
            self.stop_sent = True
            self.commands.append(command)
            self.close_order.append("write:STOP")
            if self.stop_reply is not None:
                self.pending.append(self.stop_reply)
            return len(data)

        self.commands.append(command)
        self.close_order.append(f"write:{command}")
        if command.startswith("MOVE "):
            steps = command.split()[1]
            self.pending.append(f"START MOVE {steps}\r\n".encode())
        return len(data)

    def flush(self) -> None:
        pass

    def readline(self) -> bytes:
        if self.stop_sent:
            if self.stop_readline_raises is not None:
                raise self.stop_readline_raises
            return self.pending.popleft() if self.pending else b""
        if self.pending:
            return self.pending.popleft()
        # Nothing left to say and DONE will never come: the operator interrupts.
        if self.reads_before_interrupt <= 0:
            raise KeyboardInterrupt
        self.reads_before_interrupt -= 1
        return b""

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.close_order.append("close")


def two_move_plan(tmp_path):
    config = load_move_config(
        write_config(
            tmp_path,
            moves=[
                {"name": "out", "direction": "forward", "degrees": 90.0},
                {"name": "back", "direction": "backward", "degrees": 90.0},
            ],
        )
    )
    return build_move_plan(config)


# --- STOP is sent, on the same object, before any close ----------------------


def test_keyboard_interrupt_during_a_move_sends_stop(needle_move_script, tmp_path):
    plan = two_move_plan(tmp_path)
    board = InterruptingBoard(interrupt_after=0)
    state = needle_move_script.ExecutionState()

    with pytest.raises(KeyboardInterrupt):
        needle_move_script.execute_plan(board, plan, FAST_TIMING, state)

    assert "STOP" in board.commands, "no software STOP was ever written"
    assert board.commands == ["MOVE 200", "STOP"]


def test_stop_is_written_to_the_same_board_before_it_is_closed(
    needle_move_script, tmp_path
):
    """Ordering is the whole bug: a closed port cannot carry a STOP."""
    plan = two_move_plan(tmp_path)
    board = InterruptingBoard(interrupt_after=0)
    state = needle_move_script.ExecutionState()

    with pytest.raises(KeyboardInterrupt):
        needle_move_script.execute_plan(board, plan, FAST_TIMING, state)

    assert "write:STOP" in board.close_order
    assert not board.closed, "execute_plan must not close the port itself"
    # And if the caller closes afterwards, STOP still precedes it.
    board.close()
    assert board.close_order.index("write:STOP") < board.close_order.index("close")


def test_a_successful_stop_acknowledgement_is_recorded(needle_move_script, tmp_path):
    plan = two_move_plan(tmp_path)
    board = InterruptingBoard(interrupt_after=0)
    state = needle_move_script.ExecutionState()

    with pytest.raises(KeyboardInterrupt):
        needle_move_script.execute_plan(board, plan, FAST_TIMING, state)

    outcome = state.stop_outcome
    assert outcome is not None
    assert outcome.attempted and outcome.sent and outcome.acknowledged
    assert outcome.response == "STOPPED MOVE 137 OF 200"
    assert "acknowledged" in outcome.summary


def test_the_sequence_does_not_continue_to_the_next_move(
    needle_move_script, tmp_path
):
    plan = two_move_plan(tmp_path)
    board = InterruptingBoard(interrupt_after=0)
    state = needle_move_script.ExecutionState()

    with pytest.raises(KeyboardInterrupt):
        needle_move_script.execute_plan(board, plan, FAST_TIMING, state)

    assert [c for c in board.commands if c.startswith("MOVE")] == ["MOVE 200"]
    assert state.log == []                     # the move never completed
    assert state.commanded_position == 0       # never credited as executed


# --- STOP failure modes ------------------------------------------------------


def test_stop_timeout_is_reported_not_swallowed() -> None:
    board = InterruptingBoard(stop_reply=None)   # board never answers

    outcome = utils.request_software_stop(board, timeout=0.2)

    assert outcome.attempted and outcome.sent
    assert not outcome.acknowledged
    assert "NOT acknowledged" in outcome.summary


def test_a_firmware_error_during_stop_is_reported() -> None:
    board = InterruptingBoard(stop_reply=b"ERROR controller busy\r\n")

    outcome = utils.request_software_stop(board, timeout=0.2)

    assert outcome.sent and not outcome.acknowledged
    assert outcome.error == "ERROR controller busy"
    assert "firmware reported" in outcome.summary


def test_a_serial_failure_while_writing_stop_is_reported() -> None:
    import serial

    board = InterruptingBoard(stop_write_raises=serial.SerialException("port gone"))

    outcome = utils.request_software_stop(board, timeout=0.2)

    assert outcome.attempted
    assert not outcome.sent and not outcome.acknowledged
    assert "could not be sent" in outcome.summary


def test_a_disconnection_while_reading_the_stop_reply_is_reported() -> None:
    import serial

    board = InterruptingBoard(stop_readline_raises=serial.SerialException("dropped"))

    outcome = utils.request_software_stop(board, timeout=0.2)

    assert outcome.sent and not outcome.acknowledged
    assert "dropped" in (outcome.error or "")


def test_request_software_stop_never_raises() -> None:
    """It runs inside exception handlers, so it must not add new failures."""
    class Hostile:
        is_open = True

        def write(self, data): raise RuntimeError("boom")
        def flush(self): raise RuntimeError("boom")
        def readline(self): raise RuntimeError("boom")

    outcome = utils.request_software_stop(Hostile(), timeout=0.1)

    assert outcome.attempted and not outcome.sent


def test_stop_uses_its_own_short_timeout_not_the_move_timeout() -> None:
    """A 5000-step move allows ~60 s. An abort must never wait that long."""
    import time

    timing = TimingSettings()
    board = InterruptingBoard(stop_reply=None)

    started = time.monotonic()
    utils.request_software_stop(board, timeout=timing.stop_timeout_seconds)
    elapsed = time.monotonic() - started

    assert timing.timeout_seconds(5000) > 60.0
    assert elapsed < 5.0
    assert timing.stop_timeout_seconds <= 5.0


# --- No STOP where none is warranted ----------------------------------------


def test_no_stop_is_sent_when_no_move_was_ever_started(needle_move_script, tmp_path):
    """Cancelling at the RUN prompt must not write anything to the board."""
    state = needle_move_script.ExecutionState()

    assert state.any_command_written is False
    assert state.stop_outcome is None
    assert state.motion_in_progress is False


def test_interrupt_between_moves_sends_no_stop(
    needle_move_script, tmp_path, monkeypatch
):
    """No pulse train is running during a pause, so there is nothing to abort."""
    plan = two_move_plan(tmp_path)

    class CompletingBoard(InterruptingBoard):
        def write(self, data: bytes) -> int:
            command = data.decode("ascii").strip()
            if command.startswith("MOVE "):
                steps = command.split()[1]
                self.commands.append(command)
                self.pending.append(f"START MOVE {steps}\r\n".encode())
                self.pending.append(f"DONE MOVE {steps}\r\n".encode())
                return len(data)
            return super().write(data)

    board = CompletingBoard()
    state = needle_move_script.ExecutionState()

    def interrupt_instead_of_sleeping(seconds, message):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        needle_move_script, "countdown_pause", interrupt_instead_of_sleeping
    )

    with pytest.raises(KeyboardInterrupt):
        needle_move_script.execute_plan(board, plan, FAST_TIMING, state)

    assert "STOP" not in board.commands
    assert state.stop_outcome is None            # nothing was moving
    assert state.motion_in_progress is False
    assert len(state.log) == 1                   # the first move did complete
    assert state.commanded_position == 200


def test_motion_in_progress_is_false_once_a_move_completes(
    needle_move_script, tmp_path
):
    plan = two_move_plan(tmp_path)

    class CompletingBoard(InterruptingBoard):
        def write(self, data: bytes) -> int:
            command = data.decode("ascii").strip()
            self.commands.append(command)
            if command.startswith("MOVE "):
                steps = command.split()[1]
                self.pending.append(f"START MOVE {steps}\r\n".encode())
                self.pending.append(f"DONE MOVE {steps}\r\n".encode())
            return len(data)

    board = CompletingBoard()
    state = needle_move_script.ExecutionState()
    needle_move_script.execute_plan(board, plan, FAST_TIMING, state)

    assert state.motion_in_progress is False
    assert state.commanded_position == 0
    assert board.commands == ["MOVE 200", "MOVE -200"]
    assert "STOP" not in board.commands


# --- Reporting ---------------------------------------------------------------


def test_stop_outcome_serialises_into_the_execution_log() -> None:
    outcome = utils.StopOutcome(
        attempted=True, sent=True, acknowledged=True, response="STOPPED MOVE 5 OF 200"
    )

    entry = outcome.as_log_entry()

    assert entry["attempted"] and entry["sent"] and entry["acknowledged"]
    assert entry["response"] == "STOPPED MOVE 5 OF 200"
    assert "acknowledged" in entry["summary"]


def test_report_names_the_24_v_switch_when_stop_is_unconfirmed(capsys) -> None:
    outcome = utils.StopOutcome(attempted=True, sent=True, acknowledged=False)

    utils.report_stop_outcome(outcome, 137)

    printed = capsys.readouterr().out
    assert "24 V" in printed
    assert "PHYSICAL POSITION IS UNKNOWN" in printed
    assert "+137" in printed


def test_report_does_not_claim_stop_de_energises_the_driver(capsys) -> None:
    outcome = utils.StopOutcome(
        attempted=True, sent=True, acknowledged=True, response="STOPPED MOVE 5 OF 200"
    )

    utils.report_stop_outcome(outcome, 5)

    printed = capsys.readouterr().out
    assert "STILL ENERGISED" in printed
    assert "does not " in printed and "de-energise" in printed
