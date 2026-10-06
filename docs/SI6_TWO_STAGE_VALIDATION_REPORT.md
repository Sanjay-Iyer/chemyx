# Independent Si6 two-stage validation

Validation context: HOME laptop, 2026-10-06. The independent validator operates
with code inspection, automated tests, configuration checks, and mocks only.
No Chemyx, Arduino, or NMR hardware is connected or validated here.

## Final software assessment

**Software review passes with commissioning limitations.** The architecture
preserves the existing three-instrument sampling cycle, supports configurable
directional completion, and prevents automatic repeated reagent dispatch.
Nominal QC rejects the real retained fixture; numerical QC/endpoint thresholds
and all physical instrument behavior require supervised WORK commissioning.
There is no automatic resume of an interrupted reaction.

| Section | Final software status | Evidence/limit |
|---|---|---|
| Sampling/configuration | PASS | Exact cycle/order/needle context, settings and units gates, legacy regression |
| Stage completion | PASS WITH LIMITATIONS | Independent directional, low-signal, growth, decline, timing and noisy-data counterexamples pass; physical chemistry thresholds uncalibrated |
| One-time transition/recovery | PASS | Admission before transports, durable intent, matched pump/STOP confirmation, crash/replay gates; manual reconciliation only |
| Analysis/output | PASS WITH LIMITATIONS | Production fixed-integral contract exercised, historical outputs and visible titles checked; nominal real-fixture QC rejects |
| Offline portability | PASS WITH LIMITATIONS | Final copied-root mock passes; drivers, ignored deployment payloads and WORK configuration remain operator prerequisites |
| Full experiment | PASS | Latest integrated suite 93 passed; mock Stage 1 → one configured Ch2 dose → Stage 2 completed |

The historical audit and incremental findings below preserve the independent
review trail; their initial pending/failure statuses were superseded by the
corrections and final evidence recorded here.

## Initial architecture audit

The existing `scripts/02_si6_experiment.py` delegates to
`chemyx_lab/workflows/three_instrument_si6.py`. Its services combine the existing
Chemyx driver, Arduino `NeedleController`, NMR RPC acquisition, production
`process_fid.py` processing, and journal/replay infrastructure. The base
`si6_automated_nmr.py` provides configuration validation, stages, monitoring
scheduling, completion outcomes, metered moves, and reporting.

The fixed sampling cycle already implements UP verification, air withdrawal,
DOWN motion, liquid withdrawal, settle pause, NMR, sample return while DOWN,
UP motion, clearing withdrawal, and clearing infusion. Nominal volumes are
configuration values. Cleanup finishes before a completed measurement can
advance the experiment; failed NMR can trigger cleanup only with proven pump
and needle state. Physical uncertainty stops execution for review.

Stages currently use `initial_stage`, `first_addition_stage`, and optional
repeating stages, with interval/duration scheduling and stage boundary actions.
Dual channels already have independent configuration, volume estimates, motion
state, and addressed STOP handling. The channel extension must be preserved.

NMR scan count belongs to acquisition configuration, independent of monitoring
cadence. Production processing is a subprocess using the current interpreter
and repository script. Tracked metrics are read from its simple-peak/QC tables.
JCAMP LONG DATE is required by the runner and supplies acquisition timestamps.
Raw acquisition files, processed folders, tables, plots, manifest, configuration
snapshot, operation journal, state snapshot, and terminal summary live in a
new run directory.

## Findings sent to the implementation agent

1. Global adjacent-percent plateau logic has no directional progress, minimum
   duration, or low-signal Stage-1 gate. Reuse it carefully with stage-specific
   completion evidence rather than replacing validated spectrum processing.
2. The current analyzer rejects every missing/non-clear peak. A disappearing
   Stage-1 signal needs a bounded, QC-qualified measurement path; failed
   detection alone must never establish chemical completion.
3. `after_monitoring` also executes following operator ADVANCE without plateau.
   The automatic chemical transition must require genuine completion.
4. Recovery initially supports inspection only. Boundary actions have pump UUID
   lifecycle evidence but no stable chemical-dose identity. Add durable dose
   semantics and explicit uncertain-dose resolution; never claim generic
   inspection is already normal resume.
