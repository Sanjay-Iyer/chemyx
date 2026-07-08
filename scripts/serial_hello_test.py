"""Small serial "hello world" smoke test for USB serial boards.

By default this only opens the port and listens briefly. Pass --send-hello to
write a simple text probe. It does not send Chemyx pump movement commands.
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open a serial port and optionally send a hello-world probe."
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
        help="send the text probe after opening the port",
    )
    parser.add_argument(
        "--message",
        default="hello world",
        help="text to send when --send-hello is used",
    )
    parser.add_argument(
        "--terminator",
        choices=sorted(TERMINATORS),
        default="cr",
        help="line ending added to --message",
    )
    return parser


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

            if args.send_hello:
                payload = args.message.encode("ascii") + TERMINATORS[args.terminator]
                print(f"Writing {len(payload)} byte(s): {payload!r}")
                ser.write(payload)
                ser.flush()
            else:
                print("No bytes written. Add --send-hello to transmit the text probe.")

            print(f"Listening for {args.read_seconds:g} second(s)...")
            response = read_for(ser, args.read_seconds)
    except UnicodeEncodeError:
        print("FAILED: --message must contain ASCII characters only.")
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
