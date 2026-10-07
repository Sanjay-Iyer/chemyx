# Si6 tracked peak completion

> Historical evidence before the area-only update. Current live rules and QC separation are in [SI6_AREA_ONLY_IMPLEMENTATION.md](SI6_AREA_ONLY_IMPLEMENTATION.md) and [SI6_CONFIG_USER_GUIDE.md](SI6_CONFIG_USER_GUIDE.md).

Stage 1 and Stage 2 now use one authoritative `peak_area`: the existing production
peak finder's unrounded `positive_area`. The old completion integral has been
removed. Phase correction (including current stored-phase behavior), global ALS
baseline, regional polynomial baseline, interpolation, smoothing for detection,
and positive trapezoidal integration remain the existing production methods.
ACME selection or phase tuning is outside this change.

## Files and migration

- Complete workflow: `config_templates/experiments/si6_two_stage_nominal.yaml`.
- Same updated analysis block: `si6_two_stage_fast_sim.yaml`,
  `si6_two_stage_stage1_development.yaml`, `si6_two_stage_stage2_development.yaml`.
- On WORK, replace only the `analysis` block in your existing
  `configs/experiments/si6_run.local.yaml` with the updated template block.
  Keep the physical experiment ID, syringe settings, COM ports and acquisition settings.
  Old two-stage configs fail validation with a migration message; there is no fallback.
- Entry point: `scripts/02_si6_experiment.py`.
- Shared processing/phase/baseline: `scripts/nmr/process_fid.py`,
  `chemyx_lab/analysis/nmr.py`, `configs/nmr/analysis.yaml`.
- Filter schema: `chemyx_lab/analysis/peak_tracking.py`.
- Identity, retained evidence and quantitative QC: `analysis/stage_measurement.py`.
- Stage equations: `analysis/stage_completion.py`; reports: `analysis/si6_stage_reports.py`.

From the repository root, on WORK:

```powershell
python scripts/02_si6_experiment.py --workflow-config configs/experiments/si6_run.local.yaml
python scripts/02_si6_experiment.py --live --workflow-config configs/experiments/si6_run.local.yaml
```

Use the same existing machine/Arduino configuration arguments if your setup needs
explicit paths. For a new physical reaction, set a unique `workflow.experiment_id`.
An existing dose ledger continues to block replay. Do not restart a completed or
interrupted reaction under a new ID to evade that protection.

## Measurement and tracking

1. Acquire with fixed gain; take the authoritative timestamp from JCAMP `LONG DATE`.
2. Run the existing processor and retain lossless regional arrays and every candidate.
3. Apply enabled filters once. The initial peak is the eligible candidate nearest
   `nmr.target_ppm`, with SNR as a tie-breaker. Subsequent observations follow the
   nearest eligible peak to the preceding identified center. Stage 2 inherits the
   final Stage 1 identity, while its completion history starts afresh.
4. Reject displacement beyond configured previous/reference limits. Half one
   retained ppm grid spacing is allowed for interpolation/resolution uncertainty;
   actual shifts and grid resolution are recorded. A nearer credible peak rejected
   by width/shape cannot cause selection of a farther neighbor. An eligible peak
   matching a previously identified unselected neighbor also fails identity tracking.
5. Use the existing variable integration bounds: interpolated center plus/minus
   the candidate's measured width. Integrate positive unsmoothed corrected real
   intensity with the existing trapezoid routine. Retain signed and negative area
   for phase/baseline QC. No fitted-area replacement or normalization per spectrum.

   The exact quantity is `A = trapezoid(max(regional_quantitative, 0), ppm)`
   over sampled points inside `[interpolated_ppm - width_ppm, interpolated_ppm + width_ppm]`.
   The numerical candidate-generation minimum width is negligible; integration
   does not inherit the optional acceptance-width minimum.
6. Estimate robust noise from the retained corrected trace outside twice the widths
   of credible candidates. Require at least ten independent noise points.
   `U = noise_multiplier * noise * (actual right bound - actual left bound)`.
7. If the peak is undetected, a previous identity and positive initial QC reference
   are mandatory. Credible rejected/displaced candidates mean `TRACKED_PEAK_LOST`.
   Otherwise bound area by `(detection_height + noise_multiplier*noise)` times
   the previously identified dynamic width. Detection height is noise times the
   largest enabled SNR/prominence limit or the QC noise multiplier. Significant
   negative intensity in the expected dynamic interval fails phase/baseline QC.
   Require the upper bound to be below `undetected_max_fraction` of initial area.
   Report the midpoint of `[0, upper]` as a **censored estimate**, with uncertainty
   equal to that midpoint. It is never a measured zero or a fixed-band integral.
8. Apply noise, area uncertainty, phase, source hash and finite-data QC. A failed
   measurement follows the existing safe cleanup/inconclusive path; it cannot dose.

The profile processor flag also suppresses independent offline fixed-band
statistics/target replay reports during acquisition. They remain available through
standalone processing for retrospective comparisons, with their separate schemas.
Additional display-only residual flattening is also suppressed: it is not the
authoritative ALS/regional baseline and cannot use permissive noise candidates
as signal-exclusion bands. The quantitative baseline remains unchanged.

