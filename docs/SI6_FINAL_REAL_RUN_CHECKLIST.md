> Historical/legacy runner reference: use [SI6_START_HERE](SI6_START_HERE.md) for the current automatic three-instrument experiment.

> Current operating authority: [SI6_START_HERE](SI6_START_HERE.md).
> The real Si6 recipe now explicitly selects ACME; see [phase selection and
> post-session comparison](SI6_NMR_PHASE_METHODS.md). Before replacing software,
> [back up rig state](RIG_STATE_BACKUP.md). The shared portable inventory is
> `offline/release_manifest.yaml`; `prepare_si6_release.py --working-tree
> --with-offline-payload` builds an uncommitted review snapshot. Older dated
> engineering evidence below retains its original validation scope.

# Si6 final real-run checklist — October 14, 2026

HOME verification is software-only. Perform live commands below only on the
instrument laptop with inspected hardware. Run PowerShell from the repository
root. `python` must be the verified interpreter; `.venv\Scripts\python.exe`
can replace it everywhere without activation.

Software evidence and remaining physical limits are recorded in
[SI6_PREDEPLOYMENT_VALIDATION.md](SI6_PREDEPLOYMENT_VALIDATION.md).

## Before transferring

- Run `git status --short` (must be clean) and `git rev-parse HEAD`; retain the
  full tested commit from the final delivery report.
- Export that committed version on HOME or WORK with
  `python scripts\prepare_si6_release.py --with-offline-payload`.
- Copy the ZIP, its `.sha256` and `_manifest.json` from `dist\si6_release\` to
  the external drive. Use a new destination directory when extracting.
- Verify the ZIP with `Get-FileHash -Algorithm SHA256 <ZIP>` against `.sha256`.
  The ZIP contains `RELEASE_VERSION.json`, committed source/config/tests/docs,
  the pinned local wheels and Python installer. Git access on WORK is optional.
- Separately preserve rig-owned `runs\arduino\needle_state.json`, relevant
  `runtime\si6_doses\`, and owning experiment journals when transferring the
  same physical rig/reaction. Never transplant another rig's HOME reference.
- Retain installed USB drivers, commissioned Arduino firmware 1.2.1 and the
  working NMR vendor/RPC software. The source ZIP does not install these.

## On the instrument laptop — no hardware connections yet

1. Confirm the source version and dependencies:

```powershell
python --version
python scripts\verify_si6_environment.py --expect-commit FULL_COMMIT_FROM_DELIVERY_REPORT
```

   Use the full hash in the report or `RELEASE_VERSION.json`, not the placeholder.
   Export verification checks all source/payload hashes. Intentional operator
   YAML edits are reported separately; other modified files fail verification.
   For a Git checkout also use `git rev-parse HEAD` and `git status --short`.
2. If no working environment exists, install the copied Python 3.11 x64
   installer, then run the following offline installation. Do not rebuild an
   already verified working environment unnecessarily.

```powershell
python offline\verify_bundle.py
powershell -NoProfile -ExecutionPolicy Bypass -File offline\install_offline.ps1
```

3. Edit **one reaction file**: `configs\experiments\si6_real_two_stage.yaml`.

| Setting | Exact YAML location | Shipped value |
|---|---|---|
| New physical reaction ID | `workflow.experiment_id` | Replace the example ID |
| Stage 1 cadence | `workflow.initial_stage.interval_minutes` | 120 minutes |
| Stage 1 earliest confirmation-window start | `workflow.initial_stage.min_completion_hours` | 40 hours |
| Stage 1 maximum duration / fallback deadline | `workflow.initial_stage.max_hours` | 48 hours |
| Stage 2 cadence | `workflow.first_addition_stage.interval_minutes` | 30 minutes |
| Stage 2 earliest confirmation-window start | `workflow.first_addition_stage.min_completion_hours` | 2 hours |
| Stage 2 maximum duration / fallback deadline | `workflow.first_addition_stage.max_hours` | 6 hours |
| Normal observation caps | Omitted in the real recipe | Deadline governs |
| Solvent windows | `solvent_monitor.windows_ppm` | [1.90, 2.25], [6.80, 7.10] |
| Solvent expected centers | `solvent_monitor.expected_centers_ppm` | 2.08, 6.99 |
| Maximum shifts | `solvent_monitor.maximum_shift_ppm` | 0.15, 0.10 ppm |
| Maximum pair-shift disagreement | `solvent_monitor.maximum_pair_shift_difference_ppm` | 0.04 ppm |
| Solvent failures before fallback | `solvent_monitor.consecutive_missing` | 3 |

   Four qualifying acquisitions must start at or after each minimum. Review
   [mandatory stage durations](SI6_MINIMUM_STAGE_DURATIONS.md), including the
   remaining noisy-nondetection/temporary-plateau risks and the distinction
   between ordinary timeout (no reagent transition) and latched solvent fallback
   (existing timed transition with chemistry unverified). This timing change
   alone is not scientific acceptance or physical commissioning.

   Both solvent peaks must have compatible shifts; the strong 7.12 ppm neighbor
   cannot replace the expected 6.99 ppm signal. Review thresholds when changing
   scans/gain/solvent. Signals outside these conservative identity criteria cause
   untrusted observations and possibly timed fallback, rather than a false zero.
4. Review COM3 Arduino, COM4 primary pump/COM6 alternate, and unchanged NMR
   `169.254.30.54:5000` in the selected real machine YAML. Review 200 steps/unit
   and verified direction in `arduino_real_COM3.yaml`.
5. Inspect the actual syringes, diameters, fluid paths and initial volumes:
   Channel 1 starts with 0 mL retained; Channel 2 with 2 mL for its 1.8 mL dose.
   Verify available flask liquid, needle immersion at HOME, UP clearance, tubing,
   syringe capacity/margins and room for every transfer.
6. Check a writable output directory and adequate free disk space. Disable
   sleep/hibernation and automatic restarts for the run; keep power stable.
   Verify output permission without starting instruments:

```powershell
python -c "from pathlib import Path; from tempfile import TemporaryFile; p=Path('results/experiments'); p.mkdir(parents=True,exist_ok=True); f=TemporaryFile(dir=p); f.write(b'write check'); f.close(); print('Output writable')"
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

   Confirm the printed durations/cadences and scheduled-count bounds. Explicit
   `--validate-config` cannot be combined with `--live`; no instruments open.
   At least three scheduled observations must fit before a stage's deadline
   for the configured three-failure fallback to activate. If shortening a stage
   drastically, review its cadence too; otherwise it can stop at its limit before
   fallback activates. Do not edit source or processing/config files during a run.

