# Arduino Needle-Axis Bring-Up

> **CURRENT LIVE-TEST STATUS**
>
> **Test 1:** Approved for Arduino-only live testing.
>
> Required connection: `Laptop -> USB-C data cable -> Arduino UNO R4 Minima`
>
> Keep the DM542T, 24 V supply, NEMA 17 motor, signal interface, and needle
> mechanism disconnected.
>
> **Tests 2, 3, and full Test 4: DO NOT RUN IN LIVE MODE YET.**
>
> These tests require a verified open-collector/open-drain signal interface
> between the Arduino and DM542T, correct motor and driver configuration, and
> the additional hardware listed in each test guide. Never connect UNO R4 GPIO
> directly to DM542T PUL, DIR, or ENA. The software is included now for review
> and mock testing before the remaining hardware is available.

| Test | Purpose | Can run now? |
| --- | --- | --- |
| Test 1 | Arduino USB, serial, LED, PING/PONG | Yes |
| Test 2 | Unloaded motor movement | No |
| Test 3 | Homed needle-axis movement | No |
| Test 4A | Connection-only preflight | Yes, only if it causes no movement |
| Test 4B | Full Arduino, pump, and NMR sequence | No |

## Safe first use

Use the Conda `ai` environment on this simulation/development laptop. No live
OT-2 or other robot command belongs in this subsystem.

1. Copy `arduino/configs/arduino.example.yaml` to an ignored local file such
   as `arduino/configs/arduino.local.yaml`.
2. Leave every motor, driver, interface, limit, and motion placeholder false
   or null for Test 1.
3. Install the Arduino IDE and its Arduino UNO R4 Boards package.
4. Open `arduino/firmware/needle_controller/needle_controller.ino`.
5. Confirm `MOTION_COMMISSIONED` is `false`.
6. With only USB-C connected, select **Arduino UNO R4 Minima** and the verified
   Arduino COM port, then upload.
7. Close Arduino Serial Monitor so Python can own the COM port.
8. Record the explicit COM port in the local YAML. A verified VID/PID/serial
   fingerprint may be added; the software never selects the first port.
9. Run:

```powershell
conda activate ai
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\arduino.local.yaml --live
```

Type the exact confirmation `RUN ARDUINO TEST 1` when prompted.

Expected final state: port closed, built-in LED off, motor output disabled,
and no motor command sent. Full details are in
[`docs/TEST_01_GUIDE.md`](docs/TEST_01_GUIDE.md).

## Modes

Every script supports `--validate-only`, `--mock`, `--dry-run`, `--live`,
`--config PATH`, and `--list-ports`. Motion-capable scripts also support
`--preflight-only`. With no mode flag, behavior is validation-only and opens
no hardware endpoint. `--live` and `--mock` are mutually exclusive.

Examples:

```powershell
conda activate ai
python arduino\scripts\test_01_arduino_connection.py --mock --config arduino\configs\arduino.example.yaml
python arduino\scripts\test_02_unloaded_motor.py --preflight-only --config arduino\configs\arduino.example.yaml
python arduino\scripts\test_03_needle_axis.py --mock --config arduino\configs\arduino.example.yaml
python arduino\scripts\test_04_integrated_system.py --mock --preflight-only --config arduino\configs\integrated_hello_world.example.yaml
```

Test 4A connection-only live command, after configuring the three endpoints:

```powershell
conda activate ai
python arduino\scripts\test_04_integrated_system.py --config arduino\configs\integrated_hello_world.local.yaml --live --preflight-only
```

Test 4A sends only Arduino `PING`/`STATUS`, Chemyx `help`, and NMR
`PingSpectrometer`. It performs no axis move, pump start, or NMR acquisition.

## Durable evidence

Mock and live executions write `runs/arduino/<run_id>/result.json` plus an
event log. Results include execution mode, Git commit, firmware version,
hardware configuration fingerprint, operator confirmations, and final known
state. Only matching successful **live** records can satisfy later live-test
prerequisites. A mock claim or an edited filename cannot unlock a live test.

## Validation

```powershell
conda activate ai
python -m pytest arduino\tests -q
```

See [system overview](docs/SYSTEM_OVERVIEW.md),
[required hardware](docs/REQUIRED_HARDWARE_BEFORE_LIVE_MOTION.md), and
[safety/failure modes](docs/SAFETY_AND_FAILURE_MODES.md).

