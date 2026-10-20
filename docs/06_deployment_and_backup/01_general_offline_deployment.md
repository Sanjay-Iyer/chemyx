# 01 — HOME Git to WORK, then external drive to WORK2

[Deployment index](00_START_HERE.md) | [All documentation](../00_START_HERE.md)

This is the user's established transfer route. Do not add a HOME release-ZIP or
offline-package step. The user handles Git push/pull and the WORK-side drive copy.

| Machine | Role |
|---|---|
| HOME | Develop and locally validate source; source changes travel through Git |
| WORK | Pull the Git update; use its existing installed environment and prepare the external-drive copy |
| Second WORK laptop | Receive source and needed WORK-side assets from the external drive |

For the current three-instrument time-only experiment, use the
[Git and drive update guide](../03_fixed_time_experiment/04_offline_deployment.md)
and [run guide](../03_fixed_time_experiment/01_run_guide.md).

## Source and dependencies

Git carries committed Python, YAML, documentation and small test fixtures.
Include new/untracked files in the intended commit so they reach WORK.
Ignored dependencies, vendor tools, datasets, results and rig state do not
travel automatically through Git; WORK handles those when preparing the drive.
See [requirements](02_offline_requirements.md) for destination setup details.
Use the existing verified interpreter when its dependencies are already present.

## Physical configuration and history

COM assignments, NMR network settings, actual syringe geometry and calibrated
needle direction must match the instrument laptop's rig. Preserve that rig's
local state and experimental data while copying updates. The
[backup guide](03_rig_state_backup.md) is available when needed.

For the fixed-time runner, every launch starts fresh from manually restored
Channel 1=0 mL and Channel 2=5.00 mL. Old dose JSON requires no reconciliation.
`--live` starts directly, without interactive prompts. Prepare both pumps at
their existing HOME loading; the original HOME definitions are preserved.
The needle keeps its Arduino-only HOME: startup returns to that saved HOME
using the tracked position, then raises to UP, without defining a new zero. The older experiment
controller and standalone manual tools have their own documented state rules.
