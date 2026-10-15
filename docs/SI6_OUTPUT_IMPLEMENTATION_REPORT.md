# Si6 output organization and HOME recovery — implementation report

Date: 2026-10-15. All execution on HOME used mocks, synthetic spectra or filesystem
tests. No hardware was contacted. Changes are local and unstaged; no commit/push
was performed for this task. The unfinished Arduino HOME recovery changes were
preserved and included in regression validation.

## A. Previous output structure

```text
results/runs/si6_two_stage/YYYYMMDD_HHMMSS_si6_live/
  config_snapshot.json, instrument_settings_snapshot.json, arduino_config_snapshot.json
  metadata/run_metadata.json
  operation_journal.jsonl, run_state.json, operator_status.json, time_series.csv
  raw_nmr/, processed_nmr/                 both stages mixed
  stages/stage_1/, stages/stage_2/          separate evidence/reports
  transition/channel2_addition.json
  final/, final_qc/, final_nmr_summary/    split final reporting
```

Main mocks previously used a sibling `_mock` root. The audit traced the main
entry point through acquisition, processing, evidence, reports, metadata and
recovery before code changes: [audit](SI6_OUTPUT_PATH_AUDIT.md).

## B. New output structure

```text
results/experiments/20261015_exp1/
  metadata/experiment_info.json, configuration_snapshot.yaml, software_version.json
  metadata/config_snapshot.json, instrument_settings_snapshot.json,
           arduino_config_snapshot.json, manifest.json
  logs/experiment.log, experiment_journal.jsonl, run_state.json, live_status.json
  step_1/nmr_raw/, nmr_processed/<acquisition>/
  step_1/analysis/time_series.csv, evidence/, solvent_evidence/, stage_completion.json
  step_1/analysis/qc_summary.csv, qc_summary.json, summary.json
  step_1/figures/, logs/operations.jsonl
  channel_2_dose/dose_record.json, operations.jsonl
  step_2/                                   same structure as step_1
  final_qc/summary.json, summary.csv, full_time_series.csv
  final_qc/qc_summary.json, qc_summary.csv, qc_report.md
  final_qc/experiment_summary.json, plot_manifest.json, figures/, nmr_summary/
```

Acquisition-processing figures remain with their processing evidence in the
correct step. Metadata-dependent figures are omitted when acquisition timing is
unavailable. Raw data is not duplicated into shared summaries. See the complete
[operator output guide](SI6_EXPERIMENT_OUTPUTS.md) for conditional artifacts.

## C. Repeated naming

The allocator exclusively creates `20261015_exp1`, `20261015_exp1_1`, then
`20261015_exp1_2`; numeric suffixes are execution identities. First-free gaps and
simultaneous allocation are tested. Existing directories/files are never reused.
The captured local start date/offset remains fixed across midnight.

Actual command-line mock evidence uses
`results/output_layout_cli/experiments/20261015_exp1` and its `_1`/`_2` siblings.
The first completed with 10 Step 1 acquisitions, 9 Step 2 acquisitions and one
confirmed dose. Both repeat attempts were refused before admission/acquisition,
and a SHA-256 inventory confirmed the first execution remained unchanged.
The existing live preflight can reject a reserved ID before output allocation;
suffix allocation applies to executions that reach the allocator. No preflight
safety check was removed to manufacture an additional live run folder.

## D. Configuration

Edit the existing recipe fields; retain the remainder of the YAML:

```yaml
workflow:
  experiment_id: exp1
output:
  run_root_dir: results/experiments
```

The main real recipe and default fast mock template use this root. Other explicit
roots remain supported. Relative paths, including backslashes, resolve against
the checkout; absolute roots also work. Accepted IDs retain their canonical ledger
spelling. Unsafe IDs are rejected, including Windows device names and traversal;
there is no silent lossy aliasing of physical reaction identities.

## E. Output integration

- `experiment_paths.py`: dated atomic allocation, stage mapping, historical path
  detection and small operational log projections.
- Existing `RunPaths`: compatible optional layout and per-stage views, retaining
  the experiment root for relative scientific provenance.
- Main orchestration: stage acquisition/retrieval/processing/CSV/spectrum/evidence
  paths, startup snapshots, journal/live status, protected intermediate receipt.
- Processing launch and both mock/synthetic processors: correct step-local
  outputs; shorter acquisition folder prefixes reduce Windows path length.
- Stage reports/final QC/final saved-data summary: step-local reports and shared
  final QC; missing areas remain unavailable and untimed plots are omitted.
- Metadata and offline inspection: original physical ID, execution suffix/full
  path, local/UTC times, source hashes/commit, config references/snapshots, stage
  end reasons, fallback and chemical verification; both journal layouts readable.
- Copied-checkout validator: reads the new summary path and models a separate
  mock rig per independent validator invocation.

Endpoint criteria, numerical NMR processing/phase algorithms, raw formats, dose
dispatch safety and prompt counts were not changed. Report handling of missing
areas was made tolerant so it can retain stage summaries without inventing zeros.

