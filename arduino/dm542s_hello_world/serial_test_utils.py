"""Shared serial and safety helpers for the DM542S scripts."""

from __future__ import annotations

import argparse
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Protocol

import serial


DEFAULT_BAUD = 115_200
ARDUINO_RESET_SECONDS = 2.0

# A software abort must answer fast or not at all. Never size this from a move
# duration: the whole point is to give up quickly and escalate to the 24 V
# switch if the firmware is not listening.
DEFAULT_STOP_TIMEOUT_SECONDS = 3.0


class SerialConnection(Protocol):
    """Small portion of the pyserial API used by these tests."""

    is_open: bool

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...

    def readline(self) -> bytes: ...


class ArduinoTestError(RuntimeError):
    """Base class for an Arduino reply that cannot complete a test."""


class NoResponseError(ArduinoTestError):
    """Raised when the Arduino returns no complete line before the timeout."""


class UnexpectedResponseError(ArduinoTestError):
    """Raised when replies arrive but the expected completion line does not."""


class ArduinoReportedError(ArduinoTestError):
    """Raised when the bridge sketch sends an ERROR line."""


def add_serial_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the explicit serial-port options shared by all three scripts."""
    parser.add_argument(
        "--port",
        required=True,
        help='Arduino serial port shown in Arduino IDE, for example "COM5".',
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD,
        help=f"Serial baud rate (default: {DEFAULT_BAUD}).",
    )


@contextmanager
def open_arduino_serial(
    port: str,
    baud: int,
    *,
    reset_wait: float = ARDUINO_RESET_SECONDS,
) -> Iterator[serial.Serial]:
    """Open the Arduino, wait for its USB reset, and discard startup text."""
    board = serial.Serial(
        port=port,
        baudrate=baud,
        timeout=0.25,
        write_timeout=2.0,
    )
    try:
        print(f"Connected to {port} at {baud} baud")
        time.sleep(reset_wait)
        board.reset_input_buffer()
        yield board
    finally:
        board.close()


def send_command_and_wait(
    board: SerialConnection,
    command: str,
    expected: str,
    *,
    timeout: float,
    expected_is_prefix: bool = False,
) -> list[str]:
    """Send one line and return once the expected finite reply is received."""
    wire_command = f"{command}\n".encode("ascii")
    board.write(wire_command)
    board.flush()
    print(f"Sent: {command}")

    responses: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not board.is_open:
            raise serial.SerialException("Arduino serial port disconnected")

        raw_line = board.readline()
        if not raw_line:
            continue

        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        responses.append(line)
        print(f"Received: {line}")

        if line.startswith("ERROR"):
            raise ArduinoReportedError(f"Arduino reported: {line}")

        matched = line.startswith(expected) if expected_is_prefix else line == expected
        if matched:
            return responses

    if not responses:
        raise NoResponseError(
            f"No Arduino response was received within {timeout:g} seconds."
        )
    raise UnexpectedResponseError(
        f"Expected {expected!r}, but it was not received within {timeout:g} seconds. "
        f"Last response: {responses[-1]!r}"
    )


@dataclass(frozen=True)
class StopOutcome:
    """What happened when this process tried to abort an in-progress move.

    A software abort only. The DM542S stays energised and keeps holding
    torque; this is NOT an emergency stop and does not de-energise anything.
    """

    attempted: bool
    sent: bool
    acknowledged: bool
    response: str | None = None
    error: str | None = None

    @property
    def summary(self) -> str:
        if not self.attempted:
            return "not attempted (no move was in progress)"
        if not self.sent:
            return f"could not be sent: {self.error}"
        if self.acknowledged:
            return f"acknowledged by firmware: {self.response}"
        if self.error:
            return f"sent, but the firmware reported: {self.error}"
        return "sent, but NOT acknowledged before the timeout expired"

    def as_log_entry(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "sent": self.sent,
            "acknowledged": self.acknowledged,
            "response": self.response,
            "error": self.error,
            "summary": self.summary,
        }


def request_software_stop(
    board: SerialConnection,
    *,
    timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
) -> StopOutcome:
    """Write STOP on the ALREADY-OPEN port and wait briefly for the reply.

    Called from interrupt and error handlers, so it never raises: every failure
    is reported in the returned :class:`StopOutcome` instead. The caller decides
    what to tell the operator.

    Windows allows only one owner per COM port. That is exactly why this must
    happen here, in the process that already holds the port -- a second terminal
    physically cannot open it while this script is running.
    """
    try:
        board.write(b"STOP\n")
        board.flush()
    except Exception as error:  # noqa: BLE001 - must never mask the original abort
        return StopOutcome(
            attempted=True, sent=False, acknowledged=False, error=repr(error)
        )

    deadline = time.monotonic() + timeout
    firmware_error: str | None = None
    while time.monotonic() < deadline:
        try:
            raw_line = board.readline()
        except Exception as error:  # noqa: BLE001 - port may drop mid-abort
            return StopOutcome(
                attempted=True, sent=True, acknowledged=False, error=repr(error)
            )
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        print(f"Received: {line}")
        if line.startswith("STOPPED"):
            return StopOutcome(
                attempted=True, sent=True, acknowledged=True, response=line
            )
        if line.startswith("ERROR"):
            firmware_error = line

    return StopOutcome(
        attempted=True, sent=True, acknowledged=False, error=firmware_error
    )


def report_stop_outcome(outcome: StopOutcome, last_commanded_position: int) -> None:
    """Tell the operator exactly what the abort achieved, and what it did not."""
    print("\n" + "!" * 72)
    print("MOTION INTERRUPTED")
    print("!" * 72)
    print(f"  Software STOP: {outcome.summary}")
    print(f"  Last commanded position: {last_commanded_position:+d} steps")
    print("  PHYSICAL POSITION IS UNKNOWN. The move was cut short part-way, so")
    print("  the commanded total above no longer describes where the needle is.")
    if outcome.acknowledged:
        print(
            "\n  The firmware stopped sending pulses. The DM542S is STILL "
            "ENERGISED\n  and still holding torque -- a software STOP does not "
            "de-energise it."
        )
    else:
        print(
            "\n  *** THE STOP WAS NOT CONFIRMED. TURN OFF THE DM542S 24 V "
            "SUPPLY NOW ***\n  if the motor is still moving. The 24 V switch is "
            "the emergency stop on\n  this rig; software is not."
        )
    print("!" * 72)


def print_motion_safety_checklist() -> None:
    """Print the physical checks that must precede either motion command."""
    print("\nBefore continuing, verify every item:")
    checks = (
        "Motor was connected before DM542S power was applied",
        "DM542S V+ and V- polarity was verified",
        "DM542S current switches match the motor rated phase current",
        "Motor coil pairs are correctly connected to A+/A- and B+/B-",
        "Motor shaft is unloaded and free to rotate",
        "ENA terminals are disconnected for this basic test",
        "You are ready to turn off 24 V power immediately if anything behaves incorrectly",
    )
    for item in checks:
        print(f"  [ ] {item}")

    print(
        "\nSTOP by turning off the DM542S 24 V supply if the driver faults, the motor "
        "vibrates violently, wiring heats, the Arduino disconnects, or you notice "
        "smoke, sparking, or an overheating smell."
    )


def require_run_confirmation() -> None:
    """Require the exact, deliberate confirmation used by both motion tests."""
    confirmation = input('\nType exactly RUN to issue this one motion command: ')
    if confirmation != "RUN":
        raise ArduinoTestError("Cancelled: exact RUN confirmation was not entered.")


def print_observation_checklist() -> None:
    """Print the observations that determine whether a motion test passed."""
    print("\nObservation checklist:")
    observations = (
        "Did the shaft rotate smoothly?",
        "Was there only normal low-speed motor noise?",
        "Did the Arduino remain connected?",
        "Did the driver avoid showing a fault indication?",
        "Did every component and wire remain cool?",
    )
    for item in observations:
        print(f"  [ ] {item}")


def serial_error_message(error: serial.SerialException, port: str) -> str:
    """Turn a pyserial exception into a beginner-friendly diagnostic."""
    detail = str(error) or error.__class__.__name__
    return (
        f"Serial error on {port}: {detail}\n"
        "Close Arduino IDE Serial Monitor and any other program using the port, "
        "then verify the --port value and USB connection."
    )
