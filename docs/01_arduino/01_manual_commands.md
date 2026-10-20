# 01 — Arduino manual commands and configuration

[Arduino index](00_START_HERE.md) | [All documentation](../00_START_HERE.md)

| Item | File |
|---|---|
| Python command | [needle_control.py](../../arduino/scripts/needle_control.py) |
| Current calibrated configuration | [arduino_real_COM3.yaml](../../arduino/configs/arduino_real_COM3.yaml) |
| Runtime position estimate | `runs/arduino/needle_state.json` |
| Detailed recovery | [02 — HOME recovery](02_home_recovery.md) |

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

UP/DOWN move at once, with no confirmation prompt, and need no HOME. They are
relative +1/-1 using the existing 200-step increment and reviewed direction.
A pre-HOME relative count is uncalibrated and cannot authorize automation.

`--steps N` (UP/DOWN only) moves N motor steps instead of 200 for that one
command, at most 1600 (the configured -3 to +5 travel). A multiple of 200 counts
as whole increments and keeps the count and any saved HOME. Any other size
resets the count and clears a saved HOME, so run `set-home` again once the
needle is where you want HOME. Speed and acceleration stay as configured.
`set-home` and `home` still ask one confirmation. Automated HOME=DOWN=0
is the sampling location in the flask; UP=1 is raised. `set-home` records the
current physically verified stopped location without movement. `home` uses
the calibrated absolute zero or requires supervised physical recovery when
the reference is unknown. Interrupted motion remains uncertain.

The configuration retains bounds -3 through +5. There are no physical limit
switches or encoder measurements. Inspect clear travel before jogging; retain
the commissioned wiring, firmware and calibration. See the recovery guide
for uncertain-state handling rather than changing the reference to bypass a limit.
