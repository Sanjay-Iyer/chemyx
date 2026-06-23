"""
example_02_set_diameter.py  ->  mirrors ../plain_text_commands/02_set_diameter.txt
Operation: SET SYRINGE DIAMETER.   Plain-text command sent:   set diameter 4.5
Run dry (no hardware):   python example_02_set_diameter.py --mock
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv

with Pump(mock=USE_MOCK) as pump:
    # Sends "set diameter 4.5\r"; valid range 0.103-40.000 mm; echo: "diameter = 4.5"
    echo = pump.set_diameter(4.5)
    print(f"sent: set diameter 4.5   ->   pump echoed: {echo!r}")
