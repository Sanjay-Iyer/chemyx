# Three-instrument, two-channel workflow: quick guide

Use this guide for the updated **Chemyx + Arduino needle + NMReady NMR** workflow:
**Channel 1 sampling → Stage 1 completion → one Channel 2 reagent dose → Channel 1 sampling in Stage 2.**
Both pump channels use one serial connection and run sequentially. No script rewrite is needed to adjust the supported parameters below.

Run PowerShell commands from `C:\code\chemyx_pump`. HOME is simulation-only; the live commands below are for the commissioned instrument laptop. This guide does not execute them.

## 1. Files you need

Paths below are relative to `C:\code\chemyx_pump`.

| File | Purpose / what you edit |
|---|---|
| `scripts/02_si6_experiment.py` | Main experiment runner; coordinates all three instruments and both pump channels. |
| `scripts/01_three_instrument_system_test.py` | Instrument diagnostics before an experiment. |
| `config_templates/experiments/si6_two_stage_nominal.yaml` → `configs/experiments/si6_run.local.yaml` | Copy source → your run recipe: identity, both syringes, sampling cycle, dose, NMR acquisition, stages, completion and QC. |
| `config_templates/machines/si6_instrument_settings.example.yaml` → `configs/machines/si6_work.local.yaml` | Copy source → your verified Chemyx COM port and NMR address/timeouts. |
| `arduino/configs/arduino.local.yaml` | Review on the actual rig: Arduino COM port, firmware identity, needle calibration, UP/DOWN geometry and motion limits. Reference: `arduino/configs/arduino.example.yaml`. |
| `configs/nmr/analysis.yaml` + optional `configs/nmr/analysis.local.yaml` | Production spectrum processing, phase/baseline and peak QC. Local settings override shared defaults. These files are read automatically by the processor. |
| `config_templates/experiments/si6_two_stage_fast_sim.yaml` | Separate mock-only recipe for a fast, complete controller simulation. Never select it for live use. |

**Always select the updated recipe explicitly.** The 01/02 scripts still default to the older, single-channel `configs/experiments/02_si6_automated_nmr.yaml`. The generic `si6_two_channel_once.yaml` example demonstrates channel routing; use `si6_two_stage_nominal.yaml` for this chemistry-completion workflow.

## 2. Prepare your run files and Python

On HOME:

```powershell
Set-Location C:\code\chemyx_pump
conda activate ai

# Create only if absent; preserve existing reviewed settings.
if (!(Test-Path configs\experiments\si6_run.local.yaml)) {
    Copy-Item config_templates\experiments\si6_two_stage_nominal.yaml configs\experiments\si6_run.local.yaml
}
if (!(Test-Path configs\machines\si6_work.local.yaml)) {
    Copy-Item config_templates\machines\si6_instrument_settings.example.yaml configs\machines\si6_work.local.yaml
}
```

Edit the copies before using hardware:

- Replace `workflow.experiment_id: SET_UNIQUE_EXPERIMENT_ID` with the identity of a **newly prepared physical reaction**. Keep that identity with the same reaction after an interruption.
- In machine YAML, set `chemyx.serial_port` and `nmr.host`; the template leaves both unset. Verify ports, baud, RPC port and timeouts on that laptop.
- Check both syringe diameters, capacities, actual initial retained volumes, rates and the Channel 2 delivery path. Template values are starting values, not measured calibration.
- Review Arduino calibration against the actual rig. The current local file contains COM3/demo settings; its presence is not proof of physical readiness.

On a prepared offline instrument laptop, use `.venv\Scripts\python.exe` in place of `python` in the commands below. Use its established working interpreter if different; dependency setup is in [OFFLINE_DEPLOYMENT.md](OFFLINE_DEPLOYMENT.md). Ordinary YAML edits do not require reinstalling dependencies or uploading firmware.

## 3. Validate and simulate without hardware

Define these arguments once in the current PowerShell session; reuse them for diagnostics and the experiment:

