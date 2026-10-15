# Si6 final real-run checklist — October 14, 2026

HOME verification is software-only. Perform live commands below only on the
instrument laptop with inspected hardware. Run PowerShell from the repository
root. `python` must be the verified interpreter; `.venv\Scripts\python.exe`
can replace it everywhere without activation.

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
| Stage 1 fallback duration | `workflow.initial_stage.max_hours` | 48 hours |
| Stage 2 cadence | `workflow.first_addition_stage.interval_minutes` | 30 minutes |
| Stage 2 fallback duration | `workflow.first_addition_stage.max_hours` | 4 hours |
| Normal observation caps | Each stage's `completion.max_iterations` | 20 |
| Solvent windows | `solvent_monitor.windows_ppm` | [1.90, 2.25], [6.80, 7.10] |
| Solvent expected centers | `solvent_monitor.expected_centers_ppm` | 2.08, 6.99 |
| Maximum shifts | `solvent_monitor.maximum_shift_ppm` | 0.15, 0.10 ppm |
| Maximum pair-shift disagreement | `solvent_monitor.maximum_pair_shift_difference_ppm` | 0.04 ppm |
| Solvent failures before fallback | `solvent_monitor.consecutive_missing` | 3 |

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
python -c "from pathlib import Path; from tempfile import TemporaryFile; p=Path('results/runs/si6_two_stage'); p.mkdir(parents=True,exist_ok=True); f=TemporaryFile(dir=p); f.write(b'write check'); f.close(); print('Output writable')"
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

   Confirm the printed durations/cadences and scheduled-count bounds. Explicit
   `--validate-config` cannot be combined with `--live`; no instruments open.

## Establish or inspect the Arduino reference

HOME=0 is **inside the flask**, UP=1 is out. DOWN is HOME=0. With no encoder or
HOME switch, saved position and matching motor replies are software estimates.
They do not independently prove physical needle height.

If establishing HOME, physically place the stationary needle at the desired
sampling depth first, then answer `y` to this command. It does not move the motor:

```powershell
python arduino\scripts\needle_control.py set-home --live --config arduino\configs\arduino_real_COM3.yaml
```

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
status is `operator_status.json` in the unique run directory. Detailed evidence:

```text
results/runs/si6_two_stage/<timestamp>_si6_live[/unique suffix]/
  metadata/run_metadata.json        ID, commit, UTC start/end, loaded-config hashes
  config_snapshot.json             actual recipe used
  instrument_settings_snapshot.json
  arduino_config_snapshot.json
  operator_status.json             latest concise operational status
  operation_journal.jsonl           durable operations, STOPs, errors, decisions
  run_state.json                   reconstructible journal state
  time_series.csv                  target/solvent/fallback/acquisition evidence
  raw_nmr/                         original retrieved acquisitions
  processed_nmr/                   analysis or explicit missing-sample evidence
  stages/stage_1/                  stage tables, endpoint/solvent evidence
  transition/channel2_addition.json
  stages/stage_2/
  final_qc/                        QC report, CSV/JSON and dataset-titled plots
  final_nmr_summary/               supplemental summary after services close
```

The actual run folder is printed by the CLI. Before it finishes, choose the
newest matching live directory and verify `metadata/run_metadata.json` has the
intended experiment ID. NMR plots use JCAMP LONG DATE only; operational elapsed
time is separately labeled and never substituted for acquisition metadata.

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
| Interrupted needle motion/unknown state | No automatic recovery. Inspect and physically restore a safe reference, then explicitly use `set-home`. |
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

Check the final console classification, `metadata/run_metadata.json`, stage-end
reasons, `transition/channel2_addition.json`, `final_qc/qc_report.md` and the
journal. Preserve raw/processed data and all physical-state/dose records. Archive
the actual loaded YAMLs with the run. Software validation does not establish real
dose accuracy, immersion depth, sample delivery, spectral thresholds or yield.
