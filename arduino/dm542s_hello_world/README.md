# Arduino UNO R4 Minima + DM542S needle motion

> ### New here? Start with **[OPERATOR_GUIDE.md](OPERATOR_GUIDE.md)**
> Step-by-step instructions for Windows PowerShell: which commands to run, which
> file to upload, and exactly how to edit the configuration to change the port,
> units, distances, order, and pauses. No Python or YAML knowledge assumed.
>
> This README is the technical reference behind it.

Python does not touch GPIO. It sends text lines over USB serial to an Arduino
running one bridge sketch, and the Arduino generates STEP/DIR pulses:

```text
Python -> USB serial -> Arduino UNO R4 Minima -> STEP/DIR -> DM542S -> NEMA 17
```

This package contains two things:

1. **Hello-world diagnostics** (`01_serial_hello.py`, `01b_led_blink_test.py`,
   `02_slow_forward_test.py`, `03_forward_reverse_test.py`) — fixed, one-shot
   checks that the chain works at all. These are unchanged and remain the first
   thing to run when something breaks.
2. **YAML-driven needle motion** (`99_needle_calibration.py`,
   `01_needle_move.py`) — calibration and arbitrary movement sequences.
3. **One-way single moves** (`04_needle_up.py`, `05_needle_down.py`) —
   one direction, one distance, one number to edit. The direction is fixed by
   the script, so no configuration edit can reverse it.

> **This rig has no limit switches, no home switch, no encoder, and no
> emergency-stop input.** Position is tracked only by counting the steps the
> software commanded. Add limit switches and a homing routine before running
> automated needle operation anywhere near a physical travel limit.

The dated, hardware-specific configuration that completed the hello-world suite
is recorded in [`CONFIRMED_SETUP.md`](CONFIRMED_SETUP.md).

## Script naming convention

| Range | Meaning | Examples |
| ----- | ------- | -------- |
| `01`–`97` | Ordinary, sequential needle-motion and diagnostic workflows | `01_serial_hello.py`, `01_needle_move.py`, `04_needle_up.py`, `05_needle_down.py` |
| `98`, `99`, counting down | Calibration, maintenance, and special diagnostics | `99_needle_calibration.py` |

New ordinary scripts take the next free low number (`04_...`, `05_...`). New
special scripts take the next free high number (`98_...`, `97_...`). Each
workflow script has a matching config at `configs/<same name>.yaml`.

## File layout

```text
dm542s_hello_world/
├── 01_serial_hello.py            hello-world: serial + LED (24 V off)
├── 01b_led_blink_test.py         hello-world: ten LED blinks (24 V off)
├── 02_slow_forward_test.py       hello-world: one 100-pulse forward move
├── 03_forward_reverse_test.py    hello-world: one forward/reverse cycle
├── 01_needle_move.py             YAML-driven movement sequence
├── 04_needle_up.py          one-way UP move, distance from YAML  
├── 05_needle_down.py         one-way DOWN move, distance from YAML
├── 99_needle_calibration.py      YAML-driven degrees -> millimetres calibration
├── motion_utils.py               all step/degree/mm maths, timing, YAML validation
├── single_move_utils.py          shared loader/planner/runner for scripts 04 and 05
├── calibration_utils.py          calibration fitting, warnings, file I/O
├── serial_test_utils.py          shared serial helpers, STOP, safety prompts
├── requirements.txt
├── OPERATOR_GUIDE.md             START HERE: beginner PowerShell instructions
├── README.md
├── CONFIRMED_SETUP.md
├── configs/
│   ├── 99_needle_calibration.yaml   calibration run settings
│   ├── 01_needle_move.yaml          the movement sequence to execute
│   ├── 04_needle_up.yaml       up distance for script 04
│   ├── 05_needle_down.yaml      down distance for script 05
│   └── needle_calibration.yaml      AUTHORITATIVE calibration (ships uncalibrated)
├── calibration_results/          timestamped raw results and execution logs
│   ├── needle_calibration_<stamp>.yaml / .csv
│   ├── needle_move_<stamp>.yaml
│   └── needle_up_<stamp>.yaml / needle_down_<stamp>.yaml
├── tests/                        hardware-independent tests (no COM port used)
│   ├── conftest.py
│   ├── test_motion_conversion.py     unit conversion and rounding
│   ├── test_motion_config.py         YAML validation, calibration loading
│   ├── test_config_schema.py         modes, per-move pauses, typos, duplicates
│   ├── test_zero_net_motion.py       zero-net and rounding imbalance
│   ├── test_timing_and_limits.py     timeout scaling, relative plan bounds
│   ├── test_stop_behaviour.py        Ctrl+C -> STOP on the same connection
│   ├── test_calibration.py           fitting, residuals, warnings
│   ├── test_calibration_workflow.py  trials, return error, atomic writes
│   ├── test_single_move.py           scripts 04/05 schema, direction, bounds
│   └── test_firmware_protocol.py     mocked serial + .ino regression pins
└── arduino_dm542s_bridge/
    └── arduino_dm542s_bridge.ino
```

