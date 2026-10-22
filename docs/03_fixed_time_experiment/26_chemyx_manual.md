# 26 — Simple manual Chemyx control (withdraw, infuse, stop, status)

[Experiment index](00_START_HERE.md) | [26 script](../../scripts/26_chemyxmanual.py) | [26 settings](../../configs/experiments/26_chemyxmanual.yaml) | [Three-instrument 20 / 21 / 22](20_22_three_instrument_runs.md) | [Two-instrument 23 / 24 / 25](23_25_two_instrument_runs.md)

Use 26 to move Channel 1 or 2 by hand, for example to empty liquid left in a
syringe after a 20–25 run was stopped early. It uses the same pump code as
20–25 (timed transfer, then STOP; any STOP reply is accepted) and keeps **no**
saved position, HOME, locks, journals or dose records. It writes no files and
reads none back, so an earlier stopped run, Ctrl+C or STOP can never make it
refuse a command.

| Command | What it does |
|---|---|
| `status` | Checks the pump answers (HELP) and prints both syringes and default rates. No motion, no STOP |
| `stop` | STOP to both channels (`--channel 1` or `2` for one) |
| `infuse --channel N --volume ML` | Pushes ML out of syringe N, then STOP |
| `withdraw --channel N --volume ML` | Pulls ML into syringe N, then STOP |

Before a move 26 prints the channel, volume, rate and time and asks
`Start this move? [y/N]:` once (`--yes` skips it). `--rate ML_MIN` changes the
speed for that command only. Defaults: Channel 1 5 mL/min, Channel 2 1 mL/min
(`manual:` in [26_chemyxmanual.yaml](../../configs/experiments/26_chemyxmanual.yaml)).

## Commands on WORK

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\26_chemyxmanual.py status --live
```

Empty Channel 1 after a stopped run, using the mL the run's log gave (here 3.9 mL):

```powershell
python scripts\26_chemyxmanual.py infuse --channel 1 --volume 3.9 --live
```

```powershell
python scripts\26_chemyxmanual.py withdraw --channel 2 --volume 0.5 --live
```

```powershell
python scripts\26_chemyxmanual.py stop --live
```

Rehearse any of them on HOME with `--mock` in place of `--live`.

## What 26 does not do

- **It does not know where the plunger is.** The Chemyx does not report it and
  26 keeps no record, so choose the volume from what you see and from the run's
  log (for example `about 3.92 mL left; empty Channel 1 by hand`). Each move is
  only checked against the syringe size (at most 20 mL). Do not infuse more than
  the syringe holds: watch the plunger and press Ctrl+C to stop early.
- **It does not update `chemyx_control.py`'s saved record.** That older tool
  keeps its own position record and still refuses moves when the record does not
  match (for example after its own `stop`). 26 is independent of it; use 26 for
  quick manual moves around the 20–25 runs.

## Errors and Ctrl+C

| Event | What happens |
|---|---|
| Ctrl+C during a move | STOP to that channel; prints about how many mL moved (estimated from pump time); exit 130 |
| Pump rejects a command | STOP; error printed; exit 1 |
| Port busy (a 20–25 run or the Chemyx GUI is open) | Nothing sent; exit 1. Close the other program first |
| Volume above the syringe size, zero, or no `--channel` | Refused before the pump is contacted; exit 2 |
| Confirmation declined | Nothing sent; exit 3 |

The port and syringe sizes come from 20's file (Channel 1 from 21's `chemyx`
section, Channel 2 from 20's `channel_2` section), so they match the runs. For
the two-instrument settings add `--config configs\experiments\23_stage1stage2.yaml`.

Offline tests: `python -m pytest tests\test_26_chemyxmanual.py -q`.