```powershell
$si6ConfigArgs = @(
    '--workflow-config', 'configs\experiments\si6_run.local.yaml',
    '--machine-config', 'configs\machines\si6_work.local.yaml',
    '--arduino-config', 'arduino\configs\arduino.local.yaml'
)

# Validate YOUR edited recipe; no instrument connections.
python -B scripts\02_si6_experiment.py @si6ConfigArgs

# Fast complete mock using the shipped simulation recipe.
python -B scripts\02_si6_experiment.py --mock --workflow-config config_templates\experiments\si6_two_stage_fast_sim.yaml

# Spectrum-to-completion validation of shipped criteria/model.
python -B scripts\validate_si6_synthetic_analysis.py

# Package/copy/regression check after updating or transferring the repository.
python -B scripts\validate_si6_offline.py --copy-test --run-tests
```

Stop and review failures before proceeding. The command with **neither `--mock` nor `--live` only validates configuration**. Repeat it after changing your recipe. Recreate `$si6ConfigArgs` if you open a new terminal or choose different files.

The fast simulation injects trend metrics on a virtual clock; it does not prove physical delivery or real spectral quality. The synthetic validator separately exercises processed-spectrum measurement and completion. Neither automatically validates arbitrary edits to your run recipe. Full two-stage mock sessions reject nominal YAML without a `simulation` block; use the fast simulation recipe.

## 4. Check the actual instruments, then start

On the commissioned instrument laptop only, inspect the physical HOME reference, syringes and tubing, then run these attended commands with its prepared offline interpreter:

```powershell
# Read controller status; explicitly confirm the inspected HOME reference.
.venv\Scripts\python.exe -B arduino\scripts\needle_control.py status --live --config arduino\configs\arduino.local.yaml
.venv\Scripts\python.exe -B arduino\scripts\needle_control.py confirm-home --live --config arduino\configs\arduino.local.yaml

# Validate the actual per-rig files again, without opening hardware.
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py @si6ConfigArgs

# Supervised readiness diagnostic: needle checks, small pump check, NMR/processing.
.venv\Scripts\python.exe -B scripts\01_three_instrument_system_test.py --live --all @si6ConfigArgs

# Start the reviewed two-stage experiment; answer its readiness prompts.
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --live @si6ConfigArgs
```

The diagnostic's pump check uses `three_instrument.test_withdraw_ml` / `test_infuse_ml` (0.5 mL each) on the default channel. It does **not** verify Channel 2 delivery or execute the full sampling cycle. Verify both drives' addressing, STOP acknowledgements, calibration and fluid paths separately during commissioning; follow [LIVE_COMMISSIONING_CHECKLIST.md](LIVE_COMMISSIONING_CHECKLIST.md) and [CHEMYX_CHANNEL_WORKFLOW.md](CHEMYX_CHANNEL_WORKFLOW.md).

For narrower diagnostics, replace `--all` with `--needle-only`, `--pump-only` or `--nmr-only`. Needle/pump selections initialize both serial services; NMR-only opens neither serial port. The HOME equivalent uses the simulation recipe:

```powershell
python -B scripts\01_three_instrument_system_test.py --mock --all --workflow-config config_templates\experiments\si6_two_stage_fast_sim.yaml
```

### What the experiment does

1. Confirm Stage 1 readiness and raise the needle to UP.
2. Repeat the Channel 1 cycle: withdraw 8 mL UP → needle DOWN → withdraw 5 mL → settle 10 s → acquire/process NMR → needle UP → infuse 13 mL UP → withdraw 5 mL → infuse 5 mL. Each pump move requires confirmed STOP; cleanup finishes before stage advancement.
3. Stage 1 samples every **120 min**, up to **20 observations**: three consecutive moving areas <=2.5% of the first area complete it. Secondary runtime ceiling: 48 h.
4. After Stage 1 completion and cleanup, lower the needle, infuse **1.8 mL at 1 mL/min on Channel 2 once**, then raise. Durable dose confirmation admits Stage 2.
5. Stage 2 enters automatically every **30 min**, up to **20 observations**: after 25% growth, four stable observations (three adjacent changes <=2%) complete it. Secondary runtime ceiling: 12 h. **One start confirmation; no Stage 2 prompt.**
6. Save the final reports and close instruments. Reaching a stage's time limit without completion stops as inconclusive; it does not permit an operator override to dose or bypass chemistry prerequisites.