## Wiring (common cathode)

Control side, matching the sketch:

```text
Arduino D3  -> DM542S PUL+
Arduino GND -> DM542S PUL-
Arduino D4  -> DM542S DIR+
Arduino GND -> DM542S DIR-
DM542S ENA+ and ENA- disconnected
```

STEP pulses are **active HIGH**: D3 goes high to source current through the
PUL input, and low when idle. The two signal-negative wires return only to
Arduino GND — do **not** connect them to the DM542S `V-` power terminal.

Power/motor side, kept entirely separate:

```text
24 V positive -> DM542S V+
24 V negative -> DM542S V-
Motor coil 1  -> A+ and A-
Motor coil 2  -> B+ and B-
```

Connect the motor while power is off. Never connect 24 V to the Arduino, and
never connect or disconnect motor wires while the DM542S is powered.

### Electrical limit

This assumes the specific DM542S accepts direct 5 V controller signals in the
arrangement above. A successful move only shows the signals were recognised. It
does **not** prove UNO R4 GPIO current is within its limit (the datasheet
specifies 8 mA maximum DC per I/O pin) or that direct wiring is suitable for
permanent operation.

### DIP switch assumption: 800 pulses per revolution

Every conversion in this package assumes the DM542S microstep switches are set
to **800 pulses/revolution**, and current to **1.4 A RMS / 2.0 A peak** for a
NEMA 17 rated around 1.5 A per phase.

`steps_per_revolution: 800` appears in both config files. **If you change the
DIP switches, change both configs and re-run the calibration.** The software
cannot detect a DIP switch change; it will silently command the wrong distance.

## Steps, degrees, and millimetres

Three units, converted in exactly one place (`motion_utils.py`):

| Unit | Meaning | Needs calibration? |
| ---- | ------- | ------------------ |
| **steps** | Pulses sent to the DM542S. The only thing the firmware understands. | No |
| **degrees** | Motor shaft rotation. `steps = round(degrees / 360 × steps_per_revolution)` | No — fixed by the DIP switches |
| **millimetres** | Linear needle travel. `steps = round(mm / mm_per_step)` | **Yes** — depends on the mechanism |

At 800 steps/revolution: 90° = 200 steps, 180° = 400 steps, 360° = 800 steps.

Calibration also reports `mm_per_degree` and `steps_per_mm`, all derived from
one fitted slope so they cannot disagree.

### Rounding is explicit, never silent

Steps are whole numbers, so fractional requests are rounded **half away from
zero** (2.5 → 3, −2.5 → −3). This is deliberately *not* Python's built-in
banker's rounding, which would turn 2.5 into 2. Nothing is ever truncated, and
every script reports what it actually commanded:

```text
Requested: 2.50 mm
Calculated: 137.4 steps
Commanded: 137 steps
Predicted actual movement: 2.493 mm
```

### Three different notions of "position"

Keep these distinct — the scripts do:

- **Commanded position** — the signed step total the software asked for. This is
  bookkeeping, not a measurement.
- **Estimated position** — commanded position converted to degrees or
  millimetres. Only as good as the calibration.
- **Measured physical position** — a number a human read off a ruler or dial
  indicator. The only real position information in this system.

A stalled motor, a missed pulse, a slipping coupler, or a loose grub screw makes
commanded position silently wrong, and nothing in software will notice.

## Install and upload

Python 3.10 or newer. From this directory:

```bash
py -m pip install -r requirements.txt
```

Open `arduino_dm542s_bridge\arduino_dm542s_bridge.ino` in Arduino IDE, select
**Arduino UNO R4 Minima** and its COM port, and upload. Close the Arduino Serial
Monitor afterwards — only one program can own a COM port at a time.

