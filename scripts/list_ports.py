"""List serial ports visible to this laptop."""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401


def main() -> int:
    parser = argparse.ArgumentParser(description="List visible serial ports.")
    parser.add_argument("--mock", action="store_true", help="print an example only")
    args = parser.parse_args()

    if args.mock:
        print("PORT                 DESCRIPTION                         HWID")
        print("COM4                 USB Serial Port (example)            USB VID:PID=0403:6001")
        return 0

    try:
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is not installed, so real port listing is unavailable.")
        print("Install with: python -m pip install -r requirements.txt")
        return 1

    ports = sorted(list_ports.comports(), key=lambda port: port.device)
    if not ports:
        print("No serial ports found.")
        print("Check pump power, USB/RS232 cable, and USB-to-serial driver.")
        return 0

    print("PORT                 DESCRIPTION                         HWID")
    for port in ports:
        print(f"{port.device:<20} {port.description or '':<35} {port.hwid or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
