# 15 / 16 — Simple Stage 2 run and instrument check (no state files, no lockouts)

[Experiment index](00_START_HERE.md) | [15 script](../../scripts/15_stage2_simple.py) | [16 script](../../scripts/16_3_instrument_check_simple.py) | [Shared helpers](../../chemyx_lab/workflows/simple_rig.py)

Simpler replacements for `12` and `13`. They use the same Arduino, Chemyx and
NMR drivers, the same physical recipe and the same 04/05 legacy NMR completion,
but **no** `needle_state.json`, HOME journals, pump ledgers, dose records or
port lock files. Nothing from an earlier run can block the next one.

| | 12 / 13 | 15 / 16 |
|---|---|---|
| Needle position | `needle_state.json`, must be a valid HOME reference | The two one-integer files `needle_control.py` uses (`runs/arduino/simple_needle_*.txt`) |
| After a failure | Can leave a flag that blocks manual and automated commands | Files keep their last completed values; nothing blocks; check the needle by eye |
| Chemyx STOP reply | Must be exactly `pump stop` | Any reply (logged); only `error`/`invalid` in a set/start reply fails |
| NMR failure | 12 keeps an unresolved START pending in memory | Each slot independent; failed slot skipped |
| Output | log, summary JSON, config copy, live analysis | `log.txt` and `nmr_raw\*.dx` only |
| End of run | Needle UP | Needle returned to HOME (0) |

## Before every run

1. HOME set once with `needle_control.py set-home` at the sampling depth. The
   runs move to that saved HOME first and end there (saved position 0).
2. Channel 1 syringe empty (plunger fully in).
3. NMR on, NMReady open, idle on the touchscreen.
4. No other program on COM3/COM4 (Arduino Serial Monitor, Chemyx GUI, another script).

Both `--live` commands start immediately with no prompt.

## Commands on WORK

```powershell
cd C:\code\chemyx_pump
```

Short check first (needle UP/DOWN, 1 mL out/back, one scan):

```powershell
python scripts\16_3_instrument_check_simple.py --live
```

Then, with the needle back at HOME and Channel 1 empty, the six-hour run:

```powershell
python scripts\15_stage2_simple.py --live
```

Rehearse on HOME without hardware:

```powershell
python scripts\16_3_instrument_check_simple.py --mock
```

```powershell
python scripts\15_stage2_simple.py --mock
```

## What each run does

`16`: connect Arduino and Chemyx, check the NMR answers (warning only), needle
UP one increment and DOWN to HOME, Channel 1 withdraw 1 mL and infuse 1 mL at
5 mL/min, one NMR scan saved as `nmr_raw\check_0001.dx`, then a PASS/FAIL line
per part. Exit code 0 only when every part passes.

`15`: needle UP, then 25 slots every 15 minutes (0:00 through 6:00), each with
12's recipe: withdraw 8 mL (needle UP), needle DOWN to HOME, withdraw 5 mL,
settle 10 s, 8-scan NMR (`nmr_raw\stage_2_0001.dx` ...), needle UP, return
13 mL, wash 5 mL out and back. A slot whose start time has already passed is
skipped. At the end the needle returns to HOME. Analyse the spectra afterwards
with the command printed at the end of the log (`09_analyze_stage2only_scipy.py`).

## Errors and Ctrl+C

| Event | What happens |
|---|---|
| NMR failure or timeout | Logged; that slot's spectrum is skipped; the physical sample return and wash still run; the schedule continues |
| Needle or pump error | STOP to pump and Arduino, needle returned to HOME, ports closed, exit |
| Ctrl+C | Same as a hardware error; press Ctrl+C again to skip the return to HOME |
| Needle move without DONE | Not counted; the needle is **not** moved again; the log says to check it by eye and run `needle_control.py set-home` |
| Leftover Arduino firmware fault | Cleared at connect |

When a run stops mid-transfer, the log says roughly how much liquid is in the
syringe; empty Channel 1 before the next run.

## Settings

Edit [15_stage2_simple.yaml](../../configs/experiments/15_stage2_simple.yaml):
COM ports, travel range (-3 to +5 increments), volumes, rates, schedule and NMR
settings. [16_3_instrument_check_simple.yaml](../../configs/experiments/16_3_instrument_check_simple.yaml)
reads that file and adds only the check volumes, rate and scan count.

## Limits

- Position is commanded steps only (no encoder or switch), exactly as before.
- `needle_control.py` shares the needle position with 15/16. `chemyx_control.py`
  keeps its own saved pump position and does not see 15/16 moves; a successful
  run ends with Channel 1 empty, which matches its HOME definition.
- Kept safety: firmware 120-second move timeout, configured travel range,
  syringe 0 to 20 mL bounds, STOP on every error.

Offline tests: `python -m pytest tests\test_simple_rig.py -q`.
