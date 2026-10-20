> Numbered operator guides: [01 — Arduino](../docs/01_arduino/00_START_HERE.md). For the full experiment: [03 — Fixed-time run](../docs/03_fixed_time_experiment/00_START_HERE.md).

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
in the local YAML to `1.2.1`; do not overwrite reviewed calibration.

Python's `TrackedNeedle` in `arduino/python/needle_state.py` owns automated logical
position: HOME=0 (inside the flask), UP=1 (raised), DOWN=0 (HOME). The standalone
manual script's UP/DOWN are relative +1/−1 increments in this same reference.
It stores an atomic JSON estimate
at `runs/arduino/needle_state.json` by default. The position is not measured:
there is no encoder or physical HOME. Missing/corrupt state and failed or
interrupted motion block automatic movement until inspection and supervised
`home`. A valid saved estimate survives Python/computer restarts if
the physical axis has not moved independently.
The same `home` command handles both situations with one `y`: a valid reference
permits an absolute move to zero; UNKNOWN tracking requires the operator to
physically place/verify the stationary needle at zero, then records that
reference **without movement**. `set-home`/`confirm-home` remain explicit
reference-setting aliases, including when a valid reference already exists.
Corrupt JSON is preserved as `.corrupt-*.bak` after confirmation. See the
[HOME recovery operating guide](../docs/01_arduino/02_home_recovery.md) for fault handling and
the durable recovery journal. No acknowledgment flags are required.

Configure the Arduino COM port, physical UP direction, steps per logical unit,
speed, named positions, and software limits in the tracked starting point
`arduino/configs/arduino.local.yaml` (derived from `arduino.example.yaml`). Its default `steps_per_unit` and
`up_step_sign` are deliberately null: inspect/calibrate on the rig before any
live move. The example -3/+5 bounds are **not** verified physical limits.

From the repository root on the instrument laptop, using its verified Python
environment and the existing calibrated COM3 configuration:

```powershell
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino_real_COM3.yaml
```

The physical confirmation step must follow an actual needle inspection. Live
HOME prompts for one `y`; manual `up`/`down` move at once. `status` never moves the
needle. `stop` is also available, but the existing 24 V driver-power
disconnect is the physical stop. `ENABLE`/`DISABLE` in this firmware only arm
software; they do not control the driver's unwired ENA input.

## Individually supervised manual jog

The standalone script uses relative `up`/`down` (the `manual-up`/`manual-down`
names are aliases). It needs no prior HOME, but retains the existing verified
increment and physical direction calibration:

```powershell
python arduino\scripts\needle_control.py up --config arduino\configs\arduino_real_COM3.yaml --live
```

```powershell
python arduino\scripts\needle_control.py down --config arduino\configs\arduino_real_COM3.yaml --live
```

Run **one line at a time** and inspect clear physical travel first: there is no
confirmation prompt. The current COM3 increment is 200 STEP pulses at 100 steps/s
with a 500 steps/s² ramp. Add `--steps N` to move N pulses for one command; a size
that is not a multiple of 200 resets the count and clears a saved HOME (run
`set-home` again). Configure reviewed motion values in the Arduino YAML. Relative counts
persist across invocations and enforce the configured −3/+5 travel bounds.
Before calibration, count zero is only a relative origin: it cannot authorize
automatic movement. `set-home` records the current physically verified stopped
sampling HOME as zero without movement; after that, manual moves and automated
absolute 0/1 positioning share the same ledger. Interrupted motion stays unknown.
Keep the 24 V driver-power disconnect within reach; `STOP` over USB is not a
physical emergency stop. See [the fixed-time operating guide](../docs/03_fixed_time_experiment/01_run_guide.md).

For offline checks, use `--mock` instead of `--live`, or run
`python -m pytest -q arduino/tests`. The staged Test 2, Test 3, and Test 4
scripts remain available; see `docs/01_arduino/09_unloaded_motor_test.md`,
`docs/01_arduino/10_needle_axis_test.md`, and `docs/01_arduino/11_integrated_system_test.md`.
Firmware upload commands and pin details are in `docs/01_arduino/05_firmware.md`.

This is an attended demonstration, not an unattended production safety system.
Software bounds cannot prevent a collision after missed steps, unpowered drift,
or manual movement. Inspect the physical axis after any uncertainty.
