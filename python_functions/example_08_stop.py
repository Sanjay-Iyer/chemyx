"""
example_08_stop.py  ->  mirrors ../plain_text_commands/08_stop.txt
Operation: STOP.   Plain-text command sent:   stop
    python example_08_stop.py --mock
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv

with Pump(mock=USE_MOCK) as pump:
    # Sends "stop\r" -> stops the pump immediately; echo: "pump stop"
    echo = pump.stop()
    print(f"sent: stop   ->   pump echoed: {echo!r}")