5. Existing CSV columns omit `timestamp_source` and raw/processed/phase references
   even though some evidence exists in metadata and the journal.
6. Existing mock execution replaces configured cadence and stage limits. New
   deterministic simulation must distinguish accelerated simulation from tests
   that establish nominal configuration and scheduling behavior.
7. Processing folder names use only the first 15 input-stem characters and the
   processor permits an existing directory. Non-timestamp labels sharing that
   prefix can overwrite prior outputs. Preserve complete acquisition identity
   and historical processing.
8. The offline bundle tooling and pinned requirements already exist. Payloads
   such as wheelhouse, drivers, and installers are ignored by Git and must be
   staged separately before offline deployment.
9. Normal experiment source/configuration inspection found no hardcoded
   development-machine absolute paths or cloud requests. NMR uses local
   instrument RPC. Repository roots derive from source paths. Machine COM ports,
   NMR endpoint, and measured needle calibration are local instrument settings.
10. Optional DEEP Phaser comparison depends on local Node/model assets under
    ignored historical results. It is outside the required experiment runtime;
    document it as optional rather than adding it to experiment prerequisites.
11. Previous-live-run review currently prints an advisory in demo mode, and
    `open_services` does not call the existing review guard. A fresh live run
    can therefore bypass unresolved old physical state. Two-stage restart must
    enforce dose/physical-state reconciliation before opening transports.

## Independent review gates

| Section | Gate | Initial status |
|---|---|---|
| Sampling/configuration | Exact physical action order; UP/DOWN invariants; all five transfer channels, volumes and rates; NMR scans and pause; invalid configuration rejected before motion; legacy tests | Pending implementation |
| Stage completion | Decreasing low signal plus sustained stability; increasing conservative plateau plus demonstrated growth; minimum points/time; noisy/invalid observations; independent stages and configured cadence | Pending implementation |
| One-time transition/recovery | Genuine Stage-1 completion prerequisite; configured channel/volume/rate; durable not-started/uncertain/completed; crash windows; no repeat after confirmed dose; operator resolution cannot conceal dispatch uncertainty | Pending implementation |
| Analysis/output | Production processing unchanged; per-acquisition historical outputs; stage CSVs and summary; authoritative timestamps; dataset-visible titles and matching manifest; transition marked in full series | Pending implementation |
| Offline portability | Tracked runtime resources; dependency/install manifest; copy to another root; isolated no-network mock execution; no HOME-path dependencies | Pending implementation |
| Full experiment | Deterministic Stage 1 → exactly one dose → Stage 2 → conservative completion; journal inspection; independent channel totals; all requested test coverage | Pending implementation |

## Physical limitations

Software completion evidence for a metered pump move is command execution,
timed wait, positively accepted STOP, and durable journal evidence. It is not
an independently measured delivered volume. Physical dose accuracy, addressed
drive commands/STOP replies, rate calibration, syringe dimensions, actual
needle coordinates, tubing/fluidics, NMR readiness/acquisition response, and
chemistry-specific numerical thresholds require WORK hardware verification.

## Incremental and final review results

### Baseline regression gate — PASS

Independently run before the two-stage implementation:

```powershell
conda run -n ai python -B -m pytest tests/test_si6_pump_channels.py tests/test_three_instrument_si6.py tests/test_si6_monitoring.py tests/test_runtime_journal.py -q -p no:cacheprovider --basetemp .codex_pytest_temp_validator_baseline
```

Result: **135 passed**, 26 `nmrglue`/NumPy deprecation warnings, 226.29 seconds.
This establishes the existing dual-channel, cleanup, scheduling, and journal
behavior in software before the new requirements are implemented.

### Proposed section design review — PASS WITH LIMITATIONS

The implementation agent proposed per-stage reusable directional completion,
fixed-target integrals from the retained production phase-audit trace,
independent signal/provenance/noise gates, explicit labeled simulation fixtures,
virtual-clock scheduling, and a durable experiment reservation/dose ledger.
This preserves existing drivers and production processing. The proposed
fail-closed no-automatic-resume policy is safe but must be documented explicitly.

