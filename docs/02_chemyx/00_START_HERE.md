# 02 — Chemyx pump

[All documentation](../00_START_HERE.md)

## Main manual command: 26 (no HOME needed)

`scripts\26_chemyxmanual.py` moves either channel by the volume you type, then
sends STOP. It keeps **no** saved position, HOME, locks or dose records, so it
never refuses because of an earlier run, a Ctrl+C or a STOP. The pump does not
report plunger position, so choose the volume from what you see (and from the
run's log after a stopped run). Full guide:
[26 — simple manual Chemyx](../03_fixed_time_experiment/26_chemyx_manual.md).

```powershell
cd C:\code\chemyx_pump
```

Check the pump answers (no motion, no STOP):

```powershell
python scripts\26_chemyxmanual.py status --live
```

Push liquid out of / pull liquid into Channel 1:

```powershell
python scripts\26_chemyxmanual.py infuse --channel 1 --volume 1 --live
```

```powershell
python scripts\26_chemyxmanual.py withdraw --channel 1 --volume 1 --live
```

Channel 2 (default 1 mL/min):

```powershell
python scripts\26_chemyxmanual.py infuse --channel 2 --volume 0.5 --live
```

STOP both channels (or `--channel 1` / `--channel 2`):

```powershell
python scripts\26_chemyxmanual.py stop --live
```

Each move asks `Start this move? [y/N]:` once; `--yes` skips it. `--rate ML_MIN`
changes the speed for one command (defaults: Channel 1 5 mL/min, Channel 2
1 mL/min). For the two-instrument rig add
`--config configs\experiments\23_stage1stage2.yaml`. Use `--mock` in place of
`--live` to rehearse without the pump. Close any 20–25 run, the Chemyx GUI or
another program on the pump's COM port first.

| Item | File |
|---|---|
| Main manual command (no HOME) | [scripts/26_chemyxmanual.py](../../scripts/26_chemyxmanual.py) |
| Its settings (default rates) | [configs/experiments/26_chemyxmanual.yaml](../../configs/experiments/26_chemyxmanual.yaml) |
| Pump COM port and syringe sizes it uses | `chemyx` section of [21_stage2only.yaml](../../configs/experiments/21_stage2only.yaml) and `channel_2` of [20_stage1stage2.yaml](../../configs/experiments/20_stage1stage2.yaml) (two-instrument rig: [23_stage1stage2.yaml](../../configs/experiments/23_stage1stage2.yaml)) |

## Older tool with a saved HOME: chemyx_control.py

`scripts\chemyx_control.py` (and its `chemyx_home.py` alias) is the older manual
tool. It keeps its own saved HOME and position record, and checks every move
against it, so it **refuses** to withdraw, infuse or go HOME:

- until a HOME has been saved with `set-home` (with the plunger physically at HOME),
- after its own `stop`, which marks the position UNKNOWN until `set-home`,
- when its record no longer matches the syringe, for example after any 20–25
  run (20–25 and 26 never update that record).

Use it only with the older workflows (04, 06, 10, 12, 14) that rely on that
record. Its commands are in [01 — Quick reference](01_quick_reference.md) and its
rules in [02 — Manual control and HOME](02_manual_control_and_home.md).
[03 — Driver reference](03_driver_reference.md) and
[04 — Channel configuration](04_channel_configuration.md) are supporting detail.

| Older tool | File |
|---|---|
| Manual command with saved HOME | [scripts/chemyx_control.py](../../scripts/chemyx_control.py) |
| HOME alias | [scripts/chemyx_home.py](../../scripts/chemyx_home.py) |
| Manual settings and HOME convention | [configs/chemyx/manual_control.yaml](../../configs/chemyx/manual_control.yaml) |
| Pump endpoint | [COM4](../../configs/machines/si6_real_COM4.yaml) or [COM6](../../configs/machines/si6_real_COM6.yaml) |

Its saved-state inspection, with no commanded movement:

```powershell
python scripts\chemyx_control.py status --channel both --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

The older tool defaults to 1 mL at 10 mL/min for manual transfers. Channel 1
starts empty; Channel 2 HOME is 5 mL loaded. Switching COM ports does not migrate
its saved HOME. For an older experiment's dose/restart error, use
[fixed-time run recovery](../03_fixed_time_experiment/01_run_guide.md#8--errors-and-recovery).
