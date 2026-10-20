# 01 — Arduino needle

[All documentation](../00_START_HERE.md)

Start with [01 — Manual commands and configuration](01_manual_commands.md).
For the full experiment go to [03 — Fixed-time experiment](../03_fixed_time_experiment/00_START_HERE.md).

| Need | Guide |
|---|---|
| Status, UP/DOWN, HOME and STOP | [01 — Manual commands](01_manual_commands.md) |
| Interrupted/unknown HOME recovery | [02 — HOME recovery](02_home_recovery.md) |
| Architecture and saved position | [03 — System overview](03_system_overview.md) |
| Wiring and firmware | [04 — Hardware schematic](04_hardware_schematic.md), [05 — Firmware](05_firmware.md) |
| Motion limits and fault behavior | [06 — Safety/failure modes](06_safety_and_failure_modes.md), [07 — Hardware prerequisites](07_hardware_before_live_motion.md) |
| Staged commissioning tests | [08 — Connection](08_connection_test.md), [09 — Unloaded motor](09_unloaded_motor_test.md), [10 — Needle axis](10_needle_axis_test.md), [11 — Integrated test](11_integrated_system_test.md) |

The staged tests are commissioning references; they are not steps to repeat
before each established run. The [Arduino project README](../../arduino/README.md)
retains implementation detail. The [older DM542S bring-up tools](../../arduino/dm542s_hello_world/README.md)
use a different protocol and are historical.