The validator requested exclusive atomic ledger reservation, crash/torn-ledger
tests, immutable experiment/dose/config identity, durable intent before dispatch,
matched pump completion before ledger confirmation, no output-root bypass,
strictly increasing metadata acquisition times, positive detected initial
normalization baseline, a meaningful Stage-2 growth prerequisite, and copying
local ledgers/prior-run evidence for offline transfers. Implementation evidence
is still pending; design approval is not implementation validation.

### Incremental module review — corrections requested

- The first completion implementation required an initially detected positive
  peak for both directions. Stage 2 must support a QC-qualified near-zero initial
  observation and later prove growth; decreasing initial normalization still
  requires a positive detected reference.
- Optional `expected_duration_hours` should reject booleans and non-number
  values consistently with other completion values.
- The first bounded-absence implementation recognized the analyzer message
  `no QC-passing peak`, which also describes QC-rejected detected candidates.
  Distinguish actual absence from rejected width/polarity/phase candidates;
  signed cancellation and failed detection must not establish low signal.
- Validate finite phase metadata and phase provenance, alongside finite retained
  arrays and raw hash, rather than relying solely on phase method != `none`.
- Reject nonfinite detected-peak metrics explicitly; NaN comparison results
  must not accidentally pass SNR/position gates.
- Refresh dedicated stage CSV/plot/summary after completed samples, so a long
  monitoring stage exposes its evidence while it is still running.
- Record stage-time and dose-marker origins precisely. The initial report used
  first-acquisition stage origins and wall-clock dose alignment despite a
  monotonic-duration comment.
- Required production `phase_audit.provenance` initially launched Git without
  handling a missing executable. A copied offline deployment must continue with
  unavailable Git provenance; repository operation must not require Git on
  WORK laptop 2.

### Independent chemical/admission counterexamples — PASS

Validator-authored `tests/test_si6_validator_gates.py` was run with:

```powershell
conda run --no-capture-output -n ai python -B -m pytest tests/test_si6_validator_gates.py -q -p no:cacheprovider --basetemp .codex_pytest_temp_validator_gates
```

Result: **18 passed in 0.43 seconds**. Cases include Stage-2 zero initial signal
then sustained growth/plateau, flat baseline and materially growing signal,
one anomalous low Stage-1 point, stable but insufficiently low Stage-1 signal,
invalid observations, repeated/backwards acquisition metadata, NaN areas,
concurrent admission, RESERVED/INTENT/CONFIRMED/torn reservation restart refusal,
rejected candidate absence, nonfinite phase parameters, and raw-hash mismatch.

The initial Stage-2 zero-signal, expected-duration type, phase-parameter, and
rejected-candidate findings have been corrected in the modules inspected. Full
orchestration, report-refresh, deployment, and missing-Git tests remain pending.

### Incremental integrated workflow review

The validator independently ran the CLI mock using a temporary copy of the fast
YAML with its output root redirected into an ignored validator directory:

```powershell
conda run --no-capture-output -n ai python -B scripts/02_si6_experiment.py --mock --workflow-config .codex_pytest_temp_validator_smoke/workflow.yaml --arduino-config arduino/configs/arduino.example.yaml
```

Result: **exit 0, completed**. The captured run had 13 Stage-1 acquisitions,
exactly one channel-2 1.8 mL addition at 1.0 mL/min, and 12 Stage-2 acquisitions.
Stage completion evidence contained three passing rolling windows each; Stage 2
began at zero area and completed after sustained growth/plateau. Ledger status
was CONFIRMED. The validator visually inspected full and Stage-2 PNGs: visible
dataset titles, distinct stage trends, and an obvious dose boundary.

Offline inspection was independently run:

```powershell
conda run --no-capture-output -n ai python -B scripts/02_si6_automated_nmr.py --inspect-run .codex_pytest_temp_validator_smoke/results_mock/20261006_151642_si6_mock
```

