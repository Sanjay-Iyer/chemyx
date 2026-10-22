# 02 — Chemyx pump

[All documentation](../00_START_HERE.md)

Read [01 — Quick reference](01_quick_reference.md) for commands and
[02 — Manual control and HOME](02_manual_control_and_home.md) for position,
loading and dose restrictions. [03 — Driver reference](03_driver_reference.md)
and [04 — Channel configuration](04_channel_configuration.md) are supporting detail.

**After a 20–25 run, use 26.** [26 — simple manual Chemyx](../03_fixed_time_experiment/26_chemyx_manual.md)
(`scripts\26_chemyxmanual.py withdraw|infuse|stop|status`) keeps no saved
position, HOME, locks or dose records, so it works even when a run was stopped
mid-transfer. `chemyx_control.py` below keeps its own saved record and refuses
moves when that record does not match the syringe (for example after a 20–25
run, or after its own `stop`, which marks the position UNKNOWN until `set-home`).

| Item | File |
|---|---|
| Simple manual command (no saved state) | [scripts/26_chemyxmanual.py](../../scripts/26_chemyxmanual.py) |
| Manual Python command with saved HOME | [scripts/chemyx_control.py](../../scripts/chemyx_control.py) |
| HOME alias | [scripts/chemyx_home.py](../../scripts/chemyx_home.py) |
| Manual settings and HOME convention | [configs/chemyx/manual_control.yaml](../../configs/chemyx/manual_control.yaml) |
| Pump endpoint | [COM4](../../configs/machines/si6_real_COM4.yaml) or [COM6](../../configs/machines/si6_real_COM6.yaml) |

Typical saved-state inspection, with no commanded movement:

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\chemyx_control.py status --channel both --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

Use the quick reference for individual live movements. Both channels currently
default to 1 mL at 10 mL/min for manual transfers; experiment settings are separate.
Channel 1 starts empty; Channel 2 HOME is 5 mL loaded. Switching COM ports does
not migrate a saved HOME. For an experiment dose/restart error, use
[fixed-time run recovery](../03_fixed_time_experiment/01_run_guide.md#8--errors-and-recovery).
