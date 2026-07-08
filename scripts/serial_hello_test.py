"""Small serial command test for USB serial motor-controller boards.

By default this only opens the port and listens briefly. Use --action,
--command, or --hex-command to send a small test command to the connected
controller.
"""

from __future__ import annotations

import argparse
import sys
import time

import _bootstrap  # noqa: F401


TERMINATORS = {
    "none": b"",
    "cr": b"\r",
    "lf": b"\n",
    "crlf": b"\r\n",
}

ACTIONS = ("on", "off", "forward", "backward", "up", "down", "stop")
COMMAND_PROFILES = {
    "generic": {
        "on": "on",
        "off": "off",
        "forward": "forward",
        "backward": "backward",
        "up": "up",
        "down": "down",
        "stop": "stop",
    },
    "single-letter": {
        "on": "N",
        "off": "O",
        "forward": "F",
        "backward": "B",
        "up": "U",
        "down": "D",
        "stop": "S",
    },
}

AUTO_STOP_ACTIONS = {"forward", "backward", "up", "down", "on"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open a serial port and optionally send a motor test command."
    )
    parser.add_argument("--port", default="COM7", help="serial port to open")
    parser.add_argument("--baud", type=int, default=9600, help="baud rate")
    parser.add_argument("--timeout", type=float, default=1.0, help="serial timeout seconds")
    parser.add_argument(
        "--read-seconds",
        type=float,
        default=2.0,
        help="how long to listen for response bytes",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="list visible serial ports and exit",
    )
    parser.add_argument(
        "--send-hello",
        action="store_true",
        help="send the old hello-world text probe after opening the port",
    )
    parser.add_argument(
        "--message",
        default="hello world",
        help="text to send when --send-hello is used",
    )
    parser.add_argument(
        "--show-presets",
        action="store_true",
        help="print built-in action command presets and exit",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(COMMAND_PROFILES),
        default="generic",
        help="built-in command words used by --action",
    )
    parser.add_argument(
        "--action",
        choices=ACTIONS,
        help="motor action to send using the default command text",
    )
    parser.add_argument(
        "--command",
        help="exact command text to send instead of a built-in --action command",
    )
    parser.add_argument(
        "--hex-command",
        help=(
            "exact bytes to send as hex, for example '02 46 03'; "
            "no text terminator is added"
        ),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.5,
        help=(
            "seconds before sending stop after on/forward/backward/up/down; "
            "use 0 to disable"
        ),
    )
    parser.add_argument(
        "--stop-command",
        help="text command sent after a timed movement; defaults to profile stop",
    )
    parser.add_argument(
        "--stop-hex-command",
        help="hex bytes sent after a timed movement instead of --stop-command",
    )
    parser.add_argument(
        "--terminator",
        choices=sorted(TERMINATORS),
        default="cr",
        help="line ending added to text commands",
    )
    return parser


def print_presets() -> None:
    print("Built-in action command presets:")
    for profile, commands in COMMAND_PROFILES.items():
        print()
        print(profile)
        for action in ACTIONS:
            print(f"  {action:<9} -> {commands[action]!r}")


def selected_text_command(args: argparse.Namespace) -> str | None:
    if args.command is not None:
        return args.command
    if args.action is not None:
        return COMMAND_PROFILES[args.profile][args.action]
    if args.send_hello:
        return args.message
    return None


def selected_payload(args: argparse.Namespace) -> bytes | None:
    if args.hex_command is not None:
        return parse_hex_command(args.hex_command)

    text_command = selected_text_command(args)
    if text_command is None:
        return None
    return encode_text_command(text_command, args.terminator)


def selected_stop_payload(args: argparse.Namespace) -> bytes:
    if args.stop_hex_command is not None:
        return parse_hex_command(args.stop_hex_command)

    stop_command = args.stop_command
    if stop_command is None:
        stop_command = COMMAND_PROFILES[args.profile]["stop"]
    return encode_text_command(stop_command, args.terminator)


def should_auto_stop(args: argparse.Namespace) -> bool:
    return (
        args.command is None
        and args.hex_command is None
        and args.action in AUTO_STOP_ACTIONS
        and args.duration > 0
    )


def parse_hex_command(raw: str) -> bytes:
    cleaned = raw.replace("0x", "").replace(",", " ").replace("-", " ")
    return bytes.fromhex(cleaned)


def encode_text_command(command: str, terminator: str) -> bytes:
    return command.encode("ascii") + TERMINATORS[terminator]


def write_payload(ser, payload: bytes, label: str) -> None:
    print(f"Writing {label} command ({len(payload)} byte(s)): {payload!r}")
    ser.write(payload)
    ser.flush()


def import_serial():
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is not installed.")
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


def read_for(ser, seconds: float) -> bytes:
    deadline = time.monotonic() + max(0.0, seconds)
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        waiting = getattr(ser, "in_waiting", 0)
        if waiting:
            chunks.append(ser.read(waiting))
            continue

        chunk = ser.read(1)
        if chunk:
            chunks.append(chunk)
        else:
            time.sleep(0.05)
    return b"".join(chunks)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.show_presets:
        print_presets()
        return 0

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

    print()
    print(f"Opening {args.port} at {args.baud} baud...")
    try:
        with serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=args.timeout,
            write_timeout=args.timeout,
        ) as ser:
            # Leave hardware-control lines inactive after open where supported.
            for method_name in ("setDTR", "setRTS"):
                setter = getattr(ser, method_name, None)
                if setter is None:
                    continue
                try:
                    setter(False)
                except (OSError, serial.SerialException):
                    pass

            print(f"Connected to: {ser.name}")
            print(
                "Settings: "
                f"{ser.baudrate} baud, {ser.bytesize}{ser.parity}{ser.stopbits}, "
                f"timeout={ser.timeout}s"
            )

            if hasattr(ser, "reset_input_buffer"):
                ser.reset_input_buffer()
            if hasattr(ser, "reset_output_buffer"):
                ser.reset_output_buffer()

            payload = selected_payload(args)
            if payload is not None:
                write_payload(ser, payload, "primary")
                if should_auto_stop(args):
                    print(f"Waiting {args.duration:g} second(s) before stop...")
                    time.sleep(args.duration)
                    write_payload(ser, selected_stop_payload(args), "stop")
            else:
                print(
                    "No bytes written. Add --action, --command, "
                    "or --hex-command to transmit."
                )

            print(f"Listening for {args.read_seconds:g} second(s)...")
            response = read_for(ser, args.read_seconds)
    except UnicodeEncodeError:
        print("FAILED: text commands must contain ASCII characters only.")
        return 1
    except ValueError as exc:
        print(f"FAILED: could not parse hex command: {exc}")
        return 1
    except serial.SerialException as exc:
        print(f"FAILED: could not open or use {args.port}: {exc}")
        return 1
    except OSError as exc:
        print(f"FAILED: serial OS error on {args.port}: {exc}")
        return 1

    if response:
        print()
        print(f"Received {len(response)} byte(s).")
        print("Text:", response.decode("ascii", errors="replace"))
        print("Hex :", response.hex(" "))
    else:
        print()
        print("No response bytes were received.")
        print("That can still be normal if the board firmware does not echo text.")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
