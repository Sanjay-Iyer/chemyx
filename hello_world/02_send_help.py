"""
hello_world / 02_send_help.py
=============================
GOAL: Send the pump the built-in `help` command and print whatever it sends
back.  This is the simplest possible round-trip: host -> pump -> host.  It
proves the pump is not just reachable but actually TALKING at the configured
baud rate.  It still moves NOTHING.

RUN IT
------
Real hardware:   python hello_world/02_send_help.py
No hardware:     python hello_world/02_send_help.py --mock

If you see a response, your baud rate matches.  If the port opens but you get an
empty / garbled response, the baud rate in config.py probably does NOT match the
pump's screen — see the README troubleshooting section.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from pump import Pump, PumpConnectionError

USE_MOCK = "--mock" in sys.argv or os.environ.get("CHEMYX_MOCK") == "1"


def main():
    print("=" * 60)
    print("STEP 2: Send 'help' and read the response")
    print("=" * 60)
    print(f"  Mode: {'MOCK (no hardware)' if USE_MOCK else 'REAL hardware'}")
    print()

    try:
        # The context manager opens the port and guarantees it closes again.
        with Pump(mock=USE_MOCK) as pump:
            print("  Sending: help")
            response = pump.help()      # -> sends "help\r", reads the reply
            print("  Pump replied:")
            print("  " + "-" * 56)
            for line in response.splitlines() or ["(no response)"]:
                print(f"  | {line}")
            print("  " + "-" * 56)

            if not response:
                print("  [WARN] Empty response — check the baud rate matches the pump.")
                return 1
    except PumpConnectionError as exc:
        print(f"  [FAIL] {exc}")
        return 1

    print()
    print("  Success — the pump answered, so communication is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
