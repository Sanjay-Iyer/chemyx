# Si6 script user guide

Current controls: [area-only configuration guide](SI6_CONFIG_USER_GUIDE.md).
One start confirmation launches both stages and the single dose; QC is retrospective.

Use [start here](SI6_START_HERE.md) first. All examples run from the repository
root. On HOME use `conda activate ai` then `python`; on prepared offline WORK2
replace `python` with `.venv\Scripts\python.exe`. The WORK live examples below
are operator instructions; they were not run against hardware on HOME.

## Quick start

```powershell
python -B scripts\validate_si6_synthetic_analysis.py
python -B scripts\validate_si6_offline.py --copy-test --run-tests
python -B scripts\02_si6_experiment.py --mock --workflow-config config_templates\experiments\si6_two_stage_fast_sim.yaml
```

Use the first for historical spectrum counterexamples plus current area-only workflow proof, the second after pull/copy, and
the third for a quick controller-fixture experiment.

## Main experiment runner

`scripts/02_si6_experiment.py` runs the shared pump/needle/NMR workflow in
`chemyx_lab/workflows/three_instrument_si6.py`. Channel1 performs the repeated
sampling cycle. Valid Stage1 completion permits one Ch2 addition; its durable
confirmation admits Stage2. Completion always finishes the sample cleanup.

| Argument | Use |
|---|---|
| `--workflow-config PATH` | Experiment YAML; default is the older `configs/experiments/02_si6_automated_nmr.yaml`. Select the two-stage YAML explicitly. |
| `--machine-config PATH` | Chemyx/NMR endpoint YAML; default `configs/machines/00_machine.local.yaml`. |
| `--arduino-config PATH` | Needle/controller YAML; default `arduino/configs/arduino.local.yaml`. |
| no `--mock`/`--live` | Validate configuration only; no transports opened. |
| `--mock` | Fake instruments. Use the explicit fast simulation template for directional two-stage completion. |
| `--live` | Physical WORK execution with one start confirmation. Simulation YAML is rejected live. |
| `--mock-cycles-per-stage N` | Legacy non-virtual mock limit, default4. Does not replace the virtual fixture's nominal stage schedule. |
| `--acknowledge-review RUN_ID` | Only after reconciling a previous flagged live run. Does not clear the dose ledger or enable automatic resume. |

Validate a reviewed nominal experiment:

```powershell
python -B scripts\02_si6_experiment.py --workflow-config configs\experiments\si6_run.local.yaml --machine-config configs\machines\si6_work.local.yaml --arduino-config arduino\configs\arduino.local.yaml
```

On commissioned WORK hardware only, the corresponding experiment command is:

```powershell
python -B scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_run.local.yaml --machine-config configs\machines\si6_work.local.yaml --arduino-config arduino\configs\arduino.local.yaml
```

Nominal YAML selected with `--mock` alone is not the synthetic trend proof: its
static copied spectrum/timestamp cannot establish directional completion.
Use `si6_two_stage_fast_sim.yaml` or the new synthetic command for that task.

Outputs: `output.run_root_dir` (a `_mock` sibling for mocks), one identified run
with raw data, config snapshots, processing, `operation_journal.jsonl`,
`run_state.json`, separate stage CSVs/plots/summary/evidence, transition receipt,
and combined `final` reports. `scripts/nmr/summarize_run.py RUN_FOLDER` regenerates
the supplemental final NMR summary after the instrument session has closed.

## Offline package validation

`scripts/validate_si6_offline.py` checks required portable resources, installed
dependency versions, and all four two-stage templates without live hardware.
`--simulate` runs the fast shared mock workflow; `--copy-test` copies the source
package into a new directory and executes it there; `--run-tests` runs the
listed workflow, channel, monitoring and journal regression modules.

```powershell
python -B scripts\validate_si6_offline.py
python -B scripts\validate_si6_offline.py --simulate --copy-test --run-tests
```

Run after changing code/configs on HOME, after pulling on WORK1 and after copying
to WORK2. Compact copied-root evidence is
`test_tmp_offline_validation/copied_directory_report.json`; mock runs use
`test_tmp_offline_validation/runs_mock`. Offline dependency installation and
preserving physical experiment history are covered in
[OFFLINE_DEPLOYMENT.md](OFFLINE_DEPLOYMENT.md).

## Synthetic NMR endpoint validation

`scripts/validate_si6_synthetic_analysis.py` is HOME/mock-only and has no live
option. The test model lives in `chemyx_lab/testing/si6_synthetic_analysis.py`.

```powershell
python -B scripts\validate_si6_synthetic_analysis.py
python -B scripts\validate_si6_synthetic_analysis.py --output-dir test_tmp_my_si6_validation
```

