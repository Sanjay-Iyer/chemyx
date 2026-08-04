# Test 3 Guide: Needle Axis

> **LIVE TEST BLOCKED UNTIL TEST 2 AND ALL AXIS HARDWARE PASS.**

## Required hardware and configuration

Require a matching successful live Test 2 record; verified signal interface;
upper/lower NC switches; hard stops; lead, steps/rev, microsteps, steps/mm;
home backoff; safe UP; conservative DOWN; maximum travel; speed/acceleration;
emergency disconnect; and proof the vertical axis cannot fall dangerously when
disabled. Explicitly confirm `motor.connected_to_axis_for_test_03: true`; the
temporary Test 2 disconnected flag is not reused as evidence of coupling.

## Connections and disconnected components

Connect only per the reviewed enclosed schematic, including both fail-safe NC
limit circuits. Keep Chemyx fluid movement and NMR acquisition disconnected or
inactive; they are not part of Test 3.

## Exact commands

Safe now:

```powershell
conda activate ai
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\arduino.local.yaml --preflight-only
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\arduino.example.yaml --mock
```

Future live command is the same script with `--live` and exact confirmation
`RUN ARDUINO TEST 3`.

Before that motion command, run the no-motion switch preflight with both flags:

```powershell
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\arduino.local.yaml --live --preflight-only
```

Type `RUN ARDUINO TEST 3 PREFLIGHT`, then activate/hold and release each switch
as prompted. Five stable samples are required for each state and a durable
passing record is required by the subsequent motion test. The YAML
`upper_state_change_tested` and `lower_state_change_tested` fields are notes,
not substitutes for this observed preflight record.

## Expected output

The operator activates/releases each switch, then the controller homes slowly
UP, stops at the upper limit, backs off, establishes command home, moves only
to conservative DOWN and safe UP twice, and finishes stopped at safe UP. Status
reports command-derived position and both limits. There is no full-travel test;
hard ceiling is 120 seconds.

## Stop conditions and common problems

Stop if both limits are active, a switch fails to change state, movement goes
toward an active limit, homing times out, direction is wrong, the axis can fall,
or any response/connection fails. Never command UP blindly after interrupted
motion. Inspect wiring, mechanics, and physical location first.

Final safe state: stopped at confirmed commanded safe UP with position known.
The driver remains enabled when disabling it could allow the vertical axis to
fall. Any interrupted motion records position uncertain.
