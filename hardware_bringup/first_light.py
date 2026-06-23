"""
hardware_bringup / first_light.py
=================================
The FIRST real movement of the pump, made as safe as possible.

It runs a tiny infuse + withdraw using the small "first light" amounts from
config.py, verifying the pump's echo at every step and ABORTING immediately on
any mismatch (a mismatch means a value was out of range and didn't take).

>>> RUN THIS WITH THE SYRINGE REMOVED — pump EMPTY and DRY, nothing connected
    downstream. The goal is to confirm motion and direction, not to move fluid.

RUN
---
    python hardware_bringup/first_light.py            # real pump (asks you to type a word)
    python hardware_bringup/first_light.py --mock     # no hardware, exits 0, nothing moves

Prerequisites (see BRINGUP.md): list_ports.py to find the port, set CHEMYX_PORT
(or config_local.py), match the baud, and get a good test_connection.py first.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from pump import Pump, PumpConnectionError, EchoMismatchError

USE_MOCK = "--mock" in sys.argv

# The exact word the operator must type to authorise real movement.
CONFIRM_WORD = "MOVE"


def loud_safety_header():
    bar = "!" * 72
    print(bar)
    print("!!  FIRST LIGHT — the pump is about to MOVE for the first time.")
    print("!!")
    print("!!  >>> REMOVE THE SYRINGE.  Run the pump EMPTY and DRY.            <<<")
    print("!!  >>> Disconnect anything downstream (tubing / needle / vessel). <<<")
    print("!!")
    print("!!  This performs a tiny INFUSE then WITHDRAW just to confirm the")
    print("!!  motor turns and the direction is correct. It is NOT for dispensing.")
    print(bar)
    print()


def get_confirmation():
    """Real movement requires the operator to TYPE a specific word (not Enter)."""
    if USE_MOCK:
        print("  [MOCK] No hardware, nothing moves — skipping typed confirmation.")
        return True
    if not sys.stdin.isatty():
        print("  [ABORT] No interactive terminal to confirm. Run this directly in a")
        print("          terminal so you can type the confirmation word.")
        return False
    print(f"  The pump WILL physically move. With the syringe removed, type the")
    print(f"  word  {CONFIRM_WORD}  (exactly, in capitals) and press Enter to proceed.")
    print(f"  Anything else aborts.")
    answer = input("  Confirm > ").strip()
    if answer != CONFIRM_WORD:
        print("  [ABORT] Confirmation word not matched — nothing was moved.")
        return False
    return True


def step(label, func):
    """Run one pump call, print its echo, and let echo-mismatch abort upward."""
    echo = func()
    print(f"  [ OK ] {label:<28} echo -> {echo!r}")
    return echo


def run():
    loud_safety_header()

    vol = config.FIRST_LIGHT_VOLUME
    rate = config.FIRST_LIGHT_RATE
    units_name = config.UNITS[config.DEFAULT_UNITS]
    print(f"  Mode    : {'MOCK (no hardware)' if USE_MOCK else 'REAL hardware'}")
    print(f"  Port    : {config.PORT} @ {config.BAUD_RATE} baud, channel {config.CHANNEL}")
    print(f"  Syringe : {config.DIAMETER} mm   (REMOVED for first light!)")
    print(f"  Move    : {vol} mL at {rate} {units_name}  (tiny, on purpose)")
    print()

    if not get_confirmation():
        return 1

    try:
        with Pump(mock=USE_MOCK) as pump:
            print()
            print("  --- connect & greet ---")
            step("help", pump.help)

            print("  --- configure (each echo verified) ---")
            step("set units", lambda: pump.set_units(config.DEFAULT_UNITS))
            step("set diameter", lambda: pump.set_diameter(config.DIAMETER))
            step("set rate", lambda: pump.set_rate(rate))

            print(f"  --- INFUSE {vol} mL (positive volume) ---")
            step(f"infuse {vol}", lambda: pump.infuse(vol))
            step("stop", pump.stop)

            print("  --- pause ---")
            step("pause", pump.pause)

            print(f"  --- WITHDRAW {vol} mL (negative volume) ---")
            step(f"withdraw {vol}", lambda: pump.withdraw(vol))
            step("stop", pump.stop)

    except EchoMismatchError as exc:
        print()
        print(f"  [ABORT] Echo mismatch — the pump did NOT accept a value: {exc}")
        print("          The port closed automatically. Fix the value (range) and retry.")
        return 1
    except ValueError as exc:
        print()
        print(f"  [ABORT] A value was rejected before sending: {exc}")
        return 1
    except PumpConnectionError as exc:
        print()
        print(f"  [ABORT] {exc}")
        return 1

    print()
    print("=" * 72)
    print("  FIRST LIGHT COMPLETE — motor moved both directions, all echoes verified.")
    print("  Port closed cleanly. You now have confirmed control of the pump.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(run())