An explicit output directory must be new. Default outputs use a timestamp under
`test_tmp_si6_synthetic_validation`. Every required failure gives a nonzero exit.
It checks the shipped nominal completion template and the labeled validation
model; it does not accept/test an arbitrary edited run YAML. Validate that run
file separately with the main runner and review its endpoint criteria on WORK.

Level1 checks known numbers against unchanged completion. Level2 generates a
Gaussian absorption peak near5.8ppm, small linear baseline, seeded noise and
small ppm drift, then runs the production baseline/detector/peak-QC/table writer,
continuity tracker and existing production variable-width peak area/QC.
Level3 sends these measured spectra through the shared mock controller and dose
guard. All seven requested counterexamples, plus an isolated point below the
actual Stage1 low threshold, are checked at both Level1 and Level2.

`REPORT.md` explains every success iteration and why the preceding observation
was insufficient. `level1`/`level2` contain CSV traces and complete rolling-window
JSON; Level2 also retains generated arrays, tables and model provenance.
`workflow_evidence.json` links the full mock run/journal and dose receipt.
`figures` contains Stage1, Stage2, combined dose-marker and example-spectra plots
in PNG/SVG/PDF with a title manifest. `SUMMARY.json` records the outcome.

PASS means measured synthetic data drives the configured logic and shared mock
ordering. It does not validate vendor raw FID generation/FFT/phase optimization,
physical acquisition/delivery, real kinetics or threshold calibration. Copied
DX files are metadata carriers; their FIDs are not scaled synthetic signals.
Synthetic processed traces are explicitly labeled, including their phase scope.
This validator explicitly selects the shared `configs/nmr/analysis.yaml`;
it does not test optional per-rig `analysis.local.yaml` processing overrides.
Review/test those overrides separately against actual WORK spectra.
Stage2's test-only maximum9h admits16 observations at30min; nominal maximum6h
is unchanged. Fast wall time comes from the virtual clock, not shorter YAML
cadence. The retained real fixture still fails nominal uncertainty/noise QC.

## Chemyx channels

| File | Purpose |
|---|---|
| `chemyx_lab/instruments/chemyx.py` | Existing serial driver and addressed pump commands. |
| `chemyx_lab/workflows/pump_channels.py` | Per-channel settings, validation and selection; restores default channel after transfers. |
| `chemyx_lab/workflows/si6_automated_nmr.py` | Shared timed-transfer state/STOP safeguards and monitoring support. |
| `chemyx_lab/runtime_state.py` | Replays the journal into separate channel volume/motion estimates. |
| `chemyx_lab/workflows/dose_guard.py` | Durable physical-experiment reservation and at-most-once automatic dose dispatch. |
| `scripts/diagnostics/02_verify_chemyx_movement.py` | Supervised individual-channel diagnostic; HOME must select `--mock`. |

Both channels are accessible through the same serial pump. The current two-stage
workflow already uses Ch1 for sampling and Ch2 once at the analysis-approved
boundary; dose volume/rate are YAML edits. No script rewrite is needed for that
configured use. Arbitrary mid-cycle/manual dosing or resuming just Stage2 is
outside this validated workflow. See [channel guide](CHEMYX_CHANNEL_WORKFLOW.md).

## Arduino / needle

Firmware: `arduino/firmware/needle_controller/needle_controller.ino`.
Host: `arduino/python/controller.py`, `protocol.py`, `transport.py`,
`needle_state.py`; fake transport: `arduino/mock/fake_arduino.py`.
Configuration: `arduino/configs/arduino.local.yaml`, based on
`arduino/configs/arduino.example.yaml`. Firmware expectations and motion limits
must match the actual controller; uploading firmware is separate WORK setup.

HOME examples:

```powershell
python -B arduino\scripts\needle_control.py status --mock
python -B arduino\scripts\needle_control.py up --mock
```

Supervised WORK commands, after physical inspection/calibration:

```powershell
python -B arduino\scripts\needle_control.py status --live --config arduino\configs\arduino.local.yaml
python -B arduino\scripts\needle_control.py confirm-home --live --config arduino\configs\arduino.local.yaml
python -B arduino\scripts\needle_control.py up --live --config arduino\configs\arduino.local.yaml
python -B arduino\scripts\needle_control.py down --live --config arduino\configs\arduino.local.yaml
```

HOME is an operator-confirmed reference, not automatic switch homing. State at
`needle.state_path` (normally `runs/arduino/needle_state.json`) estimates commanded
position and certainty. It is not an independent physical-height measurement.
Two-stage live startup rejects an unknown/demo-assumed HOME; do not import
another rig's state or manually edit it to permit movement.

## NMR: acquisition to completion

