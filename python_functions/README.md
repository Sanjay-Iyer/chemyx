# python_functions/

**THIS DIRECTORY = Python functions that send commands to the pump via pyserial.
Run these with Python.**

(For the same operations as raw text you type straight into a serial terminal —
no Python — see [`../plain_text_commands/`](../plain_text_commands/).)

---

## What's here

| File | What it is |
|------|------------|
| [`pump.py`](pump.py) | The reusable `Pump` class. **Edit `PORT` / `BAUD_RATE` / `CHANNEL` at the top.** Handles `\r` termination, echo verification, dual-channel prefixing, and always closes the port (context manager). |
| `example_01_set_units.py` … `example_10_help.py` | One tiny, runnable script per operation. Each mirrors the matching file in `../plain_text_commands/`. |

Each `Pump` method's comment shows the **exact plain-text command** it sends, so
this folder lines up 1:1 with `../plain_text_commands/`.

## Requirements

```
pip install pyserial
```

## Configure

Open [`pump.py`](pump.py) and edit the config block at the very top:

```python
PORT = "COM3"        # your COM port / /dev/tty.* device
BAUD_RATE = 9600     # MUST match the pump's screen
CHANNEL = 0          # 0 = default/both, 1 = channel 1, 2 = channel 2 (dual-channel 4000X)
```

## Run

Every example takes `--mock` to run with **no hardware** (built-in emulator):

```bash
python example_01_set_units.py --mock      # dry run, no pump needed
python example_01_set_units.py             # talk to the real pump
```

The two movement examples (`05_infuse`, `06_withdraw`) physically move a real
pump, so they require `--mock` (dry run) or `--yes` (confirm real movement).

## Use the class in your own code

```python
from pump import Pump

with Pump() as pump:                 # opens the port; closes it automatically on exit
    pump.set_units("mL/min")         # sends:  set units 0
    pump.set_diameter(4.5)           # sends:  set diameter 4.5   (verifies the echo)
    pump.set_rate(1.0)               # sends:  set rate 1.0
    pump.infuse(0.5)                 # sends:  set volume 0.5  then  start   (POSITIVE = infuse)
    pump.stop()                      # sends:  stop
    pump.withdraw(0.5)               # sends:  set volume -0.5 then  start   (NEGATIVE = withdraw)
    pump.stop()

# Target one drive of the dual-channel 4000X:
#   pump.set_rate(5.0, channel=2)    # sends:  2 set rate 5.0
```

Every `set_*` reads the pump's echo back and raises `EchoMismatchError` if the
returned value doesn't match what was sent (which is what happens when a value
is out of range — diameter `0.103–40.000 mm`, flow up to `~170.5 mL/min`).
