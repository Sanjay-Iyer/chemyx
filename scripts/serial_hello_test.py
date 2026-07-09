"""Identify and test an IDEX/Rheodyne Titan or MX II serial controller."""

from __future__ import annotations

import argparse
import re
import sys
import time

import _bootstrap  # noqa: F401


TITAN_BAUD_RATES = (19200, 9600, 38400, 57600)
READ_COMMANDS = {
    "status": "S",
    "firmware": "R",
    "valve-profile": "Q",
    "command-mode": "D",
    "last-error": "E",
}
ERROR_CODES = {
    0x63: "valve failure; valve cannot be homed",
    0x58: "non-volatile memory error",
    0x4D: "valve configuration or command-mode error",
    0x42: "valve positioning error",
    0x37: "data integrity error",
    0x2C: "data CRC error",
}
COMMAND_MODES = {
    0x01: "level logic",
    0x02: "single-pulse logic",
    0x03: "BCD logic",
    0x04: "inverted BCD logic",
    0x05: "dual-pulse logic",
}
HEX_RESPONSE = re.compile(rb"^[0-9A-Fa-f]{2}\r$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Identify and command an IDEX/Rheodyne Titan or MX II controller "
            "using the manufacturer's UART/USB protocol."
        )
    )
    parser.add_argument("--port", default="COM7", help="serial port to open")
    parser.add_argument(
        "--baud",
        type=int,
        default=19200,
        help="baud rate; the manufacturer default is 19200",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="serial read/write timeout in seconds",
    )
    parser.add_argument(
        "--motion-timeout",
        type=float,
        default=10.0,
        help="maximum seconds to wait for a position or home movement",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="list visible serial ports and exit",
    )

    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--identify",
        action="store_true",
        help="safely find the baud rate and read controller information",
    )
    operation.add_argument(
        "--read",
        choices=sorted(READ_COMMANDS),
        help="send one read-only controller query",
    )
    operation.add_argument(
        "--position",
        type=int,
        choices=range(1, 13),
        metavar="1-12",
        help="move to a numbered valve position, for example 1 or 2",
    )
    parser.add_argument(
        "--ports",
        type=int,
        choices=range(1, 13),
        metavar="1-12",
        default=2,
        help=(
            "selectable positions on the attached valve (default 2: the "
            "MXX777-601 is a 2-position valve; the board silently ignores "
            "commands to positions it does not have)"
        ),
    )
    operation.add_argument(
        "--home",
        action="store_true",
        help="run the controller's home movement",
    )
    operation.add_argument(
        "--command",
        help="send exact ASCII command text; CR is added automatically",
    )
    operation.add_argument(
        "--hex-command",
        help="send exact bytes, for example '50 30 31 0D'",
    )
    return parser


def import_serial():
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is not installed in this Conda environment.")
        print("Install it with this PowerShell command:")
        print("python -m pip install pyserial")
        return None, None
    return serial, list_ports


def print_ports(list_ports) -> list:
    ports = sorted(list_ports.comports(), key=lambda port: port.device)
    if not ports:
        print("No serial ports found.")
        return ports

    print("Visible serial ports:")
    print("PORT                 DESCRIPTION                         HWID")
    for port in ports:
        print(f"{port.device:<20} {port.description or '':<35} {port.hwid or ''}")
    return ports


def open_port(serial, port: str, baud: int, timeout: float):
    # Set control lines before opening to avoid an unnecessary DTR/RTS pulse.
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.bytesize = serial.EIGHTBITS
    ser.parity = serial.PARITY_NONE
    ser.stopbits = serial.STOPBITS_ONE
    ser.timeout = min(timeout, 0.1)
    ser.write_timeout = timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


def clear_buffers(ser) -> None:
    ser.reset_input_buffer()
    ser.reset_output_buffer()


def write_payload(ser, payload: bytes, label: str) -> None:
    print(f"Sending {label}: {payload!r}")
    ser.write(payload)
    ser.flush()


def read_packet(ser, timeout: float) -> bytes:
    deadline = time.monotonic() + max(0.0, timeout)
    response = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(1)
        if not chunk:
            continue
        response.extend(chunk)
        if chunk == b"\r" or response == b"*":
            break
    return bytes(response)


def send_command(ser, command: str, timeout: float, label: str) -> bytes:
    payload = command.encode("ascii") + b"\r"
    write_payload(ser, payload, label)
    return read_packet(ser, timeout)


def decode_hex_response(response: bytes) -> int | None:
    if not HEX_RESPONSE.fullmatch(response):
        return None
    return int(response[:2], 16)


def describe_response(query: str, response: bytes) -> str:
    if response == b"*":
        return "controller is busy"
    value = decode_hex_response(response)
    if value is None:
        if response == b"\r":
            return "command acknowledged"
        if not response:
            return "no response"
        return f"unexpected response {response!r}"

    if query == "status":
        error = ERROR_CODES.get(value)
        return error if error else f"current position {value}"
    if query == "firmware":
        return f"firmware code 0x{value:02X}"
    if query == "valve-profile":
        return f"valve profile 0x{value:02X}"
    if query == "command-mode":
        mode = COMMAND_MODES.get(value, "unknown mode")
        return f"command mode 0x{value:02X} ({mode})"
    if query == "last-error":
        if value == 0:
            return "no stored error"
        return ERROR_CODES.get(value, f"unrecognized error 0x{value:02X}")
    return f"value 0x{value:02X}"


