> [Documentation home](../00_START_HERE.md) | [Other and older experiment workflows](00_START_HERE.md)

> Other/older workflow. For the full 48-hour / 6-hour run use the [fixed-time experiment](../03_fixed_time_experiment/00_START_HERE.md). Older Stage 2 endpoint rules can finish before 6 hours.

# Si6 current operating entry point

For the new fixed 48-hour / 6-hour acquisition-only experiment, start with
[01_run_guide.md](../03_fixed_time_experiment/01_run_guide.md) and
`scripts/04_si6_fixed_time_run.py`. The legacy production runner described below
retains its analysis and chemical endpoint behavior.

This is the authoritative index for the current three-instrument experiment.
At the instruments, follow [02_legacy_run_guide.md](02_legacy_run_guide.md)
from top to bottom. Keep [07_legacy_errors_and_fixes.md](07_legacy_errors_and_fixes.md) open
for actual error messages and recovery. Superseded guides and dated audits are
preserved in [docs/99_archive](../99_archive/00_START_HERE.md); they are not startup instructions.
Recommended bench check: [standalone checkout](../04_instrument_checkout/01_bench_checkout.md),
`python scripts/03_simple_instrument_checkout.py --live` (one y; no chemistry
stages or NMR peak requirements). Production runner: `scripts/02_si6_experiment.py`. Real recipe:
`configs/experiments/si6_real_two_stage.yaml`. Use reviewed
`configs/machines/si6_real_COM4.yaml` or `si6_real_COM6.yaml` and
`arduino/configs/arduino_real_COM3.yaml`. Commands below run from the repository
root. HOME is an offline development machine: no physical instrument commands
were run here.

## Configure and preflight without instruments

Edit the real recipe: `workflow.experiment_id` uniquely identifies the physical
reaction; each stage's `interval_minutes`, `min_completion_hours`, `max_hours`, and
`completion` control sampling and endpoints. Current real defaults are Stage 1
120 min / fixed 48 h, with explicit time-based dose authorization, and Stage 2
30 min / minimum 2 h / maximum 6 h. Stage 1 chemical evidence is advisory.
See [time-driven progression](04_legacy_timing.md). Four post-threshold
observations are required for Stage 2 chemical completion; see
[current mandatory stage durations](04_legacy_timing.md). Keep a unique physical ID; a dated suffix never permits a
repeat dose. See [configuration details](03_legacy_configuration.md).

The `solvent_monitor` section requires both solvent peaks and consistent shifts;
three consecutive failures latch the original stage duration and disable early
chemical completion. See [solvent fallback](05_solvent_fallback.md).

`nmr.processing.phase_method: acme` selects the quantitative primary. Change only
that value to `stored`, `combined_objective`, `ernst_p0` or `deep_phaser`.
Comparisons default off and run after session closure when enabled. Method
changes may change areas/endpoints; read [phase methods](../05_nmr_analysis/01_phase_methods.md).
NMR scans, gain, center and width remain in this same recipe; keep auto-gain off
for comparable areas. Common standalone processing defaults remain in
`configs/nmr/analysis.yaml`; the recipe's explicit phase choice takes precedence.

```powershell
python -B scripts\verify_si6_environment.py --machine-config configs\machines\si6_real_COM4.yaml
python -B scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
python -B scripts\validate_si6_offline.py --copy-test --run-tests
```

Use the instrument laptop's offline `.venv\Scripts\python.exe` in place of
`python` there. All commands above are configuration/mock only, with no motion
or RPC/serial contact. Switching COM4 to COM6 changes only the machine file.

## HOME and starting on the instrument laptop

If a valid reference exists, `home` performs the normal absolute return to 0,
verifies completion/idle and durable saved state. UNKNOWN tracking causes no
automatic movement: manually place the stopped needle at the sampling position,
then one `y` establishes operator-confirmed HOME=0. No physical feedback is
claimed. HOME=DOWN=0 is in the flask; UP=1 is out. See
[HOME recovery](../01_arduino/02_home_recovery.md) and the
[final real-run checklist](../99_archive/SI6_FINAL_REAL_RUN_CHECKLIST.md).

