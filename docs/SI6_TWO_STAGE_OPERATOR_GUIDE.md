# Si6 two-stage operator guide

The current controller uses moving trapezoidal peak area only. QC warnings are saved for review after the run; they do not require operator intervention.

Before starting, select your reviewed machine and Arduino YAMLs. For this rig use Chemyx COM4 and Arduino COM3, the instrument's NMR host, calibrated UP/DOWN positions and a confirmed HOME reference. Check loaded volumes and tubing for both syringes. Copy the current nominal template to your local workflow YAML and set a unique physical `workflow.experiment_id`. Keep fixed NMR gain. Existing instrument readiness, capacity, motion, STOP and dose replay checks remain active.

On the connected WORK laptop, from the repository root:

```powershell
.venv\Scripts\python.exe -B arduino\scripts\needle_control.py confirm-home --live --config arduino\configs\arduino.local.yaml
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --workflow-config configs\experiments\si6_run.local.yaml --machine-config configs\machines\00_machine.local.yaml --arduino-config arduino\configs\arduino.local.yaml
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_run.local.yaml --machine-config configs\machines\00_machine.local.yaml --arduino-config arduino\configs\arduino.local.yaml
```

The middle command validates only. HOME confirmation is setup/calibration, not a repeated chemistry checkpoint. The live command asks **one start confirmation**; the sampling cycles, one Channel 2 addition and Stage 2 entry are then automatic.

Each Channel 1 cycle: withdraw 8 mL UP, lower, withdraw 5 mL, settle 300 s, acquire/process NMR, return 13 mL DOWN, raise, withdraw/infuse 5 mL for cleanup. Completion never skips cleanup.

Stage 1 acquires every 120 min, up to 20 observations. Three consecutive areas <=2.5% of its first area complete the stage. Then the needle lowers, Channel 2 infuses 1.8 mL at 1 mL/min once, and the needle raises. Durable dose confirmation admits Stage 2.

Stage 2 acquires every 30 min, up to 20 observations. After >=25% growth relative to its starting area, four stable observations (three adjacent changes <=2%) complete the experiment. Missing tracked peaks record zero and a review warning; the search retains the last detected identity and does not substitute a known neighboring resonance.

Iteration limits stop with `STAGE_1_MAX_ITERATIONS_REACHED` or `STAGE_2_MAX_ITERATIONS_REACHED`. Secondary operational ceilings are 48/12 h. Hardware/communication failures, failed processing or corrupted evidence, uncertain physical state, emergency stop, journal failure and unsafe dose state still stop the run. An analytical QC flag cannot produce `ANALYSIS_INCONCLUSIVE` in this profile.

The result folder contains raw spectra, processing evidence, time series, stage evidence, hardware journal, dose receipt and `final_qc/`. That directory contains review CSV/JSON, warnings in `qc_report.md`, and position/area/SNR/width plots with dataset titles and JCAMP LONG DATE timing. Report errors never change the chemistry outcome. Inspect the journal and physical state after any operational failure; there is no automatic replay/resume of a partially dosed reaction.

See the [configuration guide](SI6_CONFIG_USER_GUIDE.md) for adjustable parameters and the [implementation report](SI6_AREA_ONLY_IMPLEMENTATION.md) for validation evidence.
