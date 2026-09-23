"""USB serial PING/PONG test for canonical needle-controller firmware 1.1.0."""

from __future__ import annotations

import argparse
import time


def list_ports() -> int:
    try:
        from serial.tools import list_ports as serial_list_ports
    except ImportError:
        print("ERROR: pyserial is not installed.")
        return 2

    ports = sorted(serial_list_ports.comports(), key=lambda item: item.device)
    if not ports:
        print("No serial ports were found.")
        return 1

    print("PORT       DESCRIPTION                         HARDWARE ID")
    for item in ports:
        print(f"{item.device:<10} {item.description or '':<35} {item.hwid or ''}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a non-motion PING to canonical needle firmware 1.1.0."
    )
    parser.add_argument("--port", help="verified Arduino COM port, for example COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--startup-seconds",
        type=float,
        default=2.0,
        help="time allowed for the board to reset after opening the port",
    )
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

    print(f"Opening Arduino port {args.port} at {args.baud} baud...")
    try:
        with serial.Serial(
            port=args.port,
            baudrate=args.baud,
            timeout=0.25,
            write_timeout=2.0,
        ) as connection:
            deadline = time.monotonic() + max(0.1, args.startup_seconds)
            ready_seen = False
            while time.monotonic() < deadline:
                raw = connection.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").strip()
                print(f"RX startup: {line}")
                if line == (
                    "READY device=needle_controller "
                    "board=uno_r4_minima version=1.1.0"
                ):
                    ready_seen = True
                    break

            payload = b"1 PING\n"
            print(f"TX: {payload!r}")
            connection.write(payload)
            connection.flush()

            deadline = time.monotonic() + max(0.1, args.timeout)
            received: list[str] = []
            ack_seen = False
            while time.monotonic() < deadline:
                raw = connection.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").strip()
                received.append(line)
                print(f"RX: {line}")
                if line == "ACK 1 PING":
                    ack_seen = True
                    continue
                if line == "DONE 1 PONG" and ack_seen:
                    print("PASS: the laptop exchanged sequenced PING/PONG with the Arduino.")
                    if ready_seen:
                        print("Firmware identity confirmed: needle_controller 1.1.0.")
                    else:
                        print("WARNING: PING passed, but the startup READY identity was not observed.")
                    print("No motor command was sent.")
                    return 0
    except (serial.SerialException, OSError, ValueError) as exc:
        print(f"FAIL: could not communicate with the Arduino on {args.port}: {exc}")
        return 1

    print("FAIL: the expected 'ACK 1 PING' then 'DONE 1 PONG' replies were not received.")
    if received:
        print("The board responded, but it may have different firmware loaded.")
    else:
        print("No complete serial line was received.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