Result: **exit 0, terminal_completed, current state snapshot, no unresolved
operations**. Channel 1 withdrew/infused 450 mL each across the simulated cycles
and ended at 0 mL retained; channel 2 infused 1.8 mL and ended at 0.2 mL. These
are software estimates and journal totals, not physically measured delivery.

The independent counterexample suite was extended with missing-Git provenance
and a service-level replay barrier using fake transport constructors. Result:
**20 passed in 0.82 seconds** with
`--basetemp .codex_pytest_temp_validator_gates_3`. A same-ID prior reservation
blocked changed output-root/configuration entry before either fake transport
constructor. No hardware was opened.

Integrated review confirms reservation precedes transports, intent precedes
boundary actions, confirmation follows addressed STOP and durable metered move
plus boundary cleanup, and stage reports refresh after completed sampling
cleanup. Dose marker alignment now uses monotonic elapsed time. The first
new-suite run exposed a primary-test assertion using the wrong journal field
(`requested_volume_ml` instead of `requested_parameters.volume`); this is a
test-schema issue, awaiting corrected rerun. The first-acquisition time origin
for stage plots is explicitly labeled and remains a reporting limitation.

### Conservative Stage-2 decline counterexample — corrected, PASS

Independent test `test_validator_stage2_large_decline_then_flat_low_level_is_not_completion`
demonstrated that `[0, 100, 200, 60, 60, 60, 60, 60]` can complete with
`minimum_progress_fraction=0.25`, despite falling to 30% of the observed maximum.
The rolling flat-window condition alone does not protect maximum-yield behavior
after a large decline. The validator requested that the recent plateau remain
near the greatest QC-qualified observed signal using a configurable evidence
bound. The implementation now requires the uncertainty-expanded recent minimum
to remain near the greatest observed area within `max_window_range_fraction`.
The counterexample was rerun successfully. This correction changes only stage
evidence, preserving all raw/phase processing.

### Final focused counterexamples/configuration — PASS

After the decline correction, independent chemical/admission/QC/configuration
checks reported **37 passed in 2.76 seconds**:

```powershell
conda run --no-capture-output -n ai python -B -m pytest tests/test_si6_validator_gates.py tests/test_si6_two_stage.py::test_templates_validate tests/test_si6_two_stage.py::test_invalid_configs_rejected_before_motion tests/test_si6_two_stage.py::test_stage2_does_not_complete_one_flat_or_still_growing_interval -q -p no:cacheprovider --basetemp .codex_pytest_temp_validator_corrected
```

The validator added synthetic retained-trace acceptance/absence/cancellation
evidence and YAML/environment auto-gain rejection tests. Final validator-owned
suite: **24 passed in 2.08 seconds**, using
`--basetemp .codex_pytest_temp_validator_final_gates`. Automatic gain is now
rejected both in the profile and in effective NMR settings; low-signal plot
manifests now record actual measurement validity.

The corrected one-dose orchestration assertion was independently rerun:
**1 passed**, 26 existing `nmrglue`/NumPy warnings, 119.15 seconds. Together with
the initial **50 passed / 1 test-schema failure** run, this validates the new
orchestration and crash-window cases. Final primary regression/copy evidence
will be reviewed separately before closing deployment status.

### Production retained-array contract — corrected, PASS WITH LIMITATIONS

The validator ran the real production processor on the tracked, existing
`chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx` fixture:

```powershell
conda run --no-capture-output -n ai python -B scripts/nmr/process_fid.py chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx --output-dir .codex_pytest_temp_validator_real_processing --run-name actual_fixture_contract_v1 --dataset-display-name VALIDATOR-REAL-FIXTURE --region-min 5 --region-max 6.5 --simple-restrict-to-window --simple-target-ppm 5.8 --simple-window-ppm 0.1
```

The processor completed successfully and detected a peak at 5.792 ppm, area
30.69, SNR 31.23. This revealed an actual adapter contract defect: retained
production files carry a run-name prefix, whereas the initial adapter searched
only bare filenames. The corrected adapter requires one matching
`*spectral_evidence.npz` plus its same-prefix `processing_metadata.json`.
Independent regression now covers both bare and prefixed retained filenames.
The production fixed-window integral is 30.49231837318567.

