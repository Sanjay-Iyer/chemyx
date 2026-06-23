"""
example_03_set_rate.py  ->  mirrors ../plain_text_commands/03_set_rate.txt
Operation: SET FLOW RATE.   Plain-text command sent:   set rate 1.0
Run dry (no hardware):   python example_03_set_rate.py --mock
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv

with Pump(mock=USE_MOCK) as pump:
    # Sends "set rate 1.0\r" (in the current units, up to ~170.5 mL/min); echo: "rate = 1.0"
    echo = pump.set_rate(1.0)
    print(f"sent: set rate 1.0   ->   pump echoed: {echo!r}")