Changing width or unresolved multiplet structure changes the physical span measured.
These are tracked resonance areas in arbitrary intensity-times-ppm units, not
calibrated concentration or guaranteed whole-product conversion. Optional shoulder,
slope and shape checks are transparent screening heuristics, not deconvolution.

## Adjustable parameters

All filter rows are under `analysis.peak_tracking`. Each has an independent
`enabled` boolean. Disabled filters impose no acceptance threshold. Candidate
generation uses zero prominence and a numerical positive width floor of `1e-12`
ppm (one sampled point in SciPy); legacy processor QC is made permissive for this
profile, so it cannot override these settings. Separation is applied during
candidate generation. Phase/baseline and mandatory quantitative QC are separate.

| Filter | Default enabled | Parameters/defaults | Purpose |
|---|---|---|---|
| search | true | half_width_ppm: 0.20 | Broad identification region centered on nmr.target_ppm (5.8) |
| previous_shift | true | maximum_ppm: 0.08 | Continuity from preceding identified peak |
| reference_shift | false | maximum_ppm: 0.15 | Optional displacement from initial reference |
| continuity | true | enabled only | Reject substitution of known neighbors or farther peaks when the nearest credible candidate fails QC |
| snr | true | minimum: 5.0 | Quantitative height divided by noise |
| prominence | true | minimum_snr: 5.0 | Prominence divided by detection noise; replaces duplicated thresholds |
| absolute_prominence | false | minimum: 0.0 | Optional absolute prominence, independently adjustable |
| width | true | minimum_ppm: 0.015; maximum_ppm: 0.167 | Reject narrow artifacts/broad features |
| separation | true | minimum_ppm: 0.04 | Candidate minimum spacing; disable for closer resolved peaks |
| height | false | minimum: 0.0 | Optional absolute corrected height |
| area | true | minimum: 1.0e-12 | Positive quantitative candidate area |
| shoulder | false | maximum_asymmetry: 0.5 | Screen asymmetric left/right positive area within dynamic bounds |
| slope | false | minimum_flank_fraction: 0.05 | Both endpoints must fall this fraction below peak height |
| shape | false | minimum_interpolation_quality: 0.8 | Existing quadratic interpolation quality |
| neighbor | false | minimum_distance_widths: 2.0 | Screen nearby credible candidates in units of larger width |

Mandatory `analysis.measurement_qc`:

| Parameter | Default | Meaning |
|---|---|---|
| noise_multiplier | 3.0 | Conservative dynamic-area uncertainty/detection bound |
| max_noise_fraction | 0.01 | Noise / initial detected height |
| max_area_uncertainty_fraction | 0.005 | Dynamic uncertainty / initial detected area |
| undetected_max_fraction | 0.025 | Maximum censored upper area / initial area |

`analysis.area_epsilon = 1e-12` guards percentage diagnostics near zero.
Phase, line broadening, baseline and smoothing remain configured in
`configs/nmr/analysis.yaml`; this refactor does not change them.

## Stage equations and defaults

For a recent window, let `A` be tracked `peak_area`, `U` its uncertainty and `t`
actual acquisition hours. Let `S` be initial Stage 1 area for decreasing stages,
or largest observed Stage 2 area for increasing stages. Range is
`[max(A+U)-min(A-U)]/S`; slope is the linear fit of `A/S` against `t`.
Decreasing progress is `[A_initial-max(A+U)]/S`; increasing progress is
`[min(A-U)-A_initial]/S`. Every confirmation window must pass all criteria.

Edit `workflow.initial_stage.completion` and `first_addition_stage.completion`:

| Parameter | Stage 1 | Stage 2 | Meaning |
|---|---|---|---|
| trend | decreasing | increasing | Direction of progress |
| minimum_points | 8 | 8 | Minimum observations per confirmation end |
| minimum_duration_hours | 12 | 3 | Earliest eligible metadata elapsed time |
| window_points | 4 | 5 | Recent window length |
| consecutive_confirmations | 3 | 3 | Consecutive overlapping passing windows |
| minimum_progress_fraction | 0.95 | 0.25 | Required normalized decline/growth |
| low_fraction | 0.025 | absent | Stage 1 max(A+U)/S upper limit |
| max_window_range_fraction | 0.01 | 0.02 | Uncertainty-inclusive stability range |
| max_abs_slope_fraction_per_hour | 0.001 | 0.012 | Absolute normalized slope ceiling |
| expected_duration_hours | absent | 3 | Descriptive expectation, not an automatic endpoint |

Stage 2 additionally requires `min(A-U)/S >= 1-max_window_range_fraction`,
which prevents a decline to a low flat level from passing. A small bounded
Stage 2 baseline is allowed, but subsequent detected growth is required.
Minimum points and windows need enough configured measurement slots.
Intervals remain 120/30 minutes; maximum durations 48/6 hours. The sampling SOP,
Arduino checkpoints and single 1.8 mL channel-2 dose are unchanged.

## Outputs and verification