## F. Safety and recovery

The live canonical ledger remains `runtime/si6_doses/<original-experiment-ID>.json`.
Suffixes never change its key. Mock reservations are isolated from live history
but shared within their configured output root, so repeat execution cannot bypass
the same mock reaction's reservation. A reservation or uncertain dispatch still
refuses automatic replay. No partial-run resume was introduced. Old journals and
results remain in place and readable.

The main experiment retains one startup `y` and no later input/acknowledgment
flags. Standalone HOME now has one confirmation: known position moves absolutely
to zero; UNKNOWN position is established from operator-confirmed physical HOME
without motor movement. Communication, idle state and saved-record readback must
pass before position tracking is activated. A pending guard blocks startup after
interrupted recovery. Historical faults are retained; unrelated STOP/pump/dose
interlocks are not cleared. Details: [HOME guide](../arduino/docs/HOME_RECOVERY.md).

## G. Executed validation

**Latest result across 1,386 unique cases: 1,384 passed, 0 unresolved failures,
2 skipped.** Counts are deduplicated by test class/name, with final reruns replacing
the earlier result for the same case; they are not a sum of repeated tests.

| Executed run | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Full repository regression: `tests`, `arduino/tests`, commissioning tests | 1,376 | 4 initially | 2 |
| Output, two-stage and pre-deployment focused regression | 111 | 0 | 0 |
| HOME recovery, missing-data output, production NMR import isolation | 43 | 0 | 0 |
| Output and initial legacy-report compatibility rerun | 29 | 0 | 0 |
| Legacy blank-data report rerun | 1 | 0 | 0 |
| Actual saved-FID processing into the new Step 2 paths | 1 | 0 | 0 |
| Separate soak layout/receipt contract | 1 | 0 | 0 |
| **Final run: all 28 new output tests plus all 4 affected legacy cases** | **32** | **0** | **0** |

The four full-suite failures were older reporters accepting only `run_dir` and a
legacy root helper accepting only an output config. Their interfaces were restored
in production code; the original tests now pass unchanged. Numerical chemistry or
phase criteria were not weakened to pass tests. Earlier development runs also
identified stale path assertions and test-fixture assumptions; those were corrected.
The complete broad run took 33 min 24 sec; the final 32-case rerun took 89.79 sec.

The two skips are `test_completed_benchmark_paired_inputs_and_correct_metadata_control`
and `test_completed_figures_visible_identity_and_formats` in the optional synthetic
phase benchmark. Their recorded reason is that completed inference-job outputs are
required. Main experiment, HOME, solvent, cleanup and dose-protection tests ran.

Additional executed workflow checks:

- Full CLI mock from `C:\Users\iyer95` (outside the checkout): completed both
  stages, 10/9 acquisitions, one confirmed 1.8 mL Channel 2 dose, final summary
  under `final_qc/nmr_summary`, no automation prompts.
- Two actual repeat CLI attempts: `_1` and `_2` allocated; both refused at the
  original physical-ID reservation before acquisition. Original run hashes stayed
  unchanged. The integration test also verified the ledger hash stayed unchanged.
- Missing-data workflow with real production reporting: both stages reached their
  configured virtual deadlines; areas remained unavailable, stage evidence was
  retained and chemical completion stayed unverified. The broader solvent tests
  also covered full 48-hour/4-hour virtual fallback, recovery, malformed/missing
  acquisitions and transport failures.
- Independent spectrum-driven synthetic validator: PASS for both validation levels,
  counterexamples and the complete shared area-only workflow, one protected dose
  before Stage 2. Its existing spectral-validation scenario extends only the virtual
  Stage 2 test deadline to 12 hours; the real recipe remains 4 hours.
- Copied-checkout offline mock: PASS without Git/network/hardware. Output tests
  also verified foreign working directories, a copied deployment root, Windows
  separators/spaces and a temporary results root.
- Naming verified October 15 `exp1`, `_1`, `_2`, `exp2`, October 16 `exp1`, existing
  gaps/files, unsafe IDs and 24 simultaneous atomic allocations.
- HOME's 41 dedicated cases passed, including known 0/1, UNKNOWN without motor
  movement, decline, program restart, readback/journal failures, historical fault
  preservation, unrelated faults and subsequent strict startup. The earlier full
  HOME recovery mock also completed both stages with one HOME confirmation,
  zero automation prompts and one dose, preserving its original fault journal.

Local evidence (generated/ignored, not staged):

```text
results/output_validation_aggregate.json
results/output_layout_regression_1.xml
results/output_paths_final_verified.xml
results/output_missing_home_final.xml
results/output_final_compatibility.xml
results/output_layout_cli.log
results/output_layout_cli_repeat_report.json
results/output_layout_copied_validation.log
test_tmp_offline_validation/copied_directory_report.json
results/output_layout_spectral_validation/SUMMARY.json
results/output_layout_spectral_validation/REPORT.md
results/home_recovery_full_workflow_report.json
```

