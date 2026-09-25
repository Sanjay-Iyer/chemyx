"""Non-motion READY, PING/PONG, and IDENTITY diagnostic for firmware 1.2.1."""

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


EXPECTED_IDENTITY = {
    "device": "needle_controller",
    "board": "uno_r4_minima",
    "version": "1.2.1",
}


def identity_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check READY, PING/PONG, and read-only IDENTITY on needle firmware 1.2.1."
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
    args = parser.parse_args(argv)

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
            print("Serial port opened.")
            deadline = time.monotonic() + max(0.1, args.startup_seconds)
            ready_fields: dict[str, str] | None = None
            while time.monotonic() < deadline:
                raw = connection.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").strip()
                print(f"RX startup: {line}")
                if line.startswith("READY "):
                    ready_fields = identity_fields(line)
                    break

            if ready_fields is None:
                print("READY: not observed during the startup window.")
            elif all(ready_fields.get(key) == value for key, value in EXPECTED_IDENTITY.items()):
                print("READY: expected device, board, and firmware version observed.")
            else:
                print(f"READY: unexpected identity {ready_fields!r}.")

            payload = b"1 PING\n"
            print(f"TX: {payload!r}")
            connection.write(payload)
            connection.flush()

            deadline = time.monotonic() + max(0.1, args.timeout)
            ack_seen = False
            pong_seen = False
            while time.monotonic() < deadline:
                raw = connection.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").strip()
                print(f"RX: {line}")
                if line == "ACK 1 PING":
                    ack_seen = True
                    continue
                if line == "DONE 1 PONG" and ack_seen:
                    print("PASS: the laptop exchanged sequenced PING/PONG with the Arduino.")
                    pong_seen = True
                    break
            if not pong_seen:
                print("FAIL: expected ACK 1 PING then DONE 1 PONG were not received.")
                print("No motor command was sent.")
                return 1

            payload = b"2 IDENTITY\n"
            print(f"TX: {payload!r}")
            connection.write(payload)
            connection.flush()
            deadline = time.monotonic() + max(0.1, args.timeout)
            identity_ack_seen = False
            queried_fields: dict[str, str] | None = None
            while time.monotonic() < deadline:
                raw = connection.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").strip()
                print(f"RX: {line}")
                if line == "ACK 2 IDENTITY":
                    identity_ack_seen = True
                elif line.startswith("DONE 2 ") and identity_ack_seen:
                    queried_fields = identity_fields(line)
                    break
                elif line.startswith("ERR 2 "):
                    break

            if queried_fields is None:
                print("IDENTITY: no valid response; firmware version is unverified.")
            elif all(queried_fields.get(key) == value for key, value in EXPECTED_IDENTITY.items()) and queried_fields.get("driver") == "DM542S":
                print("IDENTITY: expected device, board, firmware version, and driver confirmed.")
            else:
                print(f"IDENTITY: unexpected identity {queried_fields!r}.")

            print("No motor command was sent.")
            if ready_fields is None or queried_fields is None:
                return 1
            if any(ready_fields.get(key) != value or queried_fields.get(key) != value
                   for key, value in EXPECTED_IDENTITY.items()):
                return 1
            return 0 if queried_fields.get("driver") == "DM542S" else 1
    except (serial.SerialException, OSError, ValueError) as exc:
        print(f"FAIL: could not communicate with the Arduino on {args.port}: {exc}")
        print("No motor command was sent.")
        return 1

    print("No motor command was sent.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
