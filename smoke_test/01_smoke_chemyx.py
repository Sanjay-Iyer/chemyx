"""Read-only Chemyx Fusion 4000X communication smoke test.

This script opens one explicitly selected serial port and sends only ``help``.
It never sends a pump start, stop, rate, volume, or motion command.
"""

from __future__ import annotations

import argparse
import sys
import time


def list_ports() -> int:
    try:
        from serial.tools import list_ports as serial_list_ports
    except ImportError:
        print("ERROR: pyserial is not installed.")
        print("Install it from the offline wheel bundle before continuing.")
        return 2

    ports = sorted(serial_list_ports.comports(), key=lambda item: item.device)
    if not ports:
        print("No serial ports were found.")
        return 1

    print("PORT       DESCRIPTION                         HARDWARE ID")
    for item in ports:
        print(f"{item.device:<10} {item.description or '':<35} {item.hwid or ''}")
    return 0


def read_response(connection) -> bytes:
    chunks: list[bytes] = []
    initial = connection.read_all()
    if initial:
        chunks.append(initial)
    while True:
        chunk = connection.read(256)
        if not chunk:
            break
        chunks.append(chunk)
        if len(chunk) < 256:
            break
    return b"".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send the read-only HELP command to a Chemyx pump."
    )
    parser.add_argument("--port", help="verified Chemyx COM port, for example COM6")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--channel",
        type=int,
        choices=(0, 1, 2),
        default=1,
        help="pump channel; 0 sends no channel prefix (default: 1)",
    )
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--response-delay", type=float, default=0.2)
    parser.add_argument(
        "--list-ports", action="store_true", help="list COM ports and exit"
    )
    args = parser.parse_args()

    if args.list_ports:
        return list_ports()
    if not args.port:
        parser.error("--port is required unless --list-ports is used")

    try:
        import serial
    except ImportError:
        print("ERROR: pyserial is not installed.")
        return 2

    command = "help" if args.channel == 0 else f"{args.channel} help"
    payload = (command + "\r").encode("ascii")

    print(f"Opening Chemyx port {args.port} at {args.baud} baud (8N1)...")
    print(f"TX: {payload!r}")
    try:
        with serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=args.timeout,
            write_timeout=args.timeout,
        ) as connection:
            connection.reset_input_buffer()
            connection.reset_output_buffer()
            connection.write(payload)
            connection.flush()
            time.sleep(max(0.0, args.response_delay))
            response = read_response(connection)
    except (serial.SerialException, OSError, ValueError) as exc:
        print(f"FAIL: could not communicate with the Chemyx on {args.port}: {exc}")
        return 1

    text = response.decode("ascii", errors="replace").strip()
    print(f"RX raw: {response!r}")
    print(f"RX text: {text or '(empty)'}")
    if not response:
        print("FAIL: the port opened, but the pump returned no response.")
        return 1

    print("PASS: the laptop sent HELP and received a Chemyx response.")
    print("No pump movement command was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
