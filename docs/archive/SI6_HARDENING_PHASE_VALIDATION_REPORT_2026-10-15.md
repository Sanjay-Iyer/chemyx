> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../SI6_SIMPLE_RUN_GUIDE.md) and [errors/fixes](../SI6_ERRORS_AND_FIXES.md) at the instruments.

# Si6 hardening and quantitative phase validation — October 15, 2026

Implemented for review on `main`, based on
`15fbb35bf95e95cbeddef68a3b257ad55469fa33`. Nothing was committed or pushed.
HOME work used analysis, local files and mock instruments exclusively.
ACME is the real recipe default; optional comparisons remain disabled.

## A. Repository improvements

- Maintained validation now lives in `chemyx_lab/testing/nmr_validation`.
  Inspection of all three October 4 scripts identified `algorithm_validation.py`
  and its `robustness_refinement.py` dependency as reusable. The active importer
  was `scripts/nmr/run_algorithm_validation.py`; the synthetic validator already
  used maintained source. The archive and its dataset-specific finalizer remain
  intact. Numerical helper ASTs match the archive, and the input-stage replay
  succeeded in a new output directory. The archived finalizer was not executed
  because it writes historical evidence. Historical inputs remain an optional
  evidence package, separate from portable source.
- `scripts/rig_state_backup.py` provides inventory, timestamped backup, SHA-256
  verification, comparison and a read-only `restore-plan`. It covers configured
  Arduino state, UNKNOWN/pending HOME markers, dose reservations and journals,
  including schema versions, experiment IDs, source locations and timestamps.
  It never writes a restored ledger or position reference. Conflicting/newer
  receipts and uncertainties require conservative reconciliation on the rig.
  Copying a position record cannot prove the needle's physical position.
- [SI6_START_HERE](../SI6_START_HERE.md) is the operating index. Detailed current
  guides link from it; obsolete runner instructions are labeled historical.
- `offline/release_manifest.yaml` supplies the shared source/resource inventory
  to release export, offline installation and validators. It inventories the
  pinned wheel lock and portable DEEP resources. Review exports explicitly
  identify uncommitted source with a tree hash and parent commit; packages never
  silently overwrite an existing archive. Physical state is excluded.

Arduino pytest discovery and `pyproject.toml` were not modified. Existing HOME,
motion, STOP, dose, journal, dated-output and startup-confirmation behavior was
preserved. The separate Arduino DM542S tests were invoked explicitly.

## B. Primary quantitative phase correction

Actual real-recipe configuration in `configs/experiments/si6_real_two_stage.yaml`:

```yaml
nmr:
  processing:
    phase_method: acme
    phase_comparison:
      enabled: false
      methods: [stored, acme, combined_objective, ernst_p0, deep_phaser]
      include_unphased_reference: true
```

Change only `phase_method` to select another supported primary. It changes the
quantitative spectrum, hence potentially areas and endpoints. Legacy recipes
without this block keep their existing common-processing defaults; snapshots
identify that source instead of incorrectly declaring an explicit selection.

The actual path is `three_instrument_si6.prepare/open_services` (validate method
and dependencies before opening transports), `run_process_fid_postprocessing`
(explicit `--phase-method`), `process_fid.main`, `nmr.build_phased_spectrum`,
`phase_methods.apply_phase`, existing `process_spectrum_for_peaks`, retained
candidate/complex evidence, `tracked_peak_area_observation`, then
`area_completion_evidence`. The numerical arrays supplying Stage 1/2 decisions
are the arrays phased by the selected method. Baseline, tracking, integration
and completion thresholds were not replaced or relaxed.

The standalone NMR diagnostic and processing-only command also preflight and
propagate the recipe selection. Processing-only uses the current area reader
and independent solvent gate when `peak_finding` is configured; legacy recipes
retain their previous QC reader. A final actual real-recipe fixture run verifies
this route, beyond testing that the method argument reaches the processor.

