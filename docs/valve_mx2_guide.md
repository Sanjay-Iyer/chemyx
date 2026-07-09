# IDEX MX Series II Valve Guide (MXX777-601)

How to control the Rheodyne/IDEX MX Series II module over USB from this
repo, and how to recover when position commands are silently ignored.

## The Hardware

| Item | Value |
|---|---|
| Module | IDEX Rheodyne MX Series II, low-pressure ("MXX") family, bare control PCB |
| Valve | **MXX777-601 — 2-POSITION, 6-port switching valve** |
| Motor | Nippon Pulse PFC42H-48Q4 stepper (12 ohm), driven by the board's own controller |
| Power | 24 V DC barrel jack — **never exceed 24 V** |
| Control link | USB-B. The board's FTDI FT232R shows up as a virtual COM port |
| USB ids | FTDI defaults, VID:PID `0403:6001` (IDEX has no own vendor id) |

**The critical detail:** "6-port" describes the plumbing. The valve has only
**two selectable positions, 1 and 2**. The driver board **silently ignores**
a command to any position the valve does not have — no ack, no error, no
movement. That is why `chemyx_lab/valve.py` defaults to `ports=2` and
raises `ValueError` before anything is sent for positions outside 1..2.

## Wire Protocol (IDEX doc 2321382G)

The authoritative reference is IDEX document **2321382G**, "UART/USB
Communication Protocol for TitanEX/TitanEZ/TitanHP, TitanHT Driver Boards
and MX Series II Modules", included in this repo at
`.codex-temp/idex-titan-uart/UART USB Communication Protocol for TitanEX.pdf`
(the same document ships in IDEX's MX Series II Driver Development Package,
File-1418039677).

Serial settings: **19200 baud (factory default), 8N1, no handshaking.**
Every packet ends with CR (`\r`, 0x0D). Everything is ASCII.

| Command | Bytes sent | Meaning | Response |
|---|---|---|---|
| `P01`..`P02` | `50 30 31 0D` | Move to position (two ASCII-hex digits) | `\r` ack if accepted; **nothing if ignored** |
| `M` | `4D 0D` | Home the valve | `\r` ack |
| `S` | `53 0D` | Status | position as 2 hex digits + `\r`, or error code, or `*` while moving |
| `D` | `44 0D` | Read command mode | `01`..`05` + `\r` |
| `Fxx` | e.g. `46 30 33 0D` | **Set command mode** (BCD = `F03`) | `\r` ack; **active only after board reset** |
| `Q` | `51 0D` | Read valve profile | 2 hex digits + `\r` |
| `R` | `52 0D` | Read firmware revision | 2 hex digits + `\r` |
| `E` | `45 0D` | Read last error code | 2 hex digits + `\r` |

Busy behaviour: while the motor is moving the board answers **any** input
with `*` (0x2A) and executes nothing.

Status/error codes from `S` or `E`: `63`=valve failure (99), `58`=NVM error
(88), `4D`=configuration/command-mode error (77), `42`=positioning error
(66), `37`=data integrity (55), `2C`=CRC (44).

## Command Mode: Why Home Works But Moves Do Not

The board accepts position commands over USB only when its command mode is
**BCD (0x03)** — IDEX's own software sets 2-position valves to BCD. In the
wrong mode (e.g. level logic, 0x01) the board still homes and answers
status queries, but **ignores `P` commands** — exactly the "home works,
nothing else does" symptom.

Check and fix from this repo:

```powershell
python scripts\test_valve.py --port COM7            # step 2 prints the mode
python scripts\test_valve.py --port COM7 --set-bcd  # stores F03, then STOP
```

After `--set-bcd`: **unplug the 24 V supply, wait a few seconds, plug it
back in** (the new mode only becomes active after a board reset), then run
the test again without `--set-bcd`. Alternatively set BCD once with IDEX's
own control software.

## Running The Test

No hardware (works on any laptop, zero setup):

```powershell
python scripts\test_valve.py --mock                    # happy path
python scripts\test_valve.py --mock --mock-level-logic # simulated failure
```

Real hardware (work laptop). Configure the port once per machine — never in
committed code:

```powershell
$env:MXVALVE_PORT="COM7"          # or: copy configs\valve.local.example.json
                                  #     to configs\valve.local.json and edit
python scripts\test_valve.py      # auto-detects the FTDI port if unset
```

The test: lists COM ports with FTDI ids, reads firmware/profile/command
mode/last error, reads the position, proves that position 5 is rejected in
Python before any bytes are sent, homes, then toggles 1 → 2 → 1 → 2 waiting
for ready and reading the position back after every move. Every command and
response is printed raw (bytes + hex) and decoded. Exit code 0 means every
step passed.

Correct output ends with:

```text
--- Summary ---
All steps passed: the valve toggled 1 -> 2 -> 1 -> 2 with confirmed readbacks.
```

## Python API

```python
from chemyx_lab.valve import MX_valve, find_address

with MX_valve(find_address(), ports=2, verbose=True) as valve:  # ports=2 is the default
    valve.home()
    valve.change_port(2)      # blocks until the board reports position 2
    print(valve.get_port())   # -> 2
    valve.change_port(5)      # ValueError: valve has only 2 positions, got 5
```

`MX_valve`, `find_address`, `get_port`, `change_port` keep the
linnarsson-lab MXII_valve names, but `ports` defaults to 2 here and every
requested position is validated before it reaches the wire. The serial
transport (framing, CR terminator, byte encoding, DTR/RTS handling) is the
same proven code path the working home command uses.

## Troubleshooting

| Symptom | Check |
|---|---|
| Home works, position moves ignored | `D` command mode must be `03` (BCD): `--set-bcd` + power cycle |
| No response to anything | Baud (19200 default), cable, FTDI VCP driver, vendor software holding the port |
| Status returns `4D` | Configuration/command-mode error: re-set BCD, power cycle |
| Move "passes" without motion | Was the valve already at the target? Toggle to the *other* position |
| Multiple FTDI devices found | Pass `--port COMn` explicitly (the pump may also be FTDI) |
