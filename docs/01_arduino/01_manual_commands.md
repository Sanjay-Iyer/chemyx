# 01 — Arduino manual commands and configuration

[Arduino index](00_START_HERE.md) | [All documentation](../00_START_HERE.md)

| Item | File |
|---|---|
| Python command | [needle_control.py](../../arduino/scripts/needle_control.py) |
| Current calibrated configuration | [arduino_real_COM3.yaml](../../arduino/configs/arduino_real_COM3.yaml) |
| Saved position (one integer each) | `runs/arduino/simple_needle_position.txt` (counter), `runs/arduino/simple_needle_steps.txt` (motor steps from HOME) |
| Shared code (also used by 20/21/22) | [simple_needle.py](../../arduino/python/simple_needle.py) |

From the repository root on WORK, with the rig physically inspected, choose
one command as needed:

```powershell
cd C:\code\chemyx_pump
```

Status (contacts the Arduino, no movement):

```powershell
python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino_real_COM3.yaml
```

UP one increment (moves at once):

```powershell
python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino_real_COM3.yaml
```

DOWN one increment (moves at once):

```powershell
python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino_real_COM3.yaml
```

UP 50 motor steps:

```powershell
python arduino\scripts\needle_control.py up --steps 50 --live --config arduino\configs\arduino_real_COM3.yaml
```

DOWN 50 motor steps:

```powershell
python arduino\scripts\needle_control.py down --steps 50 --live --config arduino\configs\arduino_real_COM3.yaml
```

Set HOME at the current stopped position (no movement):

```powershell
python arduino\scripts\needle_control.py set-home --live --config arduino\configs\arduino_real_COM3.yaml
```

Return to HOME:

```powershell
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
```

STOP:

```powershell
python arduino\scripts\needle_control.py stop --live --config arduino\configs\arduino_real_COM3.yaml
```

| Command | Position counter | Motor steps from HOME |
|---|---|---|
| `set-home` (one `y`, no movement) | 0 | 0 |
| `up` / `down` (moves at once) | +1 / −1 per completed command | ± the steps moved (200, or `--steps N`) |
| `home` (one `y`) | 0 | moves back by the saved steps, then 0 |
| `status` | shown | shown, with firmware `moving`/`fault` |
| `stop` | unchanged | unchanged |

`--steps N` (UP/DOWN only) moves N motor steps instead of 200 for that one
command, at most 1600 (the configured -3 to +5 travel). The counter still
changes by one; `home` uses the saved motor steps, so mixed step sizes return
correctly. Speed and acceleration stay as configured. HOME = DOWN = 0 is the
sampling location in the flask; UP = 1 is raised. Scripts 20, 21 and 22 read and
update the same two files.

The files are written only after the Arduino reports the move finished. No
fault, validity or recovery state is saved, so an earlier failure never blocks
a command. If a move is interrupted or fails, the command says so: check the
needle by eye and run `set-home` before relying on the saved position. Without
a `set-home`, UP/DOWN still move but nothing is saved. Firmware travel bounds,
the 120-second move timeout and STOP are unchanged; there are no physical limit
switches or encoder. The old `needle_state.json` and HOME journals are no longer
read or written by this script (older workflows 04–14 still use them).
