"""
example_06_withdraw.py  ->  mirrors ../plain_text_commands/06_withdraw.txt
Operation: WITHDRAW (full sequence). Plain-text commands sent, in order:
    set units 0
    set diameter 4.5
    set rate 1.0
    set volume -0.5     <-- NEGATIVE = withdraw
    start

SAFETY: this starts physical movement on real hardware. Run with --mock for a
dry run, or add --yes to confirm you really want to move the pump.
    python example_06_withdraw.py --mock
    python example_06_withdraw.py --yes
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv
ASSUME_YES = "--yes" in sys.argv

if not USE_MOCK and not ASSUME_YES:
    sys.exit("Refusing to move the pump without confirmation. "
             "Re-run with --mock (dry run) or --yes (real movement).")

with Pump(mock=USE_MOCK) as pump:
    print("set units 0     ->", repr(pump.set_units(0)))
    print("set diameter    ->", repr(pump.set_diameter(4.5)))
    print("set rate 1.0    ->", repr(pump.set_rate(1.0)))
    # withdraw() sends a NEGATIVE volume ("set volume -0.5") then "start"
    print("set volume -0.5 + start ->", repr(pump.withdraw(0.5)))
    print("WITHDRAW sequence complete.")
