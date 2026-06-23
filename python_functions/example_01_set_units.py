"""
example_01_set_units.py  ->  mirrors ../plain_text_commands/01_set_units.txt
Operation: SET UNITS.   Plain-text command sent:   set units 0
Run dry (no hardware):   python example_01_set_units.py --mock
Run on real pump:        python example_01_set_units.py
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv

with Pump(mock=USE_MOCK) as pump:
    # Sends "set units 0\r"  (0=mL/min 1=mL/hr 2=uL/min 3=uL/hr); echo: "units = mL/min"
    echo = pump.set_units(0)
    print(f"sent: set units 0   ->   pump echoed: {echo!r}")