Internal ppm is ascending; displayed ppm is reversed. The phase convention is
`S[k] exp(sign*i*pi/180*(P0+P1*k/N))` with index-zero pivot. ACME uses the exact
historical nmrglue ACME score and SciPy Nelder-Mead call, initialized from
negative DX PHC0/PHC1, `xtol=ftol=1e-4`, 1000 iterations/2000 evaluations.
Production and validation share `phase_optimizers.minimize_acme`. Metadata is a
starting guess; the optimized spectrum supplies measurements. Nonconvergence,
invalid predictions or nonfinite spectra fail explicitly, with no alternative
method substitution and no invented zero area or dose authorization.

Full-spectrum magnitude solvent monitoring remains independent of phasing.
Three consecutive missing-solvent observations latch the original stage timed
fallback. A genuine target nondetection with solvent present may carry the
existing zero measurement; missing sample data carry unavailable evidence.

## C. Supported algorithms

All five are implemented as selectable primaries and exercised offline. This is
software eligibility, not live instrument/chemistry commissioning.

| Method | Offline dependencies | Numerical outcome and limits | Live-use status |
|---|---|---|---|
| stored | NumPy, SciPy, nmrglue | 15/15; original inverse DX phases; historical missing-phase defaults retained | Existing route; still requires rig verification |
| acme | Same pinned wheels | 15/15; exact validated objective/seed/budget; independent quality improved on this cohort; local optimizer can fail on future data | Requested default; offline validated, not commissioned |
| combined_objective | Same wheels | 15/15; original four equal weights, bounds, grid and Powell starts; unchanged custom objective | Selectable; review method-dependent areas |
| ernst_p0 | Same wheels, pinned NumPy with `trapezoid` | 15/15; analytical integral, P1=0, cancellation rejected; cannot remove first-order dispersion | Selectable with P0-only limitation |
| deep_phaser | Local Node v24.19.0, TFJS 4.22.0, WASM CPU, two author graph models and weight shards | 15/15 execution; author input normalization/search and reversed-axis mapping preserved; three weak-target detection disagreements | Offline functional; cohort evidence does not support commissioning for unattended endpoints |

DEEP requires neither PyTorch nor Python TensorFlow. The resource manifest records
every file hash, author provenance and model format. Missing/changed resources
reject selected DEEP before transports open. Preflight loads both models; actual
inference has a 500-prediction bound and 900-second subprocess timeout. No model
download occurs. `unphased` is comparison-only and cannot be selected as primary.

## D. Measured performance

Final archived cohort: seven June 8 and eight June 9 FIDs, 15 executions per
method. Wall times were measured on HOME under concurrent validation load.

| Method | Median seconds | Maximum seconds | Median phase seconds | Median downstream seconds | Failures |
|---|---:|---:|---:|---:|---:|
| stored | 1.603 | 4.610 | 0.063 | 1.539 | 0 |
| acme | 3.753 | 5.416 | 2.064 | 1.665 | 0 |
| combined_objective | 19.696 | 65.112 | 18.442 | 1.346 | 0 |
| ernst_p0 | 1.399 | 8.991 | 0.060 | 1.347 | 0 |
| deep_phaser | 201.932 | 340.607 | 200.378 | 1.565 | 0 |

Per-method times cover phase plus production baseline/candidates/tracking/QC
and initial evidence-array writing. Shared decode/FFT preparation, late phase
quality scoring and complete CLI plots/reports are outside those row timings.
They must not be represented as the whole acquisition cycle.

The original five-method loop plus summary figures took 3565.431 seconds
(59.42 minutes); its ACME definition was an initial zero-start/library-default
attempt that failed to converge on two spectra. That attempt was rejected.
All 15 ACME results were replaced in the final scientific report by the existing
validated metadata-seeded implementation, whose separate run took 82.004
seconds. The two preserved executions total 3647.435 seconds; this is not a
measurement of a fresh single final-method batch. DEEP model-load preflight
took 0.305 seconds; normal per-spectrum child startup/load is included in its
phase timings. Original output size was 157,770,711 bytes, plus 32,292,443 bytes
for the final ACME rerun. Python allocation peaks were 101,476,557 and
19,562,929 bytes respectively; native NumPy, Node/WASM and preallocated cohort
buffers are excluded. These are not process peak RSS measurements.

