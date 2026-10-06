# Si6 two-stage implementation handoff

2026-10-06, HOME laptop. **Verified in software/simulation only.** No physical
pump, serial response, dose volume, needle position or NMR acquisition was
validated. See the independent `SI6_TWO_STAGE_VALIDATION_REPORT.md`.

## Outcome and architecture

The existing shared runner now supports the configurable Si6 sequence:
decreasing Stage 1 → one confirmed channel-2 infusion → increasing Stage 2.
Channel 1 remains the nominal sampling channel; per-transfer channel/rate and
independent syringe bookkeeping are preserved. No second workflow engine or
replacement phase algorithm was introduced. Operator changes belong in YAML.

Before changes, `scripts/02_si6_experiment.py` used `three_instrument_si6.Services`,
existing Chemyx/Arduino/NMR interfaces, base `Stage`/monotonic scheduler, production
FID processing and journal/replay. The exact sampling SOP already existed.
Completion was a global adjacent-percent plateau without direction/low-region/
minimum duration; boundary actions could follow an operator advance without
chemical completion, and demo startup only warned about unresolved prior runs.
The detailed 16-question audit is `audits/SI6_TWO_STAGE_INITIAL_AUDIT.md`.

## Nominal YAML and editable parameters

The complete final YAML is
[`si6_two_stage_nominal.yaml`](../config_templates/experiments/si6_two_stage_nominal.yaml).
It sets 120 min/48 h decreasing monitoring, a channel-2 1.8 mL infusion at
1 mL/min, then 30 min increasing monitoring with expected 3 h/max 6 h.
NMR is nominally 8 scans; pre-acquisition pause is 300 s. Syringe geometry,
starting retained volume, rates, margins and **all numerical completion/QC
criteria** are editable. These are uncommissioned starting values.

`SI6_TWO_STAGE_CONFIGURATION.md` lists every editable operator parameter and the
strict schema functions. Other complete templates provide virtual-clock fast
simulation and Stage1/Stage2 development starting points. The machine template
is `config_templates/machines/si6_instrument_settings.example.yaml`.
Initial UP and the DOWN/UP roles in this sampling SOP are enforced invariants;
actual coordinates/direction/speed are in Arduino YAML.

## Exact cycle, completion and transition

Verify UP → Ch1 withdraw 8 mL air UP → needle DOWN → withdraw 5 mL liquid DOWN →
configured pause → NMR → infuse 13 mL DOWN → needle UP → withdraw 5 mL UP →
infuse 5 mL UP → confirm idle. Completion does not skip return/clearing.

Stage 1 requires a positive initially detected QC-passing peak, material decline,
low uncertainty-expanded integral, minimum duration/points, bounded rolling
range/slope and several consecutive passing windows. Stage 2 permits a valid
near-zero baseline, requires later detected material growth, minimum duration/
points, several stable windows, and proximity to the greatest observed valid
integral. A high signal dropping to a flat low level does not qualify.
Stage limits without genuine completion stop inconclusively; operator advance
does not authorize the reagent transition.

Normal per-spectrum phase/baseline/peak processing remains intact. Added fixed
integrals come from retained `regional_quantitative` arrays, with strict unique
production-prefixed evidence filename matching, raw hash/phase/finite-value QC,
noise/uncertainty and negative-lobe/rejected-candidate guards. An undetected peak
is not converted to detector zero. Metadata timing must be present and strictly
increase; no filename/mtime substitution.

An exclusive fsynced `runtime/si6_doses/<experiment_id>.json` reservation precedes
transports. Durable dose intent precedes boundary motion. Confirmation links the
exact completed Ch2 infusion and parent addressed STOP to configured volume/rate,
sequence, experiment identity and config digest. The boundary also restores UP.
Stage 2 begins only after durable confirmation. Ledger RESERVED / DISPATCH_INTENT /
CONFIRMED, corrupt/torn state, changed output directory or changed configuration
all block a fresh same-ID live run. This is at-most-once automatic dispatch across
failures, not a promise of measured delivery after power loss. **No automatic
partial-run resume exists.** Inspect/reconcile manually; never invent a new
experiment ID to bypass a blocked reaction. Copy ledger and owning journal with
the physical experiment.

## Outputs and portability