> **Re-upload the `.ino` whenever the serial protocol changes.** Python and the
> firmware agree on exact command and reply strings (`MOVE 200` → `DONE MOVE
> 200`). An out-of-date sketch answers `ERROR unknown command: MOVE ...` and the
> Python side aborts. `tests/test_firmware_protocol.py` checks that the Python
> constants still match the sketch source, but it cannot know what is actually
> flashed on the board.

## Firmware serial protocol

| Command | Reply | Notes |
| ------- | ----- | ----- |
| `PING` | `PONG Arduino serial and LED test passed` | Also blinks the LED 3× |
| `BLINK10` | `START LED BLINK 10` … `DONE LED BLINK 10` | No motion |
| `FWD` | `START FWD 100` … `DONE FWD 100` | Fixed 100 pulses forward |
| `CYCLE` | `START CYCLE` … `DONE CYCLE` | 200 forward, pause, 200 back |
| `MOVE <n>` | `START MOVE <n>` … `DONE MOVE <n>` | Signed. `+` = forward, `−` = backward |
| `STATUS` | `STATUS IDLE` or `STATUS MOVING <done> OF <requested>` | Safe during a move |
| `STOP` | `STOPPED MOVE <done> OF <requested>` or `STOPPED IDLE` | Aborts an active `MOVE` |

`MOVE` is rejected with an `ERROR` line when the argument is missing,
non-integer, zero, or larger than the firmware maximum of 5000 steps, and when
another move is already running. `STATUS` and `STOP` are the only commands
answered while the motor is turning.

`MOVE` runs as a non-blocking state machine, so the sketch keeps reading serial
between pulse edges — that is what makes `STOP` able to interrupt a long move.
`PING`, `BLINK10`, `FWD`, and `CYCLE` keep their original blocking
implementations and their exact original reply strings, so the hello-world
scripts are unaffected. Those four block `loop()` while they run, so `STOP` is
not serviced during them and would answer the misleading `STOPPED IDLE`; they
are all short and fixed-size.

### How `STOP` actually gets sent

`STOP` is sent **only** by the Ctrl+C handler inside the script that already owns
the port — `01_needle_move.py` or `99_needle_calibration.py`, from
`request_software_stop()` in [`serial_test_utils.py`](serial_test_utils.py).

Windows allows one owner per COM port. While a script is running you **cannot**
open a second terminal or the Arduino Serial Monitor to send `STOP` yourself, so
the owning process has to do it. It writes `STOP\n` on its existing connection,
waits `timing.stop_timeout_seconds` (default 3 s) for a `STOPPED ...` line, and
reports whether the abort was confirmed. If it was not confirmed, the script
tells you to switch off the 24 V supply.

> **`STOP` is a software abort, not an emergency stop.** It stops the pulse
> train. It does **not** de-energise the DM542S, which stays powered and holding
> torque. It cannot help if the sketch hangs, the USB cable falls out, the
> Arduino resets, or the Python process is killed outright.
>
> **The emergency stop on this rig is the 24 V supply switch.** Keep it within
> reach and use it first if anything looks wrong.

## Run the hello-world diagnostics first

1. DM542S 24 V **off** — verify serial and the onboard LED:

   ```bash
   py 01_serial_hello.py --port COM3
   ```

   Optional ten-blink check, still with 24 V off:

   ```bash
   py 01b_led_blink_test.py --port COM3
   ```

2. After every displayed safety check, turn on DM542S power and run one
   100-pulse move (type exactly `RUN` to confirm):

   ```bash
   py 02_slow_forward_test.py --port COM3
   ```

3. One forward/reverse cycle, ending approximately at the starting angle:

   ```bash
   py 03_forward_reverse_test.py --port COM3
   ```

Physical clockwise/counterclockwise labels do not matter; direction naming
depends on motor wiring.

## Calibration procedure (script 99)

Determines how far the needle actually travels for a known motor rotation.

```bash
python .\99_needle_calibration.py --config .\configs\99_needle_calibration.yaml
```

Each trial is an **independent round trip** starting and ending at commanded
zero — never a chain of ever-larger forward moves:

```text
zero -> +90°  -> measure -> back to zero
zero -> +180° -> measure -> back to zero
zero -> +360° -> measure -> back to zero
```

The script:

