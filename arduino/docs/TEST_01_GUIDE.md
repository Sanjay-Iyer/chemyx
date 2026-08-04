# Test 1 Guide: Arduino Connection

## Required hardware and connections

Required: Dynabook laptop, USB-C data cable, and Arduino UNO R4 Minima.

```text
Laptop -> USB-C data cable -> Arduino UNO R4 Minima
```

Disconnect the DM542T, 24 V supply, NEMA 17, all signal-interface wiring,
limit switches, and needle mechanism. Test 1 contains no motor command.

## Configuration and firmware upload

Copy `arduino/configs/arduino.example.yaml` to
`arduino/configs/arduino.local.yaml`. Set only the verified Arduino COM port
and, optionally, its verified fingerprint. Leave motion placeholders unchanged.

In Arduino IDE, install Arduino UNO R4 Boards, open
`arduino/firmware/needle_controller/needle_controller.ino`, verify
`MOTION_COMMISSIONED = false`, `LIMITS_COMMISSIONED = false`, and the four
`COMMISSIONED_*` axis values remain zero; select **Arduino UNO R4 Minima** and
its port, upload, and close Serial Monitor.

## Exact command

```powershell
conda activate ai
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\arduino.local.yaml --live
```

Type `RUN ARDUINO TEST 1` exactly. Target runtime is under 20 seconds and the
configured hard ceiling is under 60 seconds.

## Expected output

- READY identifies `needle_controller`, `uno_r4_minima`, and firmware `0.1.0`.
- PING returns PONG.
- Initial STATUS reports motor disabled and LED off.
- LED on/off status transitions pass; BLINK completes three pulses and ends off.
- A passing live `result.json` is written and the COM port closes.

## Stop conditions and common problems

Stop on unexpected board identity, missing READY/ACK/DONE, sequence mismatch,
motor enabled at startup, or any connected motion hardware. Access denied means
another program probably owns the COM port; close Arduino Serial Monitor. A
missing port usually means wrong port selection, cable, or driver. The software
never tries another port automatically.

Final safe state: LED off, motor disabled, no movement command, USB connection
closed by Python. Disconnect USB if inspection shows anything unexpected.
