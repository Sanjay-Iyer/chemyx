# 02 — Chemyx pump

[All documentation](../00_START_HERE.md)

Read [01 — Quick reference](01_quick_reference.md) for commands and
[02 — Manual control and HOME](02_manual_control_and_home.md) for position,
loading and dose restrictions. [03 — Driver reference](03_driver_reference.md)
and [04 — Channel configuration](04_channel_configuration.md) are supporting detail.

| Item | File |
|---|---|
| Manual Python command | [scripts/chemyx_control.py](../../scripts/chemyx_control.py) |
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
