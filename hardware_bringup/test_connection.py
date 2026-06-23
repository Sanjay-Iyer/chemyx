"""
hardware_bringup / test_connection.py
=====================================
Open the configured serial port, send the pump 'help', and print its raw reply.
NOTHING moves — this is purely a "can the laptop talk to the pump?" check.

It uses the SAME Pump class and config as the rest of the repo, so a successful
run here means main.py / first_light.py will use a working link too.

RUN
---
    python hardware_bringup/test_connection.py            # real pump (uses config)
    python hardware_bringup/test_connection.py --mock     # no hardware, exits 0

Set the port for this machine first (env var CHEMYX_PORT or config_local.py).
Find the port with list_ports.py. Match the baud rate to the pump's screen.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from pump import Pump, PumpConnectionError

USE_MOCK = "--mock" in sys.argv


def main():
    print("=" * 70)
    print(" CONNECTION TEST  (send 'help', read reply — no movement)")
    print("=" * 70)
    print(f"  Mode    : {'MOCK (no hardware)' if USE_MOCK else 'REAL hardware'}")
    print(f"  Port    : {config.PORT}")
    print(f"  Baud    : {config.BAUD_RATE}")
    print(f"  Channel : {config.CHANNEL}  (0 = default/both)")
    print()

    try:
        with Pump(mock=USE_MOCK) as pump:
            print("  [ OK ] Port opened.")
            print("  Sending: help")
            reply = pump.help()
            print("  Pump replied:")
            print("  " + "-" * 66)
            for line in (reply.splitlines() or ["(no reply)"]):
                print(f"  | {line}")
            print("  " + "-" * 66)

            if not reply.strip():
                print()
                print("  [WARN] Port opened but the pump said NOTHING. Almost always a")
                print("         BAUD MISMATCH — set CHEMYX_BAUD (or config_local.py) to")
                print("         exactly the baud rate shown on the pump's screen. Also")
                print("         check you're using a STRAIGHT-THROUGH (not null-modem)")
                print("         serial cable.")
                return 1
    except PumpConnectionError as exc:
        print(f"  [FAIL] {exc}")
        print()
        print("  Plain-English fixes:")
        print("    * 'not found'      -> wrong COM number, cable unplugged, or the")
        print("                          USB-to-serial driver isn't installed. Run")
        print("                          list_ports.py and set CHEMYX_PORT correctly.")
        print("    * 'access denied'  -> another program is holding the port. Close")
        print("                          the Chemyx GUI / PuTTY / TeraTerm / any other")
        print("                          serial monitor, then retry.")
        print("    * still stuck      -> try a different USB port/cable; confirm the")
        print("                          pump is powered on.")
        return 1

    print()
    print("  SUCCESS — the pump answered. The link works at this baud rate.")
    print("  You can now run first_light.py (with the syringe removed/empty).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
