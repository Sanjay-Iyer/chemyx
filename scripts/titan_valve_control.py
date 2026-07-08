"""Safely send documented position commands to an IDEX/Rheodyne valve.

PowerShell examples:

    conda activate AI
    python scripts\titan_valve_control.py --port COM7 --baud 19200 --command "S"
    python scripts\titan_valve_control.py --port COM7 --baud 19200 --command "P01"
    python scripts\titan_valve_control.py --port COM7 --baud 19200 --command "P02"
    python scripts\titan_valve_control.py --port COM7 --baud 19200 --command "P03"

This controls valve positions through the controller firmware. It never runs
the stepper motor continuously or sends arbitrary bytes.
"""

from __future__ import annotations

import argparse
import string
import sys
import time


SUPPORTED_BAUD_RATES = (9600, 19200, 38400, 57600)
MAX_POSITION = 12


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read or change an indexed position on an IDEX/Rheodyne "
            "Titan-style valve controller."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""PowerShell examples:
  conda activate AI
  python scripts\\titan_valve_control.py --port COM7 --baud 19200 --command "S"
  python scripts\\titan_valve_control.py --port COM7 --baud 19200 --command "P01"
  python scripts\\titan_valve_control.py --port COM7 --baud 19200 --command "P02"

Test supported baud rates without moving the valve:
  foreach ($b in 19200, 9600, 38400, 57600) {
      python scripts\\titan_valve_control.py --port COM7 --baud $b --command "S"
  }
""",
    )
    parser.add_argument("--port", default="COM7", help="serial port (default: COM7)")
    parser.add_argument(
        "--baud",
        type=int,
        choices=SUPPORTED_BAUD_RATES,
        default=19200,
        help="serial baud rate (default: 19200)",
    )
    parser.add_argument(
        "--command",
        default="S",
        help='documented command: "S" or a position such as "P01"',
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="maximum seconds to wait for a response (default: 2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the packet without opening the serial port",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="list available COM ports without opening one",
    )
    return parser


def import_pyserial():
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is not installed in this Conda environment.")
        print("Install it with: python -m pip install pyserial")
        return None, None
    return serial, list_ports


def list_serial_ports(list_ports) -> list:
    ports = sorted(list_ports.comports(), key=lambda item: item.device)
    if not ports:
        print("No serial ports found.")
        return ports

    print("Available serial ports:")
    print("PORT                 DESCRIPTION                         HWID")
    for port in ports:
        print(f"{port.device:<20} {port.description or '':<35} {port.hwid or ''}")
    return ports


def validate_command(raw_command: str) -> str:
    command = raw_command.strip().upper()
    if command == "S":
        return command

    if len(command) == 3 and command.startswith("P"):
        try:
            position = int(command[1:], 16)
        except ValueError:
            position = 0
        if 1 <= position <= MAX_POSITION and command == f"P{position:02X}":
            return command

    raise ValueError(
        'command must be "S" or an indexed position from "P01" through "P0C"'
    )


def command_payload(command: str) -> bytes:
    return command.encode("ascii") + b"\r"


def printable_ascii(data: bytes) -> str:
    characters = []
    for byte in data:
        character = chr(byte)
        if character in string.printable and character not in "\r\n\t":
            characters.append(character)
        elif byte == 0x0D:
            characters.append(r"\r")
        elif byte == 0x0A:
            characters.append(r"\n")
        elif byte == 0x09:
            characters.append(r"\t")
        else:
            characters.append(f"\\x{byte:02x}")
    return "".join(characters)


def print_bytes(label: str, data: bytes) -> None:
    print(f"{label} raw  : {data!r}")
    print(f"{label} ASCII: {printable_ascii(data)}")
    print(f"{label} hex  : {data.hex(' ')}")


def open_serial_port(serial, port: str, baud: int, timeout: float):
    controller = serial.Serial()
    controller.port = port
    controller.baudrate = baud
    controller.bytesize = serial.EIGHTBITS
    controller.parity = serial.PARITY_NONE
    controller.stopbits = serial.STOPBITS_ONE
    controller.timeout = min(0.1, timeout)
    controller.write_timeout = timeout
    controller.dtr = False
    controller.rts = False
    controller.open()
    return controller


def read_response(controller, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    response = bytearray()
    while time.monotonic() < deadline:
        byte = controller.read(1)
        if not byte:
            continue
        response.extend(byte)
        if byte == b"\r" or response == b"*":
            break
    return bytes(response)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0:
        print("FAILED: --timeout must be greater than zero.")
        return 2

    try:
        command = validate_command(args.command)
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 2

    payload = command_payload(command)
    if args.dry_run:
        print_bytes("Transmit", payload)
        print("Dry run: serial port was not opened and no bytes were sent.")
        return 0

    serial, list_ports = import_pyserial()
    if serial is None:
        return 1

    ports = list_serial_ports(list_ports)
    if args.list_only:
        return 0

    print_bytes("Transmit", payload)
    visible_ports = {item.device.upper() for item in ports}
    if visible_ports and args.port.upper() not in visible_ports:
        print(f"WARNING: {args.port} is not in the available port list.")

    print()
    print(f"Opening {args.port} at {args.baud} baud, 8N1...")
    try:
        with open_serial_port(serial, args.port, args.baud, args.timeout) as controller:
            time.sleep(0.15)
            controller.reset_input_buffer()
            controller.reset_output_buffer()
            controller.write(payload)
            controller.flush()
            response = read_response(controller, args.timeout)
    except serial.SerialException as exc:
        print(f"FAILED: serial communication error on {args.port}: {exc}")
        return 1
    except OSError as exc:
        print(f"FAILED: operating-system error on {args.port}: {exc}")
        return 1

    if not response:
        print(f"No response received within {args.timeout:g} seconds.")
        return 1

    print_bytes("Response", response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
