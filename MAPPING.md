# Mapping: the same operations in two forms

Two parallel directories, the **same 10 operations** in each:

- [`python_functions/`](python_functions/) — **Python** (pyserial) wrapper layer. Run with Python.
- [`plain_text_commands/`](plain_text_commands/) — **raw pump text** only. Type/paste into a serial terminal.

Each row below is one operation. The Python file and the text file are the same
thing expressed two ways. Every command ends with a carriage return (`\r`); on a
terminal that's the **Enter** key.

| # | Operation | `python_functions/` | `plain_text_commands/` | Exact command(s) on the wire |
|---|-----------|---------------------|------------------------|------------------------------|
| 1 | Set units              | `example_01_set_units.py` → `Pump.set_units()`   | `01_set_units.txt`    | `set units 0` |
| 2 | Set syringe diameter   | `example_02_set_diameter.py` → `Pump.set_diameter()` | `02_set_diameter.txt` | `set diameter 4.5` |
| 3 | Set flow rate          | `example_03_set_rate.py` → `Pump.set_rate()`     | `03_set_rate.txt`     | `set rate 1.0` |
| 4 | Set volume (± = dir.)  | `example_04_set_volume.py` → `Pump.set_volume()` | `04_set_volume.txt`   | `set volume 0.5` / `set volume -0.5` |
| 5 | **Infuse** (sequence)  | `example_05_infuse.py` → `Pump.infuse()`         | `05_infuse.txt`       | `set units 0` · `set diameter 4.5` · `set rate 1.0` · `set volume 0.5` · `start` |
| 6 | **Withdraw** (sequence)| `example_06_withdraw.py` → `Pump.withdraw()`     | `06_withdraw.txt`     | `set units 0` · `set diameter 4.5` · `set rate 1.0` · `set volume -0.5` · `start` |
| 7 | Start                  | `example_07_start.py` → `Pump.start()`           | `07_start.txt`        | `start` |
| 8 | Stop                   | `example_08_stop.py` → `Pump.stop()`             | `08_stop.txt`         | `stop` |
| 9 | Pause                  | `example_09_pause.py` → `Pump.pause()`           | `09_pause.txt`        | `pause` |
| 10| Help / read response   | `example_10_help.py` → `Pump.help()`             | `10_help.txt`         | `help` |

The reusable Python class lives in [`python_functions/pump.py`](python_functions/pump.py)
(with `PORT` / `BAUD_RATE` / `CHANNEL` config at the top); the `example_*.py`
scripts are thin one-operation demos that call it.

## Key facts that apply to BOTH forms

- **Terminator:** every command is followed by `\r` (Python appends it; on a
  terminal you press **Enter**).
- **Direction:** volume **sign** chooses direction — **positive = infuse**,
  **negative = withdraw**.
- **Echo check:** the pump echoes the value it parsed (e.g. `diameter = 4.5`). If
  the echo doesn't match what you sent, the value was out of range and did **not**
  take. The Python layer verifies this automatically and raises
  `EchoMismatchError`; in the text form you check it by eye.
- **Dual channel:** prefix a command with the channel number to target one drive
  of the 4000X — Python: `pump.set_rate(1.0, channel=2)`; text: `2 set rate 1.0`.
  No prefix = default/both.
- **Limits:** diameter `0.103–40.000 mm`; flow up to `~170.5 mL/min`.