Raw spectra, per-acquisition production processing/plots/QC/phase arrays,
effective instrument settings and config snapshots, journal and replayed state
remain in the existing run structure. Added stage directories contain independent
CSV/PNG/SVG/PDF/summary/evidence and update after every completed cleanup.
`transition/channel2_addition.json` retains the dose receipt. `final` holds the
combined CSV/titled plot with a journal dose marker, summary and plot manifest.
The CLI also produces the existing offline final NMR summary after ports close.

Every figure visibly identifies its dataset; PNG/SVG/PDF/manifest titles match.
Stage axes explicitly use the first stage acquisition as metadata origin; the
readiness/loading time before that acquisition is not included. Dose timing is
aligned using monotonic elapsed time and labeled as a journal boundary. This is
an explicit reporting limitation versus a precise chemically measured onset.

Resource paths/output roots resolve from the derived repository root. Processing
folder identities now include a full-stem digest and reject overwrites. Git is
optional provenance, so absence on WORK2 cannot break required processing.
Source scanning found no required HOME path/cloud/download dependency.
Remaining local resources are COM assignments, local NMR LAN endpoint, driver/
vendor installations and actual calibration; optional Windows font paths and
development-only reference download scripts are documented separately.

## Files modified

- `.gitignore`
- `arduino/python/needle_state.py`
- `chemyx_lab/analysis/phase_audit.py`
- `chemyx_lab/config.py`
- `chemyx_lab/recovery.py`
- `chemyx_lab/runtime_state.py`
- `chemyx_lab/testing/mock_serial.py`
- `chemyx_lab/workflows/si6_automated_nmr.py`
- `chemyx_lab/workflows/three_instrument_si6.py`
- `config_templates/experiments/README.md`
- `docs/CONFIGURATION.md`
- `docs/THREE_INSTRUMENT_ARCHITECTURE.md`
- `offline/install_offline.ps1`
- `tests/test_si6_safety.py`

## Files added

- `chemyx_lab/analysis/si6_stage_reports.py`, `stage_completion.py`, `stage_measurement.py`
- `chemyx_lab/workflows/pump_channels.py`, `dose_guard.py`, `si6_profile.py`, `si6_simulation.py`
- `chemyx_lab/testing/fixtures/si6_two_stage_trends.json`
- `config_templates/experiments/si6_two_channel_once.yaml`
- `config_templates/experiments/si6_two_stage_nominal.yaml`
- `config_templates/experiments/si6_two_stage_fast_sim.yaml`
- `config_templates/experiments/si6_two_stage_stage1_development.yaml`
- `config_templates/experiments/si6_two_stage_stage2_development.yaml`
- `config_templates/machines/si6_instrument_settings.example.yaml`
- `scripts/validate_si6_offline.py`
- `tests/test_si6_pump_channels.py`, `test_si6_two_stage.py`, `test_si6_measurement_qc.py`, `test_si6_validator_gates.py`
- `docs/CHEMYX_CHANNEL_WORKFLOW.md`, `SI6_TWO_STAGE_CONFIGURATION.md`, `SI6_TWO_STAGE_OPERATOR_GUIDE.md`
- `docs/OFFLINE_DEPLOYMENT.md`, `OFFLINE_REQUIREMENTS.md`
- `docs/SI6_WORKFLOW_VALIDATOR.md`, `SI6_TWO_STAGE_VALIDATION_REPORT.md`, this handoff
- `docs/audits/SI6_TWO_STAGE_INITIAL_AUDIT.md`

The file list includes the preserved dual-channel work delivered with this
continuation. Historical results and NMR phase algorithms were not overwritten.

## Tests and independent review

All tests used existing CPython 3.11/Conda `ai`, whose core versions match the
offline lock. Commands below used `python -B -m pytest`, `-q -p no:cacheprovider`
and distinct ignored `--basetemp test_tmp_*` directories. Suites overlap; counts
must not be summed as unique tests.