Whole-production-CLI fixture measurements, including process startup, decode,
FFT and all generated plots/reports, are recorded separately in
`results/phase_hardening_production_cli/timing.json`. They are single executions
on a committed fixture, not 15-spectrum medians or hardware-cycle measurements.

| Method | One full CLI execution, seconds |
|---|---:|
| stored | 78.043 |
| acme | 79.926 |
| combined_objective | 88.736 |
| ernst_p0 | 62.813 |
| deep_phaser | 307.071 |

All measured analysis paths fit inside 7200/1800-second sampling intervals on
HOME. The configured pump volumes/rates alone imply 432 seconds of flow, plus
five 2-second margins and a 10-second pause: 452 seconds per nominal cycle.
The one Channel 2 addition is nominally 110 seconds including margin. Actual
NMR acquisition, RPC transfer, needle movement, stop verification and cleanup
latencies remain unmeasured on the instrument laptop. Full-cycle margin cannot
be commissioned from HOME timing. Optional comparisons run after session closure,
so they do not compete with required acquisition scheduling.

## E. Saved results and figures

Primary experiment organization remains
`results/experiments/YYYYMMDD_experimentID[_n]/step_1`, `step_2`, `final_qc`.
Each primary acquisition retains complex arrays and processing metadata.
Enabled research outputs live under its `phase_comparison/<method>` with
`phase_audit/shared/spectral_evidence.npz`, metadata, `tracking_evidence.json`
and `result.json`. Successful method folders contain results; failures are
explicit unavailable rows and partial evidence is separated. Reruns refuse to
overwrite existing comparisons. No raw acquisition is copied per method.

Stage `analysis/phase_method_summary.csv` files remain separate; final comparisons
are under `final_qc/phase_method_comparison`. Comparisons have no instrument,
endpoint or dose authority. Failure of optional analysis or its error reporting
cannot change the primary experiment outcome.

HOME evidence is in these new directories; historical data remain read-only:

- `results/phase_hardening_benchmark_1`: original arrays, metadata, timings.
- `results/phase_hardening_benchmark_acme_validated`: final ACME arrays/results.
- `results/phase_hardening_scientific_final`: consolidated CSV/JSON, endpoint
  sensitivity, phase replay checks, all 15 real/imaginary/target overlays and
  area/time, descriptive difference, quality and runtime figures.
- `results/phase_hardening_production_cli`: complete public CLI analysis outputs.
- `results/phase_hardening_isolated_final`: copied release and guarded validation.

All 75 retained complex arrays were replayed against one freshly decoded common
FFT per acquisition: maximum absolute error was exactly zero, within required
`rtol=1e-12`, `atol=1e-9`. Raw hashes and ppm axes matched. PNG, SVG and PDF
figures share visible dataset titles and manifests. Time figures use one shared
CSV and JCAMP LONG DATE metadata. No filename schedule or file modification
time is substituted. These HOME evidence packages are intentionally outside
the source release; transfer them separately if desired.

## F. Scientific findings

The four classical methods agreed on detection for all 15 acquisitions (12
detected, three nondetected, solvent confirmed). Including DEEP, all-method
agreement is 12/15. DEEP additionally missed June 8 sequence labels 1215,
1315 and 1515, with stored areas 4.512, 2.190 and 1.658 respectively. These
labels identify files, not authoritative acquisition times.

For the 12 nonzero stored-area observations, area ratios versus stored were:

| Method | Minimum | Median | Maximum |
|---|---:|---:|---:|
| acme | 0.98415 | 1.00020 | 1.00405 |
| combined_objective | 0.91063 | 0.99012 | 1.02684 |
| ernst_p0 | 1.00006 | 1.00159 | 1.00467 |
| deep_phaser | 0.00000 | 1.03203 | 1.09016 |

These are descriptive differences, not accuracy against ground truth. ACME
spans approximately -1.585% to +0.405% relative to stored on those observations.
Phase-quality median negative absorption fractions were stored 0.01982, ACME
0.00234, Combined 0.00292, Ernst 0.00409 and DEEP 0.00373. Median even-imaginary
fractions were 0.20367, 0.11227, 0.07145, 0.13545 and 0.15550. Lower individual
diagnostics do not prove correct chemistry or universally reliable phasing.
Per-spectrum peak positions, SNR, shape/asymmetry, baseline, signed/positive
areas, uncertainties and warnings are retained in the shared results table.

