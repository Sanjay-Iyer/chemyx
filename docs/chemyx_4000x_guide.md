# Chemyx Fusion 4000X Guide

This guide explains why `deploy/infuse_withdraw.py` worked on the work laptop
and how that success was folded into the reusable code.

## Why The Work-Laptop Script Worked

The proven script succeeded because all of these conditions were true:

- The laptop had `pyserial` installed and could import `serial`.
- The pump appeared as `COM4` on that laptop.
- The script opened `COM4` at `115200` baud.
- The serial framing was 8 data bits, no parity, 1 stop bit.
- Each command was plain ASCII.
- Each command ended with carriage return `\r`.
- The script reset serial buffers before each command.
- It waited about 0.2 seconds after writing before reading the reply.
- It addressed channel 1 by prefixing commands with `1`.
- It configured units, diameter, and rate before movement.
- It used positive volume for infusion.
- It used negative volume for withdrawal.
- It sent `1 start 0` to start motion with zero delay.
- It sent `stop` before switching direction and before closing the port.
- It closed the serial connection in `finally`, even after errors.

The new `chemyx_lab.pump.Pump` wrapper keeps those behaviors available:

- `COMMAND_TERMINATOR = "\r"`
- default response delay is `0.2` seconds
- channel prefixes are handled by `channel=1`
- `pump.start(delay=0)` sends `start 0`
- `pump.infuse()` sets a positive volume
- `pump.withdraw()` sets a negative volume

## Physical Connection

Use either:

- USB from the pump to the laptop, if the pump exposes a virtual COM port.
- RS232 DB9 straight-through cable, often through a USB-to-serial adapter.

Do not use a null-modem/crossover cable. A wrong cable can allow the port to
open while the pump never replies.

Only one program can own the COM port at a time. Close the Chemyx GUI, PuTTY,
TeraTerm, Arduino Serial Monitor, or any other serial monitor before running
Python.

## Serial Settings

Known-good work-laptop test values:

```text
PORT      COM4
BAUD      115200
CHANNEL   1
FRAMING   8-N-1
ENDING    carriage return, \r
TIMEOUT   2 seconds
READ WAIT 0.2 seconds after write
```

The baud rate must match the value shown on the pump. If the pump screen says a
different baud, use that value instead.

## Core Commands

Channel 1 examples:

```text
1 set units 0
1 set diameter 28.6
1 set rate 2.0
1 set volume 1.5
1 start 0
stop
1 set volume -1.5
1 start 0
stop
```

Meaning:

```text
set units 0       mL/min
set diameter      syringe inner diameter in mm
set rate          flow rate in active units
set volume +x     infuse
set volume -x     withdraw
start 0           start with zero delay
stop              stop motion
```

## Main Scripts

List ports:

```powershell
python scripts\list_ports.py
```

Dry run:

```powershell
python scripts\pump_infuse_withdraw.py --mock
```

Real hardware with local config:

```powershell
python scripts\pump_infuse_withdraw.py
```

Real hardware with explicit values:

```powershell
python scripts\pump_infuse_withdraw.py --port COM4 --baud 115200 --channel 1 --diameter 28.6 --rate 2.0 --volume 1.5
```

## Troubleshooting

`Port not found`
: Wrong COM number, cable unplugged, pump powered off, or missing driver.

`Access denied`
: Another program is holding the port.

Port opens but reply is empty
: Usually baud mismatch, wrong cable, or wrong instrument/COM port.

Echo mismatch
: The pump did not accept the value. Check diameter/rate/volume ranges and
units.

Wrong direction
: Check the sign of `set volume`. Positive is infuse, negative is withdraw.

Wrong pump drive moves
: Set `CHANNEL = 1` or `CHANNEL = 2` in `config_local.py`, or pass
`--channel 1` / `--channel 2`.
