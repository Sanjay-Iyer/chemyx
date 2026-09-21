"""USB serial PING/PONG smoke test for the bundled Arduino sketch."""

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
        description="Send PING to the non-motion Arduino smoke-test firmware."
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
            time.sleep(max(0.0, args.startup_seconds))
            connection.reset_input_buffer()
            payload = b"PING\n"
            print(f"TX: {payload!r}")
            connection.write(payload)
            connection.flush()

            deadline = time.monotonic() + max(0.1, args.timeout)
            received: list[str] = []
            while time.monotonic() < deadline:
                raw = connection.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").strip()
                received.append(line)
                print(f"RX: {line}")
                if line == "PONG ARDUINO_SMOKE_TEST":
                    print("PASS: the laptop exchanged PING/PONG with the Arduino.")
                    print("The smoke-test firmware contains no motor commands or motor-pin setup.")
                    return 0
    except (serial.SerialException, OSError, ValueError) as exc:
        print(f"FAIL: could not communicate with the Arduino on {args.port}: {exc}")
        return 1

    print("FAIL: the expected 'PONG ARDUINO_SMOKE_TEST' reply was not received.")
    if received:
        print("The board responded, but it may have different firmware loaded.")
    else:
        print("No complete serial line was received.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
