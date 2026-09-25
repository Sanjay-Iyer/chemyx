# Test 1 Guide: Arduino Connection

## Required hardware and connections

Required: Dynabook laptop, USB-C data cable, and Arduino UNO R4 Minima.

```text
Laptop -> USB-C data cable -> Arduino UNO R4 Minima
```

Keep 24 V motor power off. Test 1 contains no motor command and does not
require changing the existing D3/D4 signal wiring.

## Configuration and firmware upload

Copy `arduino/configs/arduino.example.yaml` to
`arduino/configs/arduino.local.yaml`. Set only the verified Arduino COM port
and, optionally, its verified fingerprint. Leave motion placeholders unchanged.
If an older local YAML already exists, change only `arduino.expected_version`
and `firmware.version` to `1.2.1` after uploading this firmware; preserve any
reviewed machine settings.

In Arduino IDE, install Arduino UNO R4 Boards, open
`arduino/firmware/needle_controller/needle_controller.ino`; select **Arduino
UNO R4 Minima** and its port, upload, and close Serial Monitor. The firmware
boots with motion disarmed and all runtime motion ceilings at
zero. Reviewed YAML must be applied before any motion command can succeed.

## Exact command

```powershell
conda activate air
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\arduino.local.yaml --live
```

Type `RUN` exactly. Target runtime is under 20 seconds and the
configured hard ceiling is under 60 seconds.

## Expected output

- READY identifies `needle_controller`, `uno_r4_minima`, and firmware `1.2.1`.
- PING returns PONG.
- Initial STATUS reports the software motion arm disabled and LED off.
- LED on/off status transitions pass; BLINK completes three pulses and ends off.
- A passing live `result.json` is written and the COM port closes.

The smoke diagnostic `python smoke_test\03_smoke_arduino.py --port COM3` may
also query `IDENTITY` after PING. It never issues a motor command. The main
Test 1 still requires and validates READY; IDENTITY does not bypass it.

## Stop conditions and common problems

Stop on unexpected board identity, missing READY/ACK/DONE, sequence mismatch,
motor enabled at startup, or any connected motion hardware. Access denied means
another program probably owns the COM port; close Arduino Serial Monitor. A
missing port usually means wrong port selection, cable, or driver. The software
never tries another port automatically.
Do not press RESET while Python owns the port. Firmware 1.2.1 waits briefly
after each USB host connection, then announces READY once without a reset.

Final state: LED off, software motion arm disabled, no movement command, USB connection
closed by Python. Disconnect USB if inspection shows anything unexpected.