**The nominal QC thresholds reject this real fixture.** Corrected sideband
noise is 3.48% of the initial detected target height (nominal limit 1%); the
conservative fixed-area uncertainty estimate is 29.00% of its initial integral
(nominal limit 0.5%). The nominal adapter fails closed with
`Spectrum noise exceeds initial-signal QC bound`. This is a remaining
commissioning/calibration requirement; successful mock completion does not
validate a physical endpoint or guarantee nominal real-spectrum acceptance.

For diagnostic contract verification only, the validator's ignored local
script applied noise limit 10% and area-uncertainty limit 35%. The actual
production evidence then passed with the same fixed integral. These loose
limits are **not chemistry-calibrated recommendations**, and no runtime example
threshold was relaxed. Evidence is retained in
`.codex_pytest_temp_validator_real_processing/contract_result.json`.

### Semantic dose replay — corrected, PASS

An independent counterexample initially showed that replay accepted a sequence
of semantic RESERVED/INTENT/CONFIRMED records without its actual pump movement.
Confirmation now requires the exact completed channel-2 infusion, its recorded
completion sequence, matching intent/configuration/experiment identity and
volume/rate, and an accepted completed channel-2 STOP addressed to that same
pump operation. A semantic confirmation lacking that evidence produces
`dose_completion_unproven`; unresolved intent remains inspection-only.

The latest validator-owned suite passed **26 tests in 1.50 seconds**:

```powershell
conda run --no-capture-output -n ai python -B -m pytest tests/test_si6_validator_gates.py -q -p no:cacheprovider --basetemp .codex_pytest_temp_validator_final_qc_replay
```

These tests include production filename contracts, fixed-trace QC and negative
cancellation, missing Git, YAML/environment auto-gain rejection, semantic
confirmation without pump evidence, and prior same-ID rejection before fake
transport constructors. They use no physical instrument.

### Final regression and deployment evidence review

The validator reviewed primary-produced final logs. The broad 14-file
regression in `test_tmp_two_stage_cli/final_tests.log` completed with **307
passed, 26 existing nmrglue/NumPy deprecation warnings, 768.28 seconds**. Its
process began before the latest small semantic/units changes; focused current
gates in `test_tmp_two_stage_cli/final_gates_2.log` report **108 passed in 2.92
seconds**. The earlier failed effective-settings serialization assertion was
corrected and rerun successfully.

The final copied-directory CLI mock completed successfully at a separate
temporary repository root. The reviewed evidence is
`test_tmp_offline_validation/copied_directory_report.json` and
`test_tmp_two_stage_cli/copied_directory_final.log`. It contains 13 Stage-1 and
12 Stage-2 measurements, both sustained completions, Stage-2 initial area zero,
and one CONFIRMED channel-2 addition of 1.8 mL at 1.0 mL/min. Controller trend
fixtures, simulated transport, and virtual timestamps remain explicitly
labeled. This validates software portability, not instrument drivers,
electrical connections, delivery calibration, real NMR QC thresholds, or WORK
computer commissioning.

Reviewed operator/configuration/offline documentation describes repository
relative resources, separately staged ignored wheels/drivers, machine-local
COM/IP/calibration configuration, fixed quantitative gain, strict live needle
home confirmation, at-most-once dispatch, and manual reconciliation without
automatic resume. Plots explicitly label their elapsed-time origin as first
stage acquisition. Moving an interrupted physical reaction requires carrying
its ledger and prior-run evidence; a new experiment ID is not a recovery
shortcut. No live hardware was exercised on HOME.

The latest current-code integrated regression was reviewed in
`test_tmp_two_stage_cli/final_integrated.log`: **93 passed, 26 existing
nmrglue/NumPy warnings, 317.84 seconds**. Current additional gates in
`test_tmp_two_stage_cli/last_gates.log` report **129 passed in 3.60 seconds**;
`copied_directory_current.log` again reports copied-root PASS. These checks
include the final prefixed-array contract, effective units/settings and
strengthened dose-confirmation implementation. Operator/configuration guides
explicitly state actual nominal fixture rejection and required calibration.
