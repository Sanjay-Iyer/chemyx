"""
hello_world / 03_set_parameters.py
==================================
GOAL: Set the units, syringe diameter and flow rate, and VERIFY the pump echoed
back the same values we sent.  Still NO movement — start/stop are never called
here.

This is the important "trust but verify" step.  The Chemyx firmware echoes the
value it accepted (e.g. "diameter = 4.5").  The Pump class reads that echo and
raises EchoMismatchError if it doesn't match — which is exactly what happens
when a value is out of range.  So a clean run here means the pump genuinely
accepted your settings.

RUN IT
------
Real hardware:   python hello_world/03_set_parameters.py
No hardware:     python hello_world/03_set_parameters.py --mock
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from pump import Pump, PumpConnectionError, EchoMismatchError

USE_MOCK = "--mock" in sys.argv or os.environ.get("CHEMYX_MOCK") == "1"


def main():
    print("=" * 60)
    print("STEP 3: Set parameters and verify the echo (no movement)")
    print("=" * 60)
    print(f"  Mode      : {'MOCK (no hardware)' if USE_MOCK else 'REAL hardware'}")
    print(f"  Channel   : {config.CHANNEL}  (0 = default/both)")
    print(f"  Units     : {config.UNITS[config.DEFAULT_UNITS]}")
    print(f"  Diameter  : {config.DIAMETER} mm")
    print(f"  Rate      : {config.DEFAULT_RATE} {config.UNITS[config.DEFAULT_UNITS]}")
    print()

    try:
        with Pump(mock=USE_MOCK) as pump:
            print(f"  set units {config.DEFAULT_UNITS} -> {pump.set_units(config.DEFAULT_UNITS)!r}")
            print(f"  set diameter {config.DIAMETER} -> {pump.set_diameter(config.DIAMETER)!r}")
            print(f"  set rate {config.DEFAULT_RATE} -> {pump.set_rate(config.DEFAULT_RATE)!r}")
    except EchoMismatchError as exc:
        # The echo didn't match -> a value was out of range / not accepted.
        print(f"  [FAIL] Echo verification failed: {exc}")
        return 1
    except ValueError as exc:
        # We rejected the value locally before it ever reached the pump.
        print(f"  [FAIL] Value rejected before sending: {exc}")
        return 1
    except PumpConnectionError as exc:
        print(f"  [FAIL] {exc}")
        return 1

    print()
    print("  Success — every parameter was accepted and verified by the pump.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
