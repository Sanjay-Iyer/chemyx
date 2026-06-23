"""
example_09_pause.py  ->  mirrors ../plain_text_commands/09_pause.txt
Operation: PAUSE.   Plain-text command sent:   pause
    python example_09_pause.py --mock
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv

with Pump(mock=USE_MOCK) as pump:
    # Sends "pause\r" -> pauses the move (resume later with "start"); echo: "pump pause"
    echo = pump.pause()
    print(f"sent: pause   ->   pump echoed: {echo!r}")