## Establish or inspect the Arduino reference

HOME=0 is **inside the flask**, UP=1 is out. DOWN is HOME=0. With no encoder or
HOME switch, saved position and matching motor replies are software estimates.
They do not independently prove physical needle height.

Use the same HOME command for ordinary homing and position recovery:

```powershell
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
```

With a valid saved reference it asks once and moves to absolute zero. If tracking
is UNKNOWN, it checks communication/idle state, then asks you to physically place
and verify the stationary needle at the correct sampling depth. Answer its single
`y` to establish that position as zero **without movement**. Both paths verify
saved-record readback and controller readiness before clearing position uncertainty.
`set-home` remains available to explicitly redefine zero even with a valid record.

If the saved reference is still valid on the same unchanged rig, keep it. Inspect
status and exercise the absolute movements with one `y` per requested movement:

```powershell
python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino_real_COM3.yaml
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino_real_COM3.yaml
python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino_real_COM3.yaml
```

Repeated UP/DOWN commands remain at 1/0. Re-establish HOME after uncertain or
interrupted motion, manual untracked repositioning, changed mounting/direction,
or a power event that could have moved the mechanism. Never mark an arbitrary
position as HOME merely to bypass a failure.
The [HOME operating guide](../arduino/docs/HOME_RECOVERY.md) explains both paths.
Each attempt prints a unique Arduino recovery journal, preserving prior faults
and recording operator confirmation or completed movement. Old experiment journals
remain unchanged. A `needle_state.json.home_pending.json` marker blocks startup
after interrupted/failed verification; rerun HOME after inspection, do not delete
the marker. HOME does not clear pump faults, dose reservations or unrelated
controller faults. An unresolved STOP failure needs a verified standalone STOP
before HOME can proceed. Status and STOP still have no prompt; the main experiment
still has its single startup `y` and never silently resets unknown position.

## Verify communication and start

First, on the instrument laptop, make the one-confirmation communication check:

