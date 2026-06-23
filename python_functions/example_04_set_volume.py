"""
example_04_set_volume.py  ->  mirrors ../plain_text_commands/04_set_volume.txt
Operation: SET VOLUME.   Plain-text commands:   set volume 0.5   (infuse)
                                                set volume -0.5  (withdraw)
The SIGN sets direction: POSITIVE = infuse, NEGATIVE = withdraw.
Run dry (no hardware):   python example_04_set_volume.py --mock
"""
import sys
from pump import Pump

USE_MOCK = "--mock" in sys.argv

with Pump(mock=USE_MOCK) as pump:
    # Positive volume -> infuse direction; echo: "volume = 0.5"
    echo_pos = pump.set_volume(0.5)
    print(f"sent: set volume 0.5    ->   pump echoed: {echo_pos!r}  (infuse direction)")

    # Negative volume -> withdraw direction; echo: "volume = -0.5"
    echo_neg = pump.set_volume(-0.5)
    print(f"sent: set volume -0.5   ->   pump echoed: {echo_neg!r}  (withdraw direction)")
