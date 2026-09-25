# Arduino needle controller — supervised D3/D4 demo

The active UNO R4 Minima sketch is
`arduino/firmware/needle_controller/needle_controller.ino` (version 1.2.1).
It uses the **already-validated D3 STEP / D4 DIR wiring**. Do not move wires,
add an ENABLE wire, add upper/lower switches, or change DM542S settings for
this demo. Older sketches and the `dm542s_hello_world` scripts are historical
bring-up tools with a different serial protocol; do not mix them with this
controller.

## Serial / READY troubleshooting

`READY device=needle_controller board=uno_r4_minima version=1.2.1` is the
initial USB identity handshake. The 1.2.1 sketch sends it once per serial-host
connection after a short non-blocking settling interval. Python verifies the
device, board, and firmware version before any Test 1 command. PING/PONG proves
serial communication, but **does not** prove which firmware is running.

Test 1 needs only Arduino USB; no motor or DM542S is required. Close Arduino
Serial Monitor/Plotter before Python opens the port, and do not press RESET
while Python owns it. Find the current COM port and VID/PID without opening it:

```powershell
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\arduino.local.yaml --list-ports
```

For this UNO R4 Minima, the observed USB VID:PID is `2341:0069`; confirm the
port on each laptop. To query identity without motion, use
`python smoke_test\03_smoke_arduino.py --port COM3` after closing Serial
Monitor. It sends only `PING` and `IDENTITY`. A normal Test 1 prints
`arduino_ready` and `PASS: test_01_arduino_connection`; its result records
firmware version 1.2.1, LED off, motor disabled, and no motion attempt. If
READY is absent or the version differs, stop before motion tests. After
flashing 1.2.1, update both `arduino.expected_version` and `firmware.version`
in the ignored local YAML to `1.2.1`; do not overwrite reviewed calibration.

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

## Individually supervised manual jog

This is separate from the staged Test 2/3 workflow and from persistent logical
`up`/`down` control. It needs no prior test result, HOME, or `steps_per_unit`:

```powershell
python arduino\scripts\needle_control.py manual-up --steps 200 --config arduino\configs\arduino.local.yaml --live
python arduino\scripts\needle_control.py manual-down --steps 200 --config arduino\configs\arduino.local.yaml --live
```

Run **one line at a time**, observe the axis, then decide whether to run the
other. The script prints the exact signed JOG command and pulse count before
asking you to type `MANUAL UP 200` or `MANUAL DOWN 200`. Defaults are a
100-steps/s ceiling (the historical bridge rate) and a 500-steps/s² firmware
ramp. That ramp is the firmware's unloaded fallback, not a connected-axis
measurement; use `--speed` and `--acceleration` for other reviewed values.
Positive JOG drives D4 DIR LOW (historical UP); negative drives D4 DIR HIGH.
Each command sends exactly one requested movement, prints the Arduino's actual
ACK and DONE lines, and leaves firmware motion disarmed afterward. Manual jog
invalidates any saved logical position: it does **not** establish HOME or prove
physical travel from an encoder. Keep the 24 V driver-power disconnect within
reach; `STOP` over USB is not a physical emergency stop.

For offline checks, use `--mock` instead of `--live`, or run
`python -m pytest -q arduino/tests`. The staged Test 2, Test 3, and Test 4
scripts remain available; see `arduino/docs/TEST_02_GUIDE.md`,
`arduino/docs/TEST_03_GUIDE.md`, and `arduino/docs/TEST_04_GUIDE.md`.
Firmware upload commands and pin details are in `arduino/docs/FIRMWARE.md`.

This is an attended demonstration, not an unattended production safety system.
Software bounds cannot prevent a collision after missed steps, unpowered drift,
or manual movement. Inspect the physical axis after any uncertainty.