Applying the current stage rules retrospectively to each cohort, only DEEP
satisfies Stage 1 on June 8, at observation 5 (LONG DATE
2026-06-08T15:17:54-04:00). The other four do not satisfy it within this cohort.
No method satisfies Stage 2 on either cohort; none satisfies Stage 1 on June 9.
These are rule-sensitivity calculations, not reconstructed physical stages,
validated reaction endpoints or dose authorization. The DEEP difference is a
material reason to keep it out of unattended control until investigated.

Final ACME had no convergence failures in this cohort and numerically matches
the pre-change historical validator on the committed reference case. Weak,
absent-target, missing-solvent, shifted, overlapping and noisy synthetic spectra
were also checked. No finite benchmark establishes universal future reliability;
strict failure handling remains necessary. Stored DX remains selectable by one
YAML edit if instrument-side assessment warrants it.

## G. Workflow and regression validation

Latest deduplicated results across the full regression and focused followups:
**1444 passed, zero unresolved failures, two optional skips**. This is a union
of test cases using their latest result, not one uninterrupted pytest invocation.
`results/phase_hardening_test_summary.json` lists every case and source JUnit XML.
The two skips are absent completed historical synthetic benchmark exports
(`synthetic_results.csv` and its finished figure manifest), not skipped DEEP
runtime checks.

The initial broad run had 1069 passed, three failures and two skips. The three
failures were optional-error logging with a minimal test fixture, UTF-8 reading
in a title test, and an old preservation test freezing deliberately extended
active source. All were corrected and passed targeted reruns. Preservation still
hashes historical results, verifies immutable original Git source and compares
unchanged scientific functions. It does not overwrite the old manifest or relax
scientific thresholds. Explicit Arduino DM542S plus related checks passed 346;
the final preservation/method/report suite passed 64 with the two optional skips;
the latest review checks passed 35.

Final review also found that standalone diagnostic/processing-only entry points
needed the same recipe-phase propagation, and the real area-only recipe needed
its current area reader in processing-only. Seven focused cases cover all five
method selections and preflight rejection before output/acquisition. A direct
real-recipe fixture check exposed the legacy-reader schema mismatch; it was
corrected without changing the automated experiment's measurement or safety path.

Full mock two-stage workflows use actual stored/ACME/Combined/Ernst phasing,
unchanged production integration and resulting endpoint observations. Each
completes both stages, exactly one protected Channel 2 dose and cleanup, with
no internal prompt. The DEEP control workflow substitutes only inference with
a labeled test prediction; actual models were independently executed on all 15
archived FIDs and in portable preflight. Existing CLI tests cover the single
startup `y`, declines and no added flags. Full numerical control fixtures are
complex synthetic spectra, not fabricated raw instrument FIDs; archived runs
exercise real decoding and FFT.

The ACME missing-solvent workflow explicitly uses **48 hours and 4 hours** on a
virtual clock. Both stages reach their configured deadlines, with timed fallback
recorded and chemical completion unverified. No target measurement is invented.
Existing no-retrievable/malformed-spectrum regressions also cover missing data.
Primary optimization/model failures cannot emit a false endpoint or dose;
optional comparison failures remain isolated. Dose reservation/repeat/uncertainty,
HOME known/UNKNOWN/restart/journal/unrelated-fault, dated output, backup corruption,
newer/conflicting records and configuration-before-transport regressions passed.

## H. Offline deployment readiness

Source/runtime portability is validated separately from physical commissioning.
An isolated source export was extracted without `.git`, with Git removed from
PATH, into a separately installed CPython 3.11 win-amd64 environment. All 27
pinned wheels were installed using `--no-index`; no model/package registry is
required. Source integrity, imports and real configuration preflight passed.
The guarded phase validator prohibits Python serial/socket connections and
Node HTTP/HTTPS/net/TLS/fetch calls, executes the five methods including real
local DEEP inference, and checks post-session comparisons in both stage folders.
Its duplicate fixture references are explicitly labeled offline validation.
Detailed exit statuses and package checks live in
`results/phase_hardening_isolated_final*.log`.