1. Loads and validates `configs/99_needle_calibration.yaml`.
2. Prints the complete planned sequence with every angle converted to steps.
3. Proves on paper that every trial returns to commanded zero.
4. **Refuses to open the serial port if any of that fails.**
5. Prints the safety checklist and requires you to type exactly `RUN`.
6. For each trial: moves forward, waits for `DONE MOVE`, pauses, prompts for the
   measured displacement in millimetres, validates it is positive and plausible,
   moves back by the identical step count, and confirms commanded zero.
7. Saves raw observations to `calibration_results/needle_calibration_<stamp>.yaml`
   and `.csv`.
8. Fits a straight line and reports `mm_per_degree`, `mm_per_step`,
   `steps_per_mm`, and a predicted value and residual for every point.
9. Warns about inconsistent ratios, large residuals, low R², a hidden intercept
   (the backlash signature), or too few distinct angles.
10. Asks you to type exactly `UPDATE` before overwriting
    `configs/needle_calibration.yaml` (written atomically, keeping a `.bak`).

After each return move it also asks for the **measured** offset from the starting
mark, signed, accepting `0` for a perfect return and `skip` if you cannot measure
it. This is the only direct evidence in the whole package that commanded zero and
physical zero are the same place — everything else is inference from the fit.
A consistent one-directional offset across trials is reported as measured lost
motion, distinct from the inferred intercept. Set
`calibration.measure_return_error: false` to skip the prompt.

If you cancel at that last prompt, the timestamped raw results are kept and the
authoritative calibration is left untouched.

Measure the same physical feature the same way every trial, and enter positive
magnitudes only — direction is already known. Set `repetitions: 2` or more to
see repeatability and backlash in the residuals.

`configs/needle_calibration.yaml` **ships with `calibrated: false` and no
values.** No calibration has been performed on this rig and none has been
invented. Do not hand-write the numbers; a guessed calibration produces
confident, wrong millimetre distances.

## Movement procedure (script 01)

Executes an ordered sequence of individually sized forward and backward moves.

```bash
python .\01_needle_move.py --config .\configs\01_needle_move.yaml
```

Pick a unit with `execution.movement_mode`:

| Mode | Field used | Conversion | Needs calibration? |
| ---- | ---------- | ---------- | ------------------ |
| `mm` | `mm:` | `round(mm / mm_per_step)` from `configs/needle_calibration.yaml`, which must have `calibrated: true` | **Yes** |
| `degrees` | `degrees:` | `round(degrees / 360 x steps_per_revolution)` | No |
| `steps` | `steps:` | none — the value *is* the step count | No |

The older boolean `use_mm_calibration: true|false` is still accepted and maps to
`mm`/`degrees` with a deprecation notice. Setting both to contradictory values is
a hard error, never a silent winner.

Only the active field has to be meaningful; the unused ones may be absent. A
mode-agnostic `value:` key works too, but supplying both `value:` and the
mode-specific key for the active mode is refused as ambiguous. The shipped
config uses `degrees`, because the rig is not calibrated yet.

```yaml
moves:
  - name: lower_5mm            # names must be unique
    direction: forward         # forward | backward, lowercase only
    degrees: 90.0
    mm: 5.0
    pause_after_seconds: 2.0   # optional; overrides the global default
```

The `moves:` list length **is** the number of moves. There are no separate
forward/backward counters that could disagree with it. Add, remove, or reorder
entries freely; any number of moves, in any order, with a different magnitude
each.

`direction` accepts only `forward` and `backward` (lowercase). Each move is
capped by `execution.maximum_absolute_steps_per_move`, which itself may not
exceed the firmware's 5000-step limit.

**Unknown or misspelled keys are rejected** at every level — top-level sections,
`execution`, `serial`, `driver`, `timing`, `software_limits`, and each move —
with a "did you mean" suggestion. A typo can never be silently ignored.

### Scaled timeouts

Timeouts are computed from the step count, never fixed:

```text
pulse_period_seconds   = 2 x pulse_half_period_us / 1_000_000
expected_motion        = abs(steps) x pulse_period_seconds
timeout                = startup_allowance
                       + expected_motion
                       + expected_motion x safety_margin_fraction
                       + completion_allowance
```

clamped into `[minimum_timeout_seconds, maximum_timeout_seconds]` — except that
the clamp is never allowed to fall below what a healthy move genuinely needs, so
a badly configured ceiling cannot abort a valid long move. A ceiling too low for
any planned move is rejected before the port opens.

