# plain_text_commands/

**THIS DIRECTORY = raw pump text commands ONLY. No Python. Paste/type these into
a terminal (PuTTY / TeraTerm / screen) to talk to the pump directly.**

(For the same operations wrapped in Python via pyserial, see
[`../python_functions/`](../python_functions/).)

---

## What's here — one `.txt` file per operation

| File | Operation | Command(s) sent |
|------|-----------|-----------------|
| `01_set_units.txt`    | Set units              | `set units 0` |
| `02_set_diameter.txt` | Set syringe diameter   | `set diameter 4.5` |
| `03_set_rate.txt`     | Set flow rate          | `set rate 1.0` |
| `04_set_volume.txt`   | Set volume (± = dir.)  | `set volume 0.5` / `set volume -0.5` |
| `05_infuse.txt`       | Infuse (full sequence) | units → diameter → rate → `set volume 0.5` → `start` |
| `06_withdraw.txt`     | Withdraw (full seq.)   | units → diameter → rate → `set volume -0.5` → `start` |
| `07_start.txt`        | Start                  | `start` |
| `08_stop.txt`         | Stop                   | `stop` |
| `09_pause.txt`        | Pause                  | `pause` |
| `10_help.txt`         | Help / read response   | `help` |

Each file has a `#` comment header explaining what it does and the expected
echo. **Lines beginning with `#` are notes for you — do NOT send them.** Send
only the command line(s) below the divider.

## How to send these

1. **Open a serial terminal**
   - **Windows:** [PuTTY](https://www.putty.org/) or
     [TeraTerm](https://teratermproject.github.io/). In PuTTY pick
     **Connection type: Serial**, set **Serial line** to your COM port (find it
     in *Device Manager → Ports (COM & LPT)*) and **Speed** to the pump's baud
     rate.
   - **Mac/Linux:**
     ```
     screen /dev/tty.usbserial-XXXX 9600     # replace device + baud rate
     ```
     (`ls /dev/tty.*` on Mac, `ls /dev/ttyUSB*` on Linux to find the device;
     exit `screen` with `Ctrl-A` then `K`.)

2. **Match the baud rate** to whatever the pump's screen shows (commonly 9600 or
   38400). Framing is 8 data bits, no parity, 1 stop bit (8-N-1).

3. **Type or paste the command line(s)** and press **Enter** after each one —
   Enter sends the carriage return (`\r`) the pump requires. Watch for the echo
   (e.g. `diameter = 4.5`); if the echoed value differs from what you typed, the
   value was out of range and did **not** take.

## Dual-channel note (Fusion 4000X)

The 4000X has two pump drives. To target one drive, **prefix the command with
the channel number**:

```
2 set rate 1.0      <- channel 2 only
1 start             <- channel 1 only
set rate 1.0        <- no prefix = default/both
```

## Direction reminder

Volume **sign** sets direction: **positive = infuse**, **negative = withdraw**.
Everything else (units, diameter, rate) is the same for both.
