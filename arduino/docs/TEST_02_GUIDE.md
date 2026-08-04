# Test 2 Guide: Unloaded Motor

> **LIVE TEST BLOCKED WITH CURRENT HARDWARE STATUS. Mock/preflight only.**

## Required hardware and configuration

All items in `REQUIRED_HARDWARE_BEFORE_LIVE_MOTION.md` under Test 2 are
mandatory. Record exact interface type, wiring review, inversion, DM542T 5 V
selector, motor model/current/coil pairs, supply current, current switches,
microsteps, enable polarity, fuse, step count, speed, and shaft-safe operator
confirmation. `firmware.motion_enabled` remains false until the independently
reviewed firmware guard is deliberately commissioned.

## Connections and disconnected components

Use only the reviewed schematic:

`Arduino -> verified signal driver -> DM542T -> unloaded NEMA 17`, with the
fused 24 V supply powering only the driver. Never connect GPIO directly to
PUL/DIR/ENA. The motor must be mechanically disconnected from the needle axis;
the needle mechanism and both limit functions are outside this test.

## Exact commands

Safe now:

```powershell
conda activate ai
python arduino\scripts\test_02_unloaded_motor.py --config arduino\configs\arduino.local.yaml --preflight-only
python arduino\scripts\test_02_unloaded_motor.py --config arduino\configs\arduino.example.yaml --mock
```

Future reviewed live command:

```powershell
python arduino\scripts\test_02_unloaded_motor.py --config arduino\configs\arduino.local.yaml --live
```

## Expected output

After all future checks pass: status, driver enable, a small configured forward
jog, equal reverse jog, STOP, status, and driver disabled. Record direction,
noise, vibration, temperature, and approximate return. Commanded steps do not
prove angle, distance, or physical accuracy. Hard ceiling: 120 seconds.

## Stop conditions and common problems

Any missing item prints a specific `LIVE MOTOR TEST BLOCKED` reason before
motion. Stop on wrong direction, harsh noise, vibration, heating, limit/fault
event, timeout, lost ACK/DONE, or uncertain position. Common causes are wrong
coil pairs, current/microstep settings, inversion, enable polarity, or interface
wiring; de-energize driver power and inspect—do not guess or retry.

Final safe state: pulses stopped and unloaded driver disabled. A failed or
interrupted move is recorded as position uncertain and requires inspection.
The live script requires nonempty records for direction, noise, vibration,
temperature, and approximate return position, followed by the exact acceptance
`ACCEPT TEST 2 OBSERVATIONS`. This evidence must be entered before the same
120-second deadline and is stored in the passing result.