def run_query(ser, query: str, timeout: float) -> bytes:
    response = send_command(ser, READ_COMMANDS[query], timeout, query)
    print(f"Response: {response!r} - {describe_response(query, response)}")
    return response


def identify_controller(serial, port: str, timeout: float) -> int:
    print()
    print("Searching supported Titan/MX II baud rates with read-only queries...")
    for baud in TITAN_BAUD_RATES:
        print(f"  Trying {baud} baud...")
        try:
            with open_port(serial, port, baud, timeout) as ser:
                time.sleep(0.15)
                clear_buffers(ser)
                response = send_command(ser, "S", timeout, "status query")
                if response != b"*" and decode_hex_response(response) is None:
                    continue

                print(f"Controller responded at {baud} baud.")
                print(f"Status: {response!r} - {describe_response('status', response)}")
                for query in ("firmware", "valve-profile", "command-mode", "last-error"):
                    run_query(ser, query, timeout)
                return 0
        except serial.SerialException as exc:
            print(f"FAILED: could not use {port}: {exc}")
            return 1

    print("No valid Titan/MX II response was received at any supported baud rate.")
    print("Confirm the model, wiring, and that the vendor software is closed.")
    return 1


def wait_for_position(ser, expected: int | None, timeout: float) -> int:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        time.sleep(0.2)
        clear_buffers(ser)
        response = send_command(ser, "S", min(1.0, timeout), "status query")
        if response == b"*":
            print("Controller is still moving...")
            continue

        print(f"Status: {response!r} - {describe_response('status', response)}")
        value = decode_hex_response(response)
        if value is None:
            return 1
        if value in ERROR_CODES:
            return 1
        if expected is None or value == expected:
            return 0
        print(f"Controller stopped at position {value}, not position {expected}.")
        return 1

    print(f"Timed out after {timeout:g} seconds waiting for movement to finish.")
    return 1


def parse_hex_command(raw: str) -> bytes:
    cleaned = raw.replace("0x", "").replace(",", " ").replace("-", " ")
    return bytes.fromhex(cleaned)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # The MX II driver board silently ignores commands to positions the
    # valve does not have, so refuse them here with a loud error instead.
    if args.position is not None and args.position > args.ports:
        print(
            f"FAILED: valve has only {args.ports} positions, got "
            f"{args.position}. The board would silently ignore this command. "
            f"Pass --ports N if the attached valve really has more positions."
        )
        return 2

    serial, list_ports = import_serial()
    if serial is None:
        return 1

    ports = print_ports(list_ports)
    if args.list_only:
        return 0

    known_ports = {port.device.upper() for port in ports}
    if known_ports and args.port.upper() not in known_ports:
        print()
        print(f"WARNING: {args.port} was not in the visible port list above.")

    if args.identify:
        return identify_controller(serial, args.port, args.timeout)

    print()
    print(f"Opening {args.port} at {args.baud} baud...")
    try:
        with open_port(serial, args.port, args.baud, args.timeout) as ser:
            print(f"Connected to: {ser.name}")
            print(f"Settings: {ser.baudrate} baud, 8N1, no handshaking")
            time.sleep(0.15)
            clear_buffers(ser)

            if args.read:
                response = run_query(ser, args.read, args.timeout)
                return 0 if response else 1

            if args.position is not None:
                command = f"P{args.position:02X}"
                response = send_command(
                    ser, command, args.motion_timeout, f"position {args.position}"
                )
                print(
                    f"Initial response: {response!r} - "
                    f"{describe_response('ack', response)}"
                )
                return wait_for_position(ser, args.position, args.motion_timeout)

            if args.home:
                response = send_command(ser, "M", args.motion_timeout, "home")
                print(
                    f"Initial response: {response!r} - "
                    f"{describe_response('ack', response)}"
                )
                return wait_for_position(ser, None, args.motion_timeout)

            if args.command is not None:
                response = send_command(ser, args.command, args.timeout, "raw command")
                print(f"Response: {response!r}")
                return 0 if response else 1

            if args.hex_command is not None:
                payload = parse_hex_command(args.hex_command)
                write_payload(ser, payload, "raw bytes")
                response = read_packet(ser, args.timeout)
                print(f"Response: {response!r}")
                return 0 if response else 1

            print("No command selected. Use --identify first.")
            return 0
    except UnicodeEncodeError:
        print("FAILED: text commands must contain ASCII characters only.")
        return 1
    except ValueError as exc:
        print(f"FAILED: could not parse command: {exc}")
        return 1
    except serial.SerialException as exc:
        print(f"FAILED: could not open or use {args.port}: {exc}")
        return 1
    except OSError as exc:
        print(f"FAILED: serial OS error on {args.port}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
