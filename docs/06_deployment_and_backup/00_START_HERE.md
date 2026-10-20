# 06 — Deployment and rig-state backup

[All documentation](../00_START_HERE.md)

**HOME pushes to Git -> WORK pulls -> WORK transfers by external hard drive to
WORK2.** HOME does not build offline packages or release ZIPs for this workflow.
Existing unused archives are left alone.

Start with the [fixed-time Git and drive guide](../03_fixed_time_experiment/04_offline_deployment.md).

| Guide | Purpose |
|---|---|
| [01 — Git and drive workflow](01_general_offline_deployment.md) | Source updates and transfer roles across the three laptops |
| [02 — Offline requirements](02_offline_requirements.md) | Existing interpreter/dependencies and WORK-side installation reference |
| [03 — Rig-state backup](03_rig_state_backup.md) | Preserve the relevant rig's configuration, state and results |
| [04 — Source inventory](04_release_manifest.md) | Tracked source versus machine-local assets |

| Reference/tool | File |
|---|---|
| State backup and restore planning | [scripts/rig_state_backup.py](../../scripts/rig_state_backup.py) |
| Environment validation | [scripts/verify_si6_environment.py](../../scripts/verify_si6_environment.py) |
| Maintained source/resource inventory | [offline/release_manifest.yaml](../../offline/release_manifest.yaml) |

Dependency installation is needed only when the destination environment lacks
required packages. Any dependency preparation for WORK2 happens on WORK, using
the existing external-drive workflow.