```powershell
python scripts\01_three_instrument_system_test.py --live --communications-only --workflow-config configs\experiments\si6_real_hardware_test.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

This checks Arduino/pump/NMR responses. It sends initialization STOP/configuration
commands but no pump START, needle movement or NMR acquisition. Real delivery,
STOP behavior and spectral quality need the supervised channel/NMR checks in
`REAL_INSTRUMENT_RUN_COMMANDS.md` before the reaction. Use COM6 there if required.

Main experiment — COM4:

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Alternate — COM6:

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Choose one command. Check the summary, then answer the single `y`. The run uses
the saved reference, returns HOME, raises UP and performs both stages, the one
protected dose and cleanup with no additional prompts. No acknowledgment flags.

## During the experiment

The console reports experiment/stage/observation, operational elapsed time,
solvent result, target area or UNAVAILABLE, endpoint and fallback status. Latest
status is `logs/live_status.json` in the unique run directory. Detailed evidence:

```text
results/experiments/20261015_exp1/
  metadata/experiment_info.json     physical ID, suffix, full path, local/UTC times
  metadata/configuration_snapshot.yaml
  metadata/instrument_settings_snapshot.json
  metadata/arduino_config_snapshot.json
  metadata/software_version.json  start commit and source hashes
  logs/live_status.json            latest concise operational status
  logs/experiment_journal.jsonl    durable operations, STOPs, errors, decisions
  logs/run_state.json              reconstructible journal state
  logs/experiment.log
  step_1/nmr_raw/                  original retrieved acquisitions
  step_1/nmr_processed/            analysis or missing-sample evidence
  step_1/analysis/                 time series, endpoint/solvent/QC evidence
  step_1/figures/                  stage plots
  step_1/logs/operations.jsonl
  channel_2_dose/dose_record.json
  step_2/                         same subdirectories as step_1
  final_qc/summary.json            final operational status and end reasons
  final_qc/full_time_series.csv    cross-stage target/solvent/timing evidence
  final_qc/figures/                cross-stage and QC plots
  final_qc/nmr_summary/            supplemental summary after services close
```

The actual run folder is printed by the CLI. Before it finishes, choose the
newest matching local start in `metadata/experiment_info.json` and verify it has the
intended experiment ID. NMR plots use JCAMP LONG DATE only; operational elapsed
time is separately labeled and never substituted for acquisition metadata.
Metadata retains the shared/local processing YAML at startup; per-spectrum
processing JSON records the actual applied numerical parameters. The Git commit
is captured at startup, not replaced if a checkout changes while the process runs.

Set `workflow.experiment_id` in the existing experiment YAML for a new physical
reaction. `output.run_root_dir` defaults to `results/experiments` and is configurable.
Repeated executions allocate `_1`, `_2`, etc. without overwriting; both still refer
to the original physical ID and its canonical dose reservation. A repeated reaction
is refused before instruments open. Folder date is captured once from local time;
it is independent of JCAMP timing. See [output details](SI6_EXPERIMENT_OUTPUTS.md).

Three missing solvent observations latch fallback for that stage. It keeps
sampling to `max_hours` from that stage's original start, beyond the normal
count cap if needed. It stays latched if solvent returns. Existing cycles finish
safe cleanup; none start at/after the deadline. Stage 1 fallback then admits the
single protected dose. Stage 2 has its own fresh timer/counter. Final output
distinguishes verified endpoints from timed fallback with unverified chemistry.
With healthy solvent, Stage 1 requires four low target observations; Stage 2
requires growth plus four stable observations. A healthy stage exhausting a
count/time limit without an endpoint stops; it does not authorize a dose.

## If something fails

| Situation | Required action |
|---|---|
| Missing solvent | Review warning/raw spectra and flask/liquid path. Automation follows configured fallback; it cannot diagnose evaporation uniquely. Do not treat unavailable areas as zero. |
| NMR transport timeout/error | Run stops and performs return/cleanup only when states are proven. Inspect NMR/vendor status and the named acquisition; do not start another scan blindly. |
| Pump uncertainty or failed STOP | Do not command needle/pump recovery. Inspect/disconnect driver power as appropriate, reconcile actual retained volumes and review the journal. |
| Interrupted needle motion/unknown state | No automatic movement. Inspect and physically restore the sampling position, then run `home` and answer its single manual-reference confirmation. |
| Interrupted Python/power loss | Preserve the entire run directory and dose/needle records. Inspect the physical rig before any command. A saved estimate cannot rule out missed steps or drift. |
| Existing dose reservation/receipt | Do not delete it, change the ID for the same reaction or retry the dose. No automatic partial-run resume exists. Reconcile dose delivery from journal/receipt and physical contents. |
| New, separately prepared reaction | After reconciliation, use a new experiment ID and reviewed initial volumes. Retain previous records. |

Immediate software needle STOP (no confirmation, works without a valid reference):

```powershell
python arduino\scripts\needle_control.py stop --live --config arduino\configs\arduino_real_COM3.yaml
```

For emergency physical motion, use the rig's physical power disconnect. Ctrl+C
requests software STOP but cannot guarantee delivery if a cable/device has failed.
Offline journal inspection does not resume execution:

```powershell
python scripts\02_si6_automated_nmr.py --inspect-run ACTUAL_RUN_DIRECTORY --rebuild-state
```

## After completion

Check the final console classification, `metadata/experiment_info.json`, stage-end
reasons, `channel_2_dose/dose_record.json`, `final_qc/qc_report.md` and the
journal. Preserve raw/processed data and all physical-state/dose records. Archive
the actual loaded YAMLs with the run. Software validation does not establish real
dose accuracy, immersion depth, sample delivery, spectral thresholds or yield.
