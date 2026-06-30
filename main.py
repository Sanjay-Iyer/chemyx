"""
main.py — Full INFUSE then WITHDRAW demo for the Chemyx Fusion 4000X.

This is the "real test": it connects, configures the pump, INFUSES a small safe
volume, stops, pauses, then WITHDRAWS the same volume, stops and disconnects —
printing clear status at every step.

SAFETY
------
* Volumes and rates default to the small, conservative values in config.py.
* The pump WILL physically move when run against real hardware, so the script
  asks you to confirm before any movement.  Make sure the syringe, tubing and
  waste/collection vessel are set up first.
* --mock runs the whole thing against the emulator (nothing moves).
* --yes skips the confirmation prompt (useful for unattended / mock runs).

RUN IT
------
Dry run (no hardware):   python main.py --mock --yes
Real hardware:           python main.py
"""

import os
import sys
import time

import config
from pump import Pump, PumpConnectionError, EchoMismatchError

USE_MOCK = "--mock" in sys.argv or os.environ.get("CHEMYX_MOCK") == "1"
ASSUME_YES = "--yes" in sys.argv or os.environ.get("CHEMYX_YES") == "1"

# Small, safe demo amounts (pulled from config so there's one source of truth).
DEMO_VOLUME = config.DEFAULT_VOLUME  # e.g. 0.5 mL
DEMO_RATE = config.DEFAULT_RATE  # e.g. 1.0 mL/min
DEMO_UNITS = config.DEFAULT_UNITS  # mL/min
DEMO_DIAMETER = config.DIAMETER  # mm

# In real runs we pause between infuse and withdraw; keep mock runs snappy.
SETTLE_SECONDS = 0.0 if USE_MOCK else 2.0


def banner(text):
    print()
    print("=" * 64)
    print(text)
    print("=" * 64)


def confirm_movement():
    """Return True if it's safe to proceed with physical movement."""
    if USE_MOCK:
        print("  [MOCK] No physical movement — emulator only.")
        return True
    if ASSUME_YES:
        print("  [--yes] Confirmation skipped by flag.")
        return True
    if not sys.stdin.isatty():
        print(
            "  [ABORT] No interactive terminal to confirm movement; "
            "re-run with --yes if you really mean it."
        )
        return False
    print()
    print("  !! The pump is about to MOVE physically.")
    print(
        f"  !! It will INFUSE then WITHDRAW {DEMO_VOLUME} mL at "
        f"{DEMO_RATE} {config.UNITS[DEMO_UNITS]}."
    )
    answer = input("  Type 'yes' to continue: ").strip().lower()
    return answer == "yes"


def run_demo():
    banner("Chemyx Fusion 4000X — INFUSE / WITHDRAW demo")
    print(f"  Mode     : {'MOCK (no hardware)' if USE_MOCK else 'REAL hardware'}")
    print(f"  Port     : {config.PORT} @ {config.BAUD_RATE} baud")
    print(f"  Channel  : {config.CHANNEL}  (0 = default/both)")
    print(f"  Syringe  : {DEMO_DIAMETER} mm diameter")
    print(f"  Move     : {DEMO_VOLUME} mL at {DEMO_RATE} {config.UNITS[DEMO_UNITS]}")

    # Safety gate BEFORE we open anything / move anything.
    if not confirm_movement():
        print("\n  Aborted by user — nothing was moved.")
        return 1

    try:
        with Pump(mock=USE_MOCK) as pump:
            # --- Configure -------------------------------------------------
            banner("Configure")
            print(f"  units    -> {pump.set_units(DEMO_UNITS)!r}")
            print(f"  diameter -> {pump.set_diameter(DEMO_DIAMETER)!r}")
            print(f"  rate     -> {pump.set_rate(DEMO_RATE)!r}")

            # --- INFUSE ----------------------------------------------------
            banner(f"INFUSE {DEMO_VOLUME} mL  (positive volume)")
            print(f"  infuse   -> {pump.infuse(DEMO_VOLUME)!r}")
            print("  ...infusing...")
            time.sleep(SETTLE_SECONDS)
            print(f"  stop     -> {pump.stop()!r}")

            # --- Pause between operations ----------------------------------
            banner("Pause before reversing")
            print(f"  pause    -> {pump.pause()!r}")
            time.sleep(SETTLE_SECONDS)

            # --- WITHDRAW --------------------------------------------------
            banner(f"WITHDRAW {DEMO_VOLUME} mL  (negative volume)")
            print(f"  withdraw -> {pump.withdraw(DEMO_VOLUME)!r}")
            print("  ...withdrawing...")
            time.sleep(SETTLE_SECONDS)
            print(f"  stop     -> {pump.stop()!r}")

    except EchoMismatchError as exc:
        print(f"\n  [FAIL] Pump rejected a value (echo mismatch): {exc}")
        return 1
    except ValueError as exc:
        print(f"\n  [FAIL] Value rejected before sending: {exc}")
        return 1
    except PumpConnectionError as exc:
        print(f"\n  [FAIL] {exc}")
        return 1

    banner("Done — port closed cleanly")
    print("  Infuse + withdraw demo completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(run_demo())