Final complete review-package output belongs in `dist/phase_hardening_release_final`.
Use its generated manifest and `.sha256` record to identify the exact artifact;
it includes wheels, installer/driver/toolchain resources and DEEP models/runtime.
Release extraction verification is recorded separately under
`results/phase_hardening_package_verification*`. Source identity is a working-tree
hash with the original parent commit; it must not be described as committed code.

Run from the extracted repository using its offline environment:

```powershell
.\.venv\Scripts\python.exe -B offline\verify_bundle.py
.\.venv\Scripts\python.exe -B scripts\verify_si6_environment.py --machine-config configs\machines\si6_real_COM4.yaml
.\.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
.\.venv\Scripts\python.exe -B scripts\validate_primary_phase_release.py --deep --output results\NEW_offline_phase_check
.\.venv\Scripts\python.exe -B scripts\validate_si6_offline.py --simulate
.\.venv\Scripts\python.exe -m pip check
```

These commands do not contact instruments. Choose a new output directory for
each validation. Use the included offline installer for a new environment;
the lock targets Python 3.11, not arbitrary installed Python versions. HOME
fresh-environment tests used 3.11.15; the packaged installer is 3.11.9 with the
same target ABI. Installer execution and USB drivers still need verification
on the target OS. Before live use, verify serial/RPC connectivity, physical HOME,
needle reach/solvent levels, pump calibration/delivery, actual spectra and
whole-cycle timing on the instrument laptop. No method is claimed fully
commissioned here. Git carries source only; transfer the complete ignored
binary payload to WORK/external drive as well.

## I. Documentation inventory

Current index: `SI6_START_HERE.md`. Detailed additions:
`SI6_NMR_PHASE_METHODS.md` (configuration, conventions, offline dependencies,
failure policy and findings), `RIG_STATE_BACKUP.md` (maintenance and recovery
boundary), and this report. Updated current operational references:
`SI6_FINAL_REAL_RUN_CHECKLIST.md`, `REAL_INSTRUMENT_RUN_COMMANDS.md`,
`SI6_EXPERIMENT_OUTPUTS.md`, `SI6_DEPLOYMENT_MANIFEST.md`,
`OFFLINE_DEPLOYMENT.md`, `OFFLINE_SETUP.md` and
`LIVE_COMMISSIONING_CHECKLIST.md`. Generic configuration, command, architecture,
quickstart, troubleshooting and validation guides now point to the current
entry or label legacy instructions. Existing HOME and solvent guides remain
the detailed references for their preserved behavior. Archived scientific
reports and datasets were not rewritten. Documentation tests check current
paths, links and the real recipe's ACME authority.

## J. Git review and handoff

Branch: `main`; working changes remain unstaged and uncommitted. Modified source
is limited to phase processing/auditing, workflow selection/snapshot/optional
reporting, maintained validation entry, release tooling, documentation and one
historical-preservation test adaptation. New source covers phase selection,
comparison, benchmark/report, validation adapters/fixtures, backup, inventory
and focused regression tests. `results/phase_hardening_git_review.json` records
the exact modified/new file list; generated results and binary payloads stay
ignored. Physical rig-state files and credentials are not staged.

Suggested commit: `Harden Si6 offline deployment and configurable quantitative NMR phasing`.
After reviewing the diff and artifacts, run these yourself:

```powershell
git status --short
git diff --check
git add -- .gitignore chemyx_lab configs/experiments/si6_real_two_stage.yaml docs offline scripts tests
git diff --cached --stat
git diff --cached
git commit -m "Harden Si6 offline deployment and configurable quantitative NMR phasing"
git push origin main
```

Do not force-add ignored wheels, models, benchmarks or physical state. After
your commit, build a clean committed release if desired:

```powershell
python scripts\prepare_si6_release.py --with-offline-payload --output-dir dist\NEW_committed_release
```