At the shipped defaults: 100 steps -> 1 s motion, 5.0 s timeout (the floor);
800 steps -> 8 s motion, 12.0 s timeout; 5000 steps -> 50 s motion, 64.5 s
timeout. The expected duration and timeout for every move are printed in the
preflight table and again as each move starts.

`timing.pulse_half_period_us` must match `HALF_PULSE_US` in the sketch. A test
pins the Python constant to the sketch source so the two cannot silently drift.

### Relative software plan bounds

```yaml
software_limits:
  enabled: true
  minimum_steps: -1000
  maximum_steps: 1000
```

Every **intermediate** cumulative position is checked, not just the final total.
Without this, a sequence like five `+2160°` moves followed by five `-2160°` moves
sums to exactly zero and passes zero-net validation while travelling 30
revolutions away from the start — on a rig with no limit switches.

These bounds are **relative to wherever the needle is when the script starts**.
There is no homing, so they cannot detect a wrong starting position and are not
machine limits. They bound the plan, not the machine.

### Zero-net validation

When `require_zero_net_steps: true`, the sequence must return to exactly zero
commanded steps. This is checked **before the COM port is opened**, so a bad
sequence never reaches the motor:

1. Every move is converted to a signed whole step count.
2. A preflight table prints move number, name, direction, requested value and
   unit, exact fractional steps, commanded steps, and cumulative position.
3. The signed step counts are summed.
4. A non-zero total refuses the run and states the exact mismatch.

**Validation happens after rounding.** A sequence that is nominally balanced can
still fail by one step:

```text
+1.0 mm -> 3.33 -> +3 steps
+1.0 mm -> 3.33 -> +3 steps
-2.0 mm -> 6.67 -> -7 steps
                   -------
                   -1 step, even though +1.0 +1.0 -2.0 = 0.0 mm exactly
```

The script reports this as a rounding imbalance and stops. **No hidden
correction move is ever appended** — edit the YAML yourself so the intent is
explicit (splitting the 2.0 mm move into two 1.0 mm moves fixes the example
above).

Set `require_zero_net_steps: false` to allow a deliberately open-ended sequence.
The imbalance is then reported but not enforced.

### During and after the run

Moves execute one at a time, each waiting for its `DONE MOVE ...` line, pausing
that move's `pause_after_seconds` (or the global default), and printing the
cumulative commanded position. Any serial error, firmware `ERROR` line, timeout,
or unexpected response aborts the sequence immediately — there is no
"continue anyway" mode, because continuing past a serial error on a rig with no
homing loses track of the needle.

At the end the script prints total forward steps, total backward steps, final
commanded position, and whether zero-net motion was achieved, then writes
`calibration_results/needle_move_<stamp>.yaml`. That log contains the fully
resolved configuration (serial, driver, execution, timing, software limits), the
complete plan, every executed move, and the outcome of any software STOP — enough
to reproduce or audit the run without the original config file. A log is written
for completed, interrupted, aborted, and failed runs alike. **The sequence is
never repeated automatically.**

A zero final commanded position means the step counts cancelled. It does **not**
prove the needle physically returned to its starting point. Measure it if that
matters.

## One-way single moves (scripts 04 and 05)

For the common case of "move the needle in, do something, move it back out",
where a full sequence is more machinery than the job needs.

```bash
python .\04_needle_up.py --config .\configs\04_needle_up.yaml
```

```bash
python .\05_needle_down.py --config .\configs\05_needle_down.yaml
```

`--config` is optional; each script defaults to its matching file. To change how
far either one moves, edit **one number**:

```yaml
movement:
  movement_mode: degrees   # mm | degrees | steps
  distance: 90.0           # positive magnitude, in the unit above
```

**Direction is fixed in the script, not the configuration.** Script 04 always
moves the needle up and script 05 always moves it down; `direction:` is rejected
as a configuration key, and a negative `distance` is refused rather than
silently reversing the move the script name promises.

`up` and `down` are this rig's operator-facing names for the motion core's
`forward` and `backward`, which are simply the sign of the step count on the
wire (`MOVE +200` / `MOVE -200`). Both names are printed in every preflight so
the label and the sign cannot drift apart. Which physical direction each one
produces depends on motor wiring — swapping two wires of one coil inverts it —
so confirm it against the actual needle before trusting the labels.

The unit modes, conversion,
rounding, per-move ceiling, scaled timeouts, relative software bounds, typed
`RUN` confirmation, Ctrl+C `STOP` path, and execution logs are all the same
machinery as script 01, imported from `motion_utils.py` — there is no second
implementation of the motion mathematics.

