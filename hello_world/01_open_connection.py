"""
hello_world / 01_open_connection.py
===================================
GOAL: Open the serial port to the pump, confirm we're connected, then close it
cleanly.  This sends NO commands and moves NOTHING — it just proves that the
cable, port name and baud rate in config.py are correct.

RUN IT
------
Real hardware:   python hello_world/01_open_connection.py
No hardware:     python hello_world/01_open_connection.py --mock
                 (or set the env var CHEMYX_MOCK=1)

If this script fails, fix it before trying anything else — every later step
depends on a working connection.
"""

# --- make the project root importable when run from anywhere -----------------
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from pump import Pump, PumpConnectionError

# --mock on the command line OR CHEMYX_MOCK=1 in the environment -> emulator.
USE_MOCK = "--mock" in sys.argv or os.environ.get("CHEMYX_MOCK") == "1"


def main():
    print("=" * 60)
    print("STEP 1: Open the connection")
    print("=" * 60)
    print(f"  Port      : {config.PORT}")
    print(f"  Baud rate : {config.BAUD_RATE}")
    print(f"  Mode      : {'MOCK (no hardware)' if USE_MOCK else 'REAL hardware'}")
    print()

    # Build the pump.  Nothing happens on the wire until connect() is called.
    pump = Pump(mock=USE_MOCK)

    try:
        # Open the serial port.
        pump.connect()
    except PumpConnectionError as exc:
        # A clear, human-readable message (port not found / access denied / ...).
        print(f"  [FAIL] {exc}")
        return 1

    # If we got here the port is open.
    print(f"  [ OK ] Connected: is_connected = {pump.is_connected}")

    # Always close the port — even a 'hello world' should leave things tidy.
    pump.disconnect()
    print(f"  [ OK ] Disconnected: is_connected = {pump.is_connected}")
    print()
    print("  Success — the port opened and closed cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
