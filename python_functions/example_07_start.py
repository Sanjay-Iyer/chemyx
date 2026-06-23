"""
example_07_start.py  ->  mirrors ../plain_text_commands/07_start.txt
Operation: START.   Plain-text command sent:   start
SAFETY: starts movement on real hardware -- use --mock for a dry run.
    python example_07_start.py --mock
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv

with Pump(mock=USE_MOCK) as pump:
    # Sends "start\r" -> begins the programmed move; echo: "pump start"
    echo = pump.start()
    print(f"sent: start   ->   pump echoed: {echo!r}")