| File | Role |
|---|---|
| `chemyx_lab/instruments/nmr.py` | RPC settings/acquisition/retrieval interface. |
| `scripts/diagnostics/03_check_nmr_connection.py` | WORK RPC readiness check. |
| `scripts/diagnostics/04_run_nmr_1d_acquisition.py` | Supervised standalone acquisition; `--dry-run --mock-settings` prints settings without acquisition. |
| `scripts/nmr/process_fid.py` | Existing raw decoding/FFT/phase/baseline/regional detection/QC/tables/evidence. |
| `chemyx_lab/analysis/nmr.py` | Shared decoding, spectra, phase/baseline and peak/integration functions. |
| `scripts/nmr/inspect_processing.py` | Offline inspection of existing DX series. |
| `scripts/nmr/validation_phase_gui.py` | Offline phase review tool for reviewed spectra. |
| `chemyx_lab/analysis/stage_measurement.py` | Retained trace/provenance/QC, continuous peak identity and moving peak area. |
| `chemyx_lab/analysis/stage_completion.py` | Decreasing/increasing progress, range/slope/time and sustained-window decision. |
| `chemyx_lab/analysis/si6_stage_reports.py` | Separate and combined stage reports with dose marker. |
| `chemyx_lab/analysis/final_nmr_summary.py`, `scripts/nmr/summarize_run.py` | Supplemental final report after transports close. |

Normal path: acquisition → production processing → tracked peak/QC → fixed
integral/QC → stage-specific completion → cleanup → controller decision.
JCAMP LONG DATE governs acquisition timing; filenames and file modification
time are not substitutes. Processing settings are in `configs/nmr/analysis.yaml`;
acquisition and endpoint rules are in experiment YAML.

Offline processing of one existing spectrum through the same diagnostic adapter:

```powershell
python -B scripts\01_three_instrument_system_test.py --mock --process-only --input-dx chemyx_lab\testing\fixtures\tracked_resonance_phsi4_20260810.dx --workflow-config config_templates\experiments\si6_two_stage_nominal.yaml
```

This tests processing only, not a successful two-stage reaction or commissioned
nominal tracked-area QC. `scripts/nmr/README.md` covers additional processing tools.

## Tests and documentation

`tests/test_si6_final_validator.py`: independently authored numeric false-stop,
spectral sensitivity/drift/reproducibility, production QC rejection, full shared
workflow ordering, dataset-title convention and guide command/YAML checks.
`tests/test_si6_synthetic_analysis.py`: all spectral trajectories/counterexamples,
synthetic provenance and refusal to overwrite an existing validation directory.
`tests/test_si6_profile.py`: rejects schedules with too few slots for every
required confirmation window before any instrument transport opens.
`tests/test_si6_two_stage.py`: full controller fixture, exact sampling, dose,
reports/replay and templates. `tests/test_si6_validator_gates.py`: endpoint/QC
and fail-closed safety gates. `tests/test_si6_pump_channels.py`: channel routing
and independent state. `tests/test_three_instrument_si6.py`,
`test_si6_monitoring.py`, `test_runtime_journal.py`: shared orchestration,
scheduling/failure and durable journal behavior.

```powershell
python -B -m pytest tests\test_si6_synthetic_analysis.py tests\test_si6_final_validator.py -q -p no:cacheprovider --basetemp test_tmp_si6_final_tests
```

Read [operator guide](SI6_TWO_STAGE_OPERATOR_GUIDE.md),
[config guide](SI6_CONFIG_USER_GUIDE.md),
[detailed schema](SI6_TWO_STAGE_CONFIGURATION.md),
[offline deployment](OFFLINE_DEPLOYMENT.md),
[offline requirements](OFFLINE_REQUIREMENTS.md),
[handoff](SI6_TWO_STAGE_HANDOFF.md),
[final HOME report](SI6_FINAL_HOME_VALIDATION_REPORT.md),
[existing workflow validator](SI6_WORKFLOW_VALIDATOR.md),
[channel guide](CHEMYX_CHANNEL_WORKFLOW.md).

## Common task → command/file

| I want to… | Command/file |
|---|---|
| Run fast simulated experiment | `python -B scripts\02_si6_experiment.py --mock --workflow-config config_templates\experiments\si6_two_stage_fast_sim.yaml` |
| Validate synthetic NMR endpoints | `python -B scripts\validate_si6_synthetic_analysis.py` |
| Check offline package after pull/copy | `python -B scripts\validate_si6_offline.py --copy-test --run-tests` |
| Validate nominal settings only | Main runner with explicit nominal/run YAML and no mode flag (example above). |
| Run real experiment | Reviewed main-runner `--live` example above, commissioned WORK only. |
| Change Stage1 timing / Ch2 dose / scans / target | Run YAML; [config table](SI6_CONFIG_USER_GUIDE.md). |
| Check stage decisions / dose | Run `stages`, `transition/channel2_addition.json`, `operation_journal.jsonl`, `final`. |
| Inspect previous run without motion | `python -B scripts\02_si6_automated_nmr.py --inspect-run RUN_FOLDER --rebuild-state` |
| Regenerate supplemental NMR summary | `python -B scripts\nmr\summarize_run.py RUN_FOLDER` |