## 5. Parameters you can adjust

**Run YAML** below means `configs/experiments/si6_run.local.yaml`. List indices are zero-based. Volumes are mL; this two-stage profile requires rates in mL/min on both channels.

| Change | File / exact YAML keys |
|---|---|
| Reaction identity; results directory | Run YAML: `workflow.experiment_id`; `output.run_root_dir`. |
| Stage 1 cadence / runtime ceiling | `workflow.initial_stage.interval_minutes` / `max_hours` (120 min / 48 h). |
| Stage 2 cadence / runtime ceiling | `workflow.first_addition_stage.interval_minutes` / `max_hours` (30 min / 12 h). |
| Iteration limits and completion | Each stage's `completion.max_iterations`; Stage 1 `near_zero_fraction`, `consecutive_iterations`; Stage 2 `minimum_growth`, `relative_change_threshold`, `consecutive_iterations`. |
| Peak identity | Run YAML `analysis.peak_finding`: search, shifts, continuity, prominence, width and separation. |
| Moving area | `analysis.peak_area.method: trapezoid`; production peak-specific bounds. |
| Review warnings only | `qc_reporting`; `affect_workflow` must be false. No noise/SNR/uncertainty/phase QC gate. |

Complete defaults and commands: [Si6 configuration guide](SI6_CONFIG_USER_GUIDE.md). Missing tracked peaks record zero; failed processing/corrupt evidence and instrument failures stop without dosing. `final_qc/` saves warnings and position/area/SNR/width plots after the run. Old statistical/QC profiles require migration before live use.

## 6. Results and inspection

Nominal live output: `results/runs/si6_two_stage/<run-folder>/`, unless you change `output.run_root_dir`. Mocks use the selected recipe's `_mock` sibling root. The CLI prints the exact results folder.

- `raw_nmr/`, `processed_nmr/`: source JCAMP data and per-acquisition processing.
- `stages/stage_1/`, `stages/stage_2/`: CSVs, plots, summaries and completion evidence.
- `transition/channel2_addition.json`: dose receipt; `final/`: combined report and dose-marker plots.
- `operation_journal.jsonl`, `run_state.json`: event history and per-channel state estimates; config snapshots record run settings.
- `final_nmr_summary/`: supplemental NMR report, generated after instrument services close.

To regenerate the supplemental report or inspect an interrupted run offline, replace the example folder with the actual path:

```powershell
$si6RunFolder = 'results\runs\si6_two_stage\REPLACE_WITH_ACTUAL_RUN_FOLDER'
python -B scripts\nmr\summarize_run.py $si6RunFolder
python -B scripts\02_si6_automated_nmr.py --inspect-run $si6RunFolder --rebuild-state
```

The older script above is used **only for journal inspection** here. `--rebuild-state` reconstructs a snapshot; it does not resume hardware. There is no automatic partial-run or Stage-2-only resume. Preserve `runtime/si6_doses/<experiment_id>.json`, run history and the physical rig's needle state. Existing dose reservations block automatic restart; do not delete them or change the reaction ID to retry an uncertain dose.

NMR timing comes from JCAMP **LONG DATE**, not filename schedule labels or modification times. Unavailable acquisition metadata is reported and disables time plots; it does not change area-only endpoints. Saved figure titles include the dataset identity in PNG/SVG/PDF; the workflow passes its run identity into processing. For separate offline dataset analysis, review `statistics.dataset_display_name` and `target_peak.dataset_display_name` in processing YAML.

More detail: [config guide](SI6_CONFIG_USER_GUIDE.md), [script guide](SI6_SCRIPT_USER_GUIDE.md), [operator guide](SI6_TWO_STAGE_OPERATOR_GUIDE.md), [channel guide](CHEMYX_CHANNEL_WORKFLOW.md).

See [current area-only implementation](SI6_AREA_ONLY_IMPLEMENTATION.md) for control rules, QC separation, termination conditions and validation.
