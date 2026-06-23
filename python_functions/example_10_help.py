"""
example_10_help.py  ->  mirrors ../plain_text_commands/10_help.txt
Operation: HELP (send 'help', read the response).   Plain-text command sent:   help
    python example_10_help.py --mock
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv

with Pump(mock=USE_MOCK) as pump:
    # Sends "help\r" and prints whatever the pump replies (proves comms + baud match)
    response = pump.help()
    print("sent: help   ->   pump replied:")
    for line in response.splitlines() or ["(no response)"]:
        print(f"   | {line}")