The Chemyx syringes have their own software HOME (no native pump command):
`scripts/chemyx_control.py set-home` saves each operator-confirmed plunger
position without motion, the experiment's transfers update the same record,
and `home` returns the syringes afterwards. See
[Chemyx manual control](../02_chemyx/02_manual_control_and_home.md).

The following are **instrument-laptop commands only**, after supervised physical
setup and calibration:

```powershell
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

The experiment asks one startup `y`, then performs HOME, Stage 1, one protected
Channel 2 dose, Stage 2 and cleanup without more operator prompts. No additional
acknowledgment flags. A primary processing failure cannot authorize a dose;
missing solvent uses timed fallback. The dose ledger prevents automatic restart
of a reserved physical experiment. No automatic partial-run resume is available.
Do not erase uncertain records or change ID to bypass a reservation.

## Results and maintenance

Outputs remain `results/experiments/YYYYMMDD_experimentID[_n]` with `metadata`,
`logs`, `step_1`, `channel_2_dose`, `step_2` and `final_qc`.
[Output guide](06_legacy_outputs.md) describes primary arrays, stage tables,
completion evidence and final QC. [Phase methods](../05_nmr_analysis/01_phase_methods.md)
describes optional method comparisons. Acquisition time comes from JCAMP metadata,
preferentially LONG DATE, never nominal filename schedule tokens.

Before updating the rig software, [back up and verify physical state](../06_deployment_and_backup/03_rig_state_backup.md).
Keep `runs/arduino`, pending HOME markers, `runtime/si6_doses` and journals separate
from source releases. Read-only restore plans flag conflicts; no automatic ledger
rollback or copied-position trust is offered.

[Offline deployment](../06_deployment_and_backup/01_general_offline_deployment.md) and the
[deployment manifest](../06_deployment_and_backup/04_release_manifest.md) cover wheel installation.
`offline/release_manifest.yaml` is the shared release inventory. Build a review
snapshot without committing with:

```powershell
python scripts\prepare_si6_release.py --working-tree --with-offline-payload
```

Transfer its ZIP, checksum and manifest; extract into a new directory, verify
integrity/environment and preserve rig state. DEEP needs the separately
checksummed Node/TFJS/WASM/model payload included in an offline build; adapter
source alone is insufficient. No Git or internet is required on the instrument
laptop after extraction.

Git carries the source. Ignored wheels, installers and model/runtime binaries
must also reach WORK: transfer the complete offline ZIP from HOME, or copy its
verified `offline` payload alongside the Git checkout before rebuilding there.
Pushing source alone does not transfer DEEP weights or Node.

## Detailed and historical references

- [Current simple operating guide](02_legacy_run_guide.md)
- [Current errors and fixes](07_legacy_errors_and_fixes.md)
- [Historical command reference](../99_archive/REAL_INSTRUMENT_RUN_COMMANDS.md)
- [Historical checklist](../99_archive/SI6_FINAL_REAL_RUN_CHECKLIST.md)
- [Current phase methods](../05_nmr_analysis/01_phase_methods.md)
- [October 15 hardening and phase validation](../99_archive/SI6_HARDENING_PHASE_VALIDATION_REPORT_2026-10-15.md)
- [Physical-state backup](../06_deployment_and_backup/03_rig_state_backup.md)
- [Synthetic validation](../99_archive/SI6_SYNTHETIC_ANALYSIS_AUDIT.md)
- [Historical validation report](../99_archive/SI6_FINAL_HOME_VALIDATION_REPORT.md)

Instructions for `scripts/02_si6_automated_nmr.py` concern a legacy manual
workflow and do not replace this guide. Statistical/QC historical reports are
scientific evidence, not the current area-only endpoint policy. No HOME mock
result proves physical delivery, actual needle position, NMR acquisition or
chemistry commissioning. ACME live control still requires instrument-side review.
