# 00 — Documentation: start here

For **12_stage2only and its 13_3InstrumentCheck**, open the
[12 / 13 run guide](03_fixed_time_experiment/12_13_legacy_nmr_alternatives.md).
On WORK, from `C:\code\chemyx_pump` with the rig prepared, run the short check first:

```powershell
python scripts\13_3InstrumentCheck.py --live
```

After it finishes and the rig is ready for Stage 2, start the six-hour experiment:

```powershell
python scripts\12_stage2only.py --live
```

Both commands start without prompts; run one at a time. The guide links their
default YAML files and explains the one-scan check and 25-slot experiment.

For the current **Arduino + Chemyx + NMR, full 48-hour / 6-hour experiment**,
open **[03 — Fixed-time experiment](03_fixed_time_experiment/00_START_HERE.md)**.
Its commands, Python entry points and YAML files are listed together.
For a reaction already ready for Stage 2, open
[06 — stage2only](03_fixed_time_experiment/06_stage2only_run_guide.md).

To operate and troubleshoot the Stage 2 (`06`) run on the WORK laptop, read in this order:

1. [01 — Arduino manual operation](01_arduino/01_manual_commands.md): move the needle, inspect its position, establish HOME, recover tracking ([HOME recovery](01_arduino/02_home_recovery.md)).
2. [02 — Chemyx manual operation](02_chemyx/01_quick_reference.md): Channel 1 HOME, saved displacement, manual infuse/withdraw, pump recovery ([manual control and HOME](02_chemyx/02_manual_control_and_home.md)).
   For the NMR, run the [09 — NMR standalone tests](09_nmr_instrument/01_standalone_tests.md): connection test, one 1-scan run, one 4-scan run, each explaining any failure.
3. [Stage 2 experiment operation and recovery](03_fixed_time_experiment/10_stage2only_operation_and_recovery.md): prepare both instruments and the NMR, launch `06`, look up errors, restart after an interruption.
For the new primary dynamic peak-area method, use
[08 — SciPy stage2only run](03_fixed_time_experiment/08_stage2only_scipy_run_guide.md)
and [09 — SciPy analysis recovery](03_fixed_time_experiment/09_stage2only_scipy_analysis.md).

| Number | Group | Open this for |
|---|---|---|
| [01](01_arduino/00_START_HERE.md) | Arduino needle | Manual status, UP/DOWN, HOME, STOP and hardware reference |
| [02](02_chemyx/00_START_HERE.md) | Chemyx pump | Channel 1/2 control, syringe HOME, manual transfers and recovery |
| [03](03_fixed_time_experiment/00_START_HERE.md) | Three-instrument fixed-time experiment | Full 48-hour / 6-hour run, Stage 2 alternatives, and the 12 / 13 commands |
| [04](04_instrument_checkout/00_START_HERE.md) | Standalone instrument checkout | Separate bench check before an experiment |
| [05](05_nmr_analysis/00_START_HERE.md) | NMR analysis | Saved-data processing, plotting, phase methods and statistics |
| [06](06_deployment_and_backup/00_START_HERE.md) | Deployment and backup | WORK updates, offline dependencies, release inventory and rig-state backup |
| [07](07_other_experiments/00_START_HERE.md) | Other/older experiments | Older endpoint-controlled workflow and experiment templates |
| [08](08_reference/00_START_HERE.md) | Reference | YAML rules, valve guides and vendor API manuals |
| [09](09_nmr_instrument/00_START_HERE.md) | NMR instrument | Standalone connection test and 1-scan / 4-scan acquisitions with failure explanations |
| [90](90_reviews/00_START_HERE.md) | Technical reviews | Audit evidence and detailed implementation findings |
| [99](99_archive/00_START_HERE.md) | Historical archive | Superseded instructions and development reports |

Folders identify the workflow; filenames inside each folder are numbered in
reading order. Start with `00_START_HERE.md`, then `01_...`. Numbers identify
documentation groups, not experiment phases or script execution order.
Scripts and configs retain their existing names and locations.

All terminal commands run from `C:\code\chemyx_pump`. HOME is for local
validation/mocks; commands marked `--live` or `--nmr-check` belong on WORK
with the physical rig prepared.

If an old filename appears in a previous message or saved package, use the
[old-name → new-location map](08_reference/02_documentation_map.md).
Updates travel through HOME Git push, WORK Git pull, then an external-drive
copy from WORK to WORK2. HOME does not build offline packages.
