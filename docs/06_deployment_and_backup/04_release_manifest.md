# 04 — Source inventory and WORK-side transfer assets

[Deployment index](00_START_HERE.md) | [All documentation](../00_START_HERE.md)

The active deployment route is **HOME Git push -> WORK Git pull -> external
drive from WORK to WORK2**. HOME does not generate a release ZIP or offline
package. Existing export tooling/archives are retained as unused resources.

## Tracked source

The maintained inventory is [offline/release_manifest.yaml](../../offline/release_manifest.yaml).
It lists required source/resources for validation. Its presence does not make
package generation part of the workflow.

Source changes include `chemyx_lab`, `scripts`, Arduino source/configuration,
`configs`, `config_templates`, numbered `docs`, tests, root project/requirements
files, offline installation scripts and small committed fixtures. New files
must be committed to reach WORK through Git.

For the fixed-time experiment, the main files are:

| Purpose | File |
|---|---|
| Run | [04_si6_fixed_time_run.py](../../scripts/04_si6_fixed_time_run.py) |
| Controller | [si6_fixed_time.py](../../chemyx_lab/workflows/si6_fixed_time.py) |
| Raw NMR worker | [si6_fixed_time_nmr.py](../../chemyx_lab/workflows/si6_fixed_time_nmr.py) |
| Recipe | [si6_fixed_48h_6h.yaml](../../configs/experiments/si6_fixed_48h_6h.yaml) |
| Startup/restart behavior | [05 — Restart behavior](../03_fixed_time_experiment/05_restart_behavior.md) |

## Machine-local assets

Existing ignored `offline/wheelhouse`, installers, drivers, Arduino toolchain,
optional analysis resources, datasets and results are handled on WORK if needed
for the external-drive transfer. Git alone does not copy ignored assets. Retain
the destination's working interpreter or use the existing WORK-side installation
procedure if dependencies are missing. Vendor NMR software/licenses remain
machine-specific resources.

The fixed-time restart change adds no runtime dependency or firmware update.
See [offline requirements](02_offline_requirements.md) for environment details
and [rig-state backup](03_rig_state_backup.md) for preservation tools.