`time_series.csv`, each `stages/<stage>/time_series.csv` and
`final/full_time_series.csv` share the same `peak_area`. They include center,
previous/reference shift, height, SNR, prominence, width, actual area bounds,
noise, uncertainty, classification and candidate audit. Each processed acquisition
has `tracking_evidence.json` with all candidate values/rejection reasons/selection;
live orchestration adds endpoint evidence. Failure evidence records its state.
Stage evidence JSON lists failed criteria for each confirmation window.
Stage summaries report initial/current center and area, shifts, remaining fraction,
fraction of observed maximum, progress/range/slope and confirmation counts.
Area/time and peak-ppm/time PNG/SVG/PDF plots carry identical visible dataset
titles, with the same titles recorded in manifests. Times use LONG DATE only.

Offline tests cover production-processed amplitude/decline/growth, drift sequence
5.79/5.77/5.81/5.75/5.83, stronger neighbors, identity loss, filter toggles,
bounded nondetection versus QC failure, timestamp corruption, channel capacity,
dose replay, journal order and dataset titles. No physical instrument validation
or new chemistry calibration is claimed. The occurrence inventory alongside this
guide classifies retained independent report/legacy/historical integral references.

## Verified results (2026-10-07)

323 distinct tests passed across the targeted runs below. Initial failures were
fixed and their affected tests rerun; this is a combined result, not a claim of
one uninterrupted full-suite run. The transient Windows file-replacement failure
in a mock crash test also passed on rerun. Existing nmrglue/NumPy deprecation
warnings remain. No live hardware commands were executed.

| Test file | Passing tests |
|---|---:|
| `tests/test_si6_automated_nmr.py` | 5 |
| `tests/test_si6_monitoring.py` | 19 |
| `tests/test_si6_safety.py` | 50 |
| `tests/test_si6_pump_channels.py` | 33 |
| `tests/test_si6_profile.py` | 8 |
| `tests/test_si6_journal_integration.py` | 19 |
| `tests/test_nmr_processing.py` | 42 |
| `tests/test_nmr_phase_validation.py` | 12 |
| `tests/test_nmr_plot_titles.py` | 11 |
| `tests/test_si6_two_stage.py` | 32 |
| `tests/test_si6_measurement_qc.py` | 8 |
| `tests/test_si6_validator_gates.py` | 26 |
| `tests/test_si6_final_validator.py` | 18 |
| `tests/test_si6_synthetic_analysis.py` | 12 |
| `tests/test_si6_peak_tracking.py` | 23 |
| `tests/test_final_nmr_summary.py` | 5 |

The new 23-test module covers first/previous/reference selection, stronger
neighbors, nearest-target rejection, excessive shift, disabling continuity,
independent absolute/normalized prominence, SNR, width, height, area, search,
shoulder, slope, shape, neighbor and separation controls; unrounded moving areas;
drift without conversion; shared dataset titles; current profile CLI; disappearance
with a previously identified stronger neighbor; and persisted processing failures.

The full synthetic workflow completed Stage 1 at observation 14 and Stage 2 at
observation 16 under its labeled validation model. Journal order confirmed one
1.8 mL channel-2 dose after Stage 1 and before Stage 2. These are model-specific
software results, not physical reaction-duration predictions. The ordinary
validate-only command accepted the updated nominal template without opening
hardware. `git diff --check` passed.

## Remaining integral references

[Every matching repository source/config/doc line is classified in the inventory](audits/SI6_TRACKED_PEAK_FIXED_REFERENCE_INVENTORY.csv).
The inventory excludes ignored generated results, binary figures, raw DX data,
and its own generated CSV. It includes notebook and historical documentation
matches as well as active code/configuration matches. Live controller, stage
measurement, stage equations, stage reports and synthetic trajectories contain
no old completion-area signal or old measurement function call.

- `nmr.analyze_dx_peak`, legacy experiment YAML and the legacy two-instrument
  runner retain their original integration API. They are separate workflows.
- `statistics_report`, `target_peak_report`, their configs/plots and generic
  `time_series` integration support retain independent fixed-region measurements.
  The Si6 profile processor flag disables these retrospective reports during
  acquisition; standalone processing still supports them.
- Phase comparison/replay galleries, inspection/explorer scripts and notebooks
  retain retrospective fixed-region comparisons. They never authorize a live dose.
- Residual-baseline protected bands are baseline/display constraints, not endpoint
  integrals. Authoritative phase/ALS/regional processing is unchanged.
- Previous Si6 audits are explicitly marked historical. Their old field names,
  numbers and line links remain as a record of the earlier implementation.
- `si6_profile` mentions obsolete integration settings only to reject them at
  configuration admission. There is no integration fallback.

For a detected observation, Stage 1 decline/remaining fraction/slope/range and
Stage 2 growth/maximum/slope/range/completion all derive from the same existing
production `positive_area` stored as `peak_area`. A quantitatively qualified
nondetection uses the explicitly censored interval described above. Peak-position
plots omit those undetected observations; CSV/JSON label their center as last
known, rather than claiming a newly identified position.