For a single move the relative software bounds still apply, and only one side of
the window is doing work: `maximum_steps` limits script 04, `minimum_steps`
limits script 05.

### What these scripts deliberately cannot do

**They do not track position between runs**, and they cannot: a one-way move
never returns to zero, so `require_zero_net_steps` does not exist in their
configuration and is rejected if you copy it across. Nothing in software knows
whether a matching return move was ever run, or whether it used the same
distance. Running 04 at 90° and then 05 at 45° leaves the needle 45° up from
where it started, with no warning at any point.

If a sequence must provably return to its starting point, use
`01_needle_move.py` — its zero-net validation is the whole reason that script
exists, and it checks the plan before the port is opened.

## Tests

Hardware-independent; no COM port is ever opened. From the repository root:

```bash
python -m pytest arduino/dm542s_hello_world/tests -q
```

They cover unit conversion at 800 steps/revolution, signed directions,
millimetre conversion, rounding, zero-net validation, one-step rounding
imbalance, invalid YAML, missing/incomplete calibration, bad magnitudes and
direction names, excessive motion, serial parsing against a mocked device,
calibration fitting and warnings, and preservation of the original `PING`,
`BLINK10`, `FWD`, and `CYCLE` behaviour.

## Troubleshooting

- **COM port unavailable / access denied** — close the Arduino Serial Monitor
  and any other terminal, IDE, or script holding the port. Check the `port:`
  value in the config (or `--port` for the hello-world scripts) against the port
  shown in Arduino IDE. Unplugging and replugging can change the number.
- **Arduino does not respond** — confirm the bridge sketch is uploaded, `baud:`
  is 115200, the USB cable carries data (not charge-only), and the correct board
  and port are selected. Try `01_serial_hello.py` first; it needs no 24 V power.
  If it answers `ERROR unknown command: MOVE ...`, an old sketch is flashed:
  re-upload.
- **DM542S powered but the motor does not move** — turn off 24 V, then check
  coil pairing on A+/A−/B+/B−, the STEP wire on D3 and DIR on D4, both signal
  grounds on Arduino GND, the current DIP switches against the motor rating, the
  pulses/revolution switches, and that the shaft is unloaded. A motor that locks
  and buzzes but does not turn is usually a mis-paired coil.
- **The motor moves in the opposite physical direction** — this is a naming
  question, not a fault; direction depends on motor wiring. Swap the two wires of
  **one** coil with 24 V off, or swap the `forward`/`backward` values in the
  config. Re-run the calibration afterwards and note the change in
  `CONFIRMED_SETUP.md`. Do not "fix" it by editing `setDirection()` alone —
  that would silently invert every saved plan and log.
- **A sequence fails zero-net validation** — read the printed totals. If it says
  *rounding imbalance*, the millimetre or degree values cancel but the whole-step
  counts do not; adjust the move sizes so the steps cancel. Otherwise the values
  themselves do not sum to zero. Nothing was opened and nothing moved.
- **Millimetre calibration missing** — `configs/needle_calibration.yaml` is
  absent or has `calibrated: false`. Either run script 99, or set
  `use_mm_calibration: false` and work in degrees.
- **Commanded zero is not physically zero** — the software counted steps the
  driver may not have executed. Suspect a missed step from too-fast pulses or too
  little current, a stalled or obstructed needle, a slipping coupler or grub
  screw, backlash (check for a non-zero intercept in the calibration warnings),
  or a driver fault mid-sequence. Software cannot detect any of these. Return the
  needle to a known physical reference by hand and recalibrate.
- **Driver fault indication** — turn off 24 V and investigate supply, wiring,
  motor, and current setting against the driver manual before retrying.
- **The Arduino disconnects during movement** — turn off 24 V. Do not retry until
  wiring, power separation, and control-input current have been investigated.

Turn off the DM542S 24 V supply immediately for a driver fault, violent
vibration, an Arduino reset or disconnection, rapidly heating parts, smoke,
sparking, or an overheating smell.

## Before automated needle operation

**Add physical limit switches and a homing routine before running unattended or
automated needle motion near travel limits.** Everything in this package is
software-only position tracking. There is no way for it to know the needle has
hit a hard stop, and a stalled motor produces no error — only a commanded
position that quietly diverges from reality. An encoder would additionally let
the software detect missed steps rather than assume they never happen.
