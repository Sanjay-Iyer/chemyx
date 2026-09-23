# Arduino needle controller — supervised D3/D4 demo

The active UNO R4 Minima sketch is
`arduino/firmware/needle_controller/needle_controller.ino` (version 1.2.0).
It uses the **already-validated D3 STEP / D4 DIR wiring**. Do not move wires,
add an ENABLE wire, add upper/lower switches, or change DM542S settings for
this demo. Older sketches and the `dm542s_hello_world` scripts are historical
bring-up tools with a different serial protocol; do not mix them with this
controller.

Python's `TrackedNeedle` in `arduino/python/needle_state.py` owns logical
position: HOME=0, UP positive, DOWN negative. It stores an atomic JSON estimate
at `runs/arduino/needle_state.json` by default. The position is not measured:
there is no encoder or physical HOME. Missing/corrupt state and failed or
interrupted motion block further movement until inspection and explicit
`confirm-home`. A valid saved estimate survives Python/computer restarts if
the physical axis has not moved independently.
If the JSON is corrupt, the explicit `confirm-home` action first preserves it
as a `.corrupt-*.bak` file, then establishes a new inspected reference.

Configure the Arduino COM port, physical UP direction, steps per logical unit,
speed, named positions, and software limits in an ignored local copy of
`arduino/configs/arduino.example.yaml`. Its default `steps_per_unit` and
`up_step_sign` are deliberately null: inspect/calibrate on the rig before any
live move. The example -3/+5 bounds are **not** verified physical limits.

From the repository root, in the instrument laptop's `air` environment:

```powershell
Copy-Item arduino\configs\arduino.example.yaml arduino\configs\arduino.local.yaml
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\arduino.local.yaml --live
python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino.local.yaml
python arduino\scripts\needle_control.py confirm-home --live --config arduino\configs\arduino.local.yaml
python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino.local.yaml
python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino.local.yaml
python arduino\scripts\needle_control.py return-home --live --config arduino\configs\arduino.local.yaml
```

The physical confirmation step must follow an actual needle inspection. Live
motion prompts for an exact typed confirmation. `status` never moves the
needle. `stop` is also available, but the existing 24 V driver-power
disconnect is the physical stop. `ENABLE`/`DISABLE` in this firmware only arm
software; they do not control the driver's unwired ENA input.

For offline checks, use `--mock` instead of `--live`, or run
`python -m pytest -q arduino/tests`. The staged Test 2, Test 3, and Test 4
scripts remain available; see `arduino/docs/TEST_02_GUIDE.md`,
`arduino/docs/TEST_03_GUIDE.md`, and `arduino/docs/TEST_04_GUIDE.md`.
Firmware upload commands and pin details are in `arduino/docs/FIRMWARE.md`.

This is an attended demonstration, not an unattended production safety system.
Software bounds cannot prevent a collision after missed steps, unpowered drift,
or manual movement. Inspect the physical axis after any uncertainty.