| Check | Result |
|---|---|
| Independent pre-change baseline: channels, integrated runner, monitoring, journal | 135 passed |
| Broad regression: two-stage, validator, channels, integrated, monitoring, safety, base Si6, journal integration, runtime journal, configs, templates, final summary, plot titles, NMR timing | 307 passed, 26 existing nmrglue warnings, 768.28 s |
| Final current integration: two-stage, validator, measurement QC, runtime journal | 93 passed, 26 warnings, 317.84 s |
| Final focused gates: validator, measurement QC, Si6 safety, journal, templates | 129 passed, 3.60 s |
| Last measurement/validator rerun after NumPy compatibility fallback | 34 passed, 0.92 s |
| Independent validator counterexamples | 26 passed |
| CLI full fast mock and final copied-directory mock | PASS: 13 Stage1 acquisitions, one Ch2 1.8 mL @ 1 mL/min, 12 Stage2 acquisitions |
| Real repository fixture processor/adapter contract | Production peak 5.792 ppm, picked area 30.69, fixed integral 30.4923; nominal QC rejects; diagnostic loose-QC adapter accepts (software only) |
| Strict configuration/resource checks, targeted Ruff, PowerShell installer parse | PASS |

The broad and focused command file lists are reproduced in the independent
report; deterministic `scripts/validate_si6_offline.py --run-tests` supplies an
offline rerunnable core checklist. The first new run had one test-schema
assertion failure (wrong journal volume field), corrected and rerun. Independent
review found and drove corrections to disappearing-peak handling, demo recovery
admission, processing collisions, missing-Git provenance, nonfinite metrics,
phase cancellation/rejected candidates, Stage2 near-zero baseline/growth and
large-decline plateau, production-prefixed filenames, effective auto-gain/units,
manifest validity and semantically matched dose confirmation.

**Remaining commissioning limitation:** actual retained fixture noise is 3.48%
of target height versus nominal 1% max; conservative integral uncertainty is
about 29% versus nominal 0.5% max. The nominal live QC/chemical endpoint values
are not validated for that spectrum. Controller-fixture completion does not
establish measurement precision or chemical yield. WORK must establish suitable
spectral precision and reviewed QC/endpoint calibration before a reaction.

## HOME / WORK1 / WORK2 status and exact commands

**HOME software: PASS WITH LIMITATIONS.** Code, targeted tests, mock sequence,
recovery crash windows, strict config, production-array contract, visible titles,
static paths and copied-directory mock are verified. No hardware was contacted.

From repository root:

```powershell
conda activate ai
python -B scripts\02_si6_experiment.py --workflow-config config_templates\experiments\si6_two_stage_nominal.yaml
python -B scripts\02_si6_experiment.py --mock --workflow-config config_templates\experiments\si6_two_stage_fast_sim.yaml
python -B scripts\validate_si6_offline.py --run-tests --copy-test
```

HOME sandbox execution remapped only output root to ignored test directories;
the copied-root mock used repository-relative resources and unchanged kinetics.

**WORK1: PENDING OPERATOR VERIFICATION.** After pull, inspect revision/resources,
preserve reviewed rig settings, prepare/verify offline wheelhouse and installers,
install drivers/vendor NMR interfaces, run offline mocks/tests and supervise
hardware/chemistry calibration. Source delivery does not transfer ignored payload.

**WORK2 offline: PENDING OPERATOR VERIFICATION.** Copy the complete repository,
offline payload, reviewed local configs and relevant rig ledger/journal/history
using the hard drive. Install a fresh destination `.venv` with the existing
`offline/install_offline.ps1 --no-index` behavior. Run resources/mocks/tests
before connecting instruments. Identify COM/IP settings on this laptop and
explicitly confirm inspected needle HOME; another rig's state is not proof.

Exact WORK experiment command after commissioning and editing the run YAML:

```powershell
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_run.local.yaml --machine-config configs\machines\si6_work.local.yaml --arduino-config arduino\configs\arduino.local.yaml
```

It retains existing live/readiness confirmations. See operator guide for the
supervised confirm-home command and inspection/reconciliation commands.

**Copy/install requirements:** source/config/templates/fixtures/tests/docs,
Arduino firmware and host code, analysis YAML, root environment/project files;
ignored offline wheels/installers/manifest plus needed drivers/toolchain;
relevant local machine/experiment configs and physical experiment ledger/journals.
Install Python 3.11 x64 and the pinned dependencies, correct USB/serial drivers,
commissioned Arduino firmware, and NMR vendor/RPC software/licenses before going
offline. `OFFLINE_REQUIREMENTS.md` and `OFFLINE_DEPLOYMENT.md` provide full steps.
No cloud model, Git executable, internet or package fetching is needed at runtime.
