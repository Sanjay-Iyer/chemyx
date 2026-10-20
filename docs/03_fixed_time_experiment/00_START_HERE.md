# 03 — Three-instrument fixed-time experiment

[All documentation](../00_START_HERE.md)

This is the current **Arduino + Chemyx + NMR** workflow: **48-hour Phase 1,
one 1.86 mL Channel 2 dose, then a full 6-hour Phase 2**. NMR only saves raw
data during the experiment; missing spectra and analysis results cannot end a phase.

| Read in order | Purpose |
|---|---|
| [01 — Run guide](01_run_guide.md) | Setup, physical references, launch, results and error recovery |
| [02 — Scripts, configs and restart review](02_scripts_configs_and_restart_review.md) | Implementation inventory, JSON blockers and current pause/restart limitations |
| [03 — Post-run analysis](03_post_run_analysis.md) | Process the saved spectra after acquisition |
| [04 — Git and drive update](04_offline_deployment.md) | HOME pushes to Git; WORK pulls, then transfers by external drive to WORK2 |
| [05 — Restart behavior](05_restart_behavior.md) | Fresh dose/pump setup; return to the existing Arduino HOME |
| [06 — stage2only](06_stage2only_run_guide.md) | Separate six-hour Stage 2, 13 measurements from 0:00 through 6:00, no repeat dose |
| [07 — Stage 2 analysis recovery](07_stage2only_analysis_recovery.md) | Select a data folder and re-run full Stage 2 processing independently |
| [08 — Stage 2 only with SciPy dynamic area](08_stage2only_scipy_run_guide.md) | Same six-hour schedule with primary 8σ prominence / width-0.95 live analysis |
| [09 — Re-run SciPy Stage 2 analysis](09_stage2only_scipy_analysis.md) | Folder-picker recovery using exactly the same dynamic primary method as 08 |
| [10 — Stage 2 operation and recovery](10_stage2only_operation_and_recovery.md) | Offline manual for the `06` run: Arduino and Channel 1 preparation, launch, every stopping error, recovery checks, restart |

For the Stage 2 (`06`) run use this order: [Arduino manual operation](../01_arduino/01_manual_commands.md),
then [Chemyx manual operation](../02_chemyx/01_quick_reference.md), then
[10 — Stage 2 operation and recovery](10_stage2only_operation_and_recovery.md).

## Python and configuration together

| Task | Python entry point | Configuration |
|---|---|---|
| Run, validate or simulate | [04_si6_fixed_time_run.py](../../scripts/04_si6_fixed_time_run.py) | [si6_fixed_48h_6h.yaml](../../configs/experiments/si6_fixed_48h_6h.yaml) |
| Run Stage 2 only | [06_si6_stage2only_run.py](../../scripts/06_si6_stage2only_run.py) | [si6_stage2only_6h.yaml](../../configs/experiments/si6_stage2only_6h.yaml) |
| Re-run Stage 2 analysis | [07_analyze_stage2only.py](../../scripts/07_analyze_stage2only.py) | [analysis.yaml](../../configs/nmr/analysis.yaml), with a folder picker or explicit data directory |
| Run Stage 2 only with dynamic primary area | [08_si6_stage2only_scipy_run.py](../../scripts/08_si6_stage2only_scipy_run.py) | [si6_stage2only_scipy_6h.yaml](../../configs/experiments/si6_stage2only_scipy_6h.yaml); separate numerical [SciPy profile](../../configs/nmr/stage2only_scipy.yaml) |
| Re-run dynamic Stage 2 analysis | [09_analyze_stage2only_scipy.py](../../scripts/09_analyze_stage2only_scipy.py) | [analysis.yaml](../../configs/nmr/analysis.yaml) plus the [SciPy profile](../../configs/nmr/stage2only_scipy.yaml) |
| Pump endpoint and NMR network | Same run command, `--machine-config` | [COM4](../../configs/machines/si6_real_COM4.yaml) or [COM6](../../configs/machines/si6_real_COM6.yaml) |
| Needle calibration | Same run command, `--arduino-config` | [arduino_real_COM3.yaml](../../arduino/configs/arduino_real_COM3.yaml) |
| Shared syringe HOME/loading | Read via the fixed-time preset | [manual_control.yaml](../../configs/chemyx/manual_control.yaml) |
| Post-run processing | [05_analyze_fixed_time_run.py](../../scripts/05_analyze_fixed_time_run.py) | [analysis.yaml](../../configs/nmr/analysis.yaml) supplies processing defaults; the wrapper defaults to ACME |

## Commands from the repository root

On HOME use the `ai` environment for local checks:

```powershell
cd C:\code\chemyx_pump
conda activate ai
python scripts\04_si6_fixed_time_run.py --validate-config
python scripts\04_si6_fixed_time_run.py --mock
```

On WORK use its existing verified Python. The default preset selects Chemyx
COM4, Arduino COM3 and 8 NMR scans. With the rig physically prepared:

```powershell
python scripts\04_si6_fixed_time_run.py --live
```

For Chemyx COM6:

```powershell
python scripts\04_si6_fixed_time_run.py --live --config configs\experiments\si6_fixed_48h_6h.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

`--live` starts immediately: no startup `y` or prompts during either phase or dose.
Prepare both pumps at their established HOME loading before launching; their
original HOME records are preserved.
Use the run guide for reference/loading requirements and the separate
`--nmr-check` acquisition on WORK. The [standalone bench checkout](../04_instrument_checkout/00_START_HERE.md)
is a separate workflow.

After acquisition, on a machine with the analysis packages installed:

```powershell
python scripts\05_analyze_fixed_time_run.py
```

Each experiment saves a unique folder under `results/fixed_time/live` (or
`mock`). Analysis writes a new subfolder without changing raw data.

There is currently **no pause/resume**. Ctrl+C stops the run; a fresh launch
starts fresh clocks with Channel 1 at 0 mL and Channel 2 at 5 mL. Old dose
records require no reconciliation. Neither instrument's official HOME is reset.
The needle uses the existing HOME established
by the Arduino-only script: startup moves to that HOME, then UP, without setting
a new zero. Its current position must be tracked. See document 05.
Do not follow the older `02_si6_experiment.py` instructions for this full-duration run.

For a reaction already ready for Stage 2, use the separate
[stage2only guide](06_stage2only_run_guide.md):

```powershell
python scripts\06_si6_stage2only_run.py --live
```

It skips Phase 1 and all reagent addition, and includes a final measurement at
6:00. A separate background process updates `analysis_live/index.html` after
saved measurements for review during the run. Processing errors do not stop or
shorten sampling. Its independent config/results do not change the full-run schedule.