`git diff --check` passed with repository settings. Protected historical results,
archives, firmware and NMR configuration have no tracked changes. No physical
instrument performance, chemistry yield or real threshold commissioning is claimed
by these software-only checks.

## H. Documentation

Updated directly affected guides: `REAL_INSTRUMENT_RUN_COMMANDS.md`,
`SI6_FINAL_REAL_RUN_CHECKLIST.md`, `SI6_SOLVENT_FALLBACK_UPDATE.md`,
`SI6_START_HERE.md`, `RESULTS_AND_DATA.md`, `SI6_PROMPT_AUDIT.md`, and
`arduino/README.md`. Added this report, `SI6_EXPERIMENT_OUTPUTS.md`,
`SI6_OUTPUT_PATH_AUDIT.md`, `REPOSITORY_ORGANIZATION_REVIEW.md` and
`arduino/docs/HOME_RECOVERY.md`. The latter HOME documentation was already in
progress when the output task arrived.

## I. Whole-repository recommendations

The full [repository review](REPOSITORY_ORGANIZATION_REVIEW.md) records concrete
findings, affected files, priorities and migration risks. Five valuable follow-ups:

1. Move executable validation dependencies out of generated `results` only through
   a separately tested compatibility migration.
2. Add a verified backup/restore inventory for canonical rig state and dose history.
3. Make production Arduino tests part of explicit default/CI discovery.
4. Establish one current operator/documentation index and label historical guides.
5. Unify release resource inventories and extracted-payload verification.

General reorganization was reviewed only. No unrelated source, historical data,
archives, commissioning directories or scientific algorithms were moved/deleted.

## J. Git readiness

Branch: `main`. HEAD remains `07a994b2d22b9b71daf7cdf4fce7fa5080b88193`.
All changes below are **unstaged**. The index is empty; no generated output,
physical-state record or secret was staged. No tracked historical data was changed
or deleted. There are no other pre-existing unrelated modifications.

Preserved work already present when the output request arrived: Arduino README,
needle state/control, HOME helper/guide/tests, the final checklist, prompt audit,
solvent guide and pre-deployment tests. Some of those files also received output
updates. Nothing was reset or discarded.

Final inventory: **25 modified files; 9 new files**. The explicit
list below is both the review inventory and the exact staging command; comments
separate tracked modifications from new files. Review before running it yourself.

Recommended commit message: `Simplify safe HOME recovery and organize Si6 experiment outputs`.

```powershell
Set-Location -LiteralPath 'C:\code\chemyx_pump'
git status --short
git diff --check
$files = @(
    # Modified tracked files
    'arduino/README.md'
    'arduino/python/needle_state.py'
    'arduino/scripts/needle_control.py'
    'chemyx_lab/analysis/final_nmr_summary.py'
    'chemyx_lab/analysis/si6_final_qc.py'
    'chemyx_lab/analysis/si6_stage_reports.py'
    'chemyx_lab/recovery.py'
    'chemyx_lab/run_metadata.py'
    'chemyx_lab/testing/si6_synthetic_analysis.py'
    'chemyx_lab/workflows/dose_guard.py'
    'chemyx_lab/workflows/si6_automated_nmr.py'
    'chemyx_lab/workflows/si6_simulation.py'
    'chemyx_lab/workflows/three_instrument_si6.py'
    'config_templates/experiments/si6_two_stage_fast_sim.yaml'
    'configs/experiments/si6_real_two_stage.yaml'
    'docs/REAL_INSTRUMENT_RUN_COMMANDS.md'
    'docs/RESULTS_AND_DATA.md'
    'docs/SI6_FINAL_REAL_RUN_CHECKLIST.md'
    'docs/SI6_PROMPT_AUDIT.md'
    'docs/SI6_SOLVENT_FALLBACK_UPDATE.md'
    'docs/SI6_START_HERE.md'
    'scripts/validate_si6_offline.py'
    'tests/test_si6_predeployment.py'
    'tests/test_si6_solvent_fallback.py'
    'tests/test_si6_two_stage.py'
    # New files
    'arduino/docs/HOME_RECOVERY.md'
    'arduino/python/home_recovery.py'
    'arduino/tests/test_home_recovery.py'
    'chemyx_lab/experiment_paths.py'
    'docs/REPOSITORY_ORGANIZATION_REVIEW.md'
    'docs/SI6_EXPERIMENT_OUTPUTS.md'
    'docs/SI6_OUTPUT_IMPLEMENTATION_REPORT.md'
    'docs/SI6_OUTPUT_PATH_AUDIT.md'
    'tests/test_experiment_output_paths.py'
)
git add -- $files
git diff --cached --check
git diff --cached --stat
git commit -m "Simplify safe HOME recovery and organize Si6 experiment outputs"
git push origin main
```

These commands are provided for the operator. They were **not executed** by this
task. Existing offline ZIPs were not rebuilt and do not contain these local changes.
