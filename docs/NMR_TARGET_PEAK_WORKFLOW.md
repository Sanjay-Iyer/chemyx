# Focused 5.7 ppm NMR workflow

This is an offline decision-support and visualization workflow for the single
resonance labelled **5.7 ppm**. It does not connect to the Chemyx pump or NMR
instrument and does not change instrument-control behavior.

## Repository inspection and data flow

- Test data: `results/raw/nmr/06-09-26/` (eight NMReady JCAMP-DX FIDs).
- Main processor: `scripts/nmr/process_fid.py`.
- Existing publication statistics: `chemyx_lab.analysis.statistics_report` and
  `chemyx_lab.analysis.statistics_plots`.
- Focused metrics/completion: `chemyx_lab.analysis.target_peak_report` and
  `chemyx_lab.analysis.completion`.
- Focused figures: `chemyx_lab.analysis.target_peak_plots`.
- Configuration: `configs/nmr/analysis.yaml`, under `target_peak`.

The processor reads each JCAMP FID, applies the existing stored phase and ALS
baseline pipeline, detects regional peaks on the real trace, and retains the
processed quantitative spectrum. The focused report then applies the same fixed
5.70–5.90 ppm window to every spectrum. It does not reprocess or independently
reinterpret the FID.

### Area calculation

`chemyx_lab.analysis.time_series.integrate_fixed_region` calls
`integrate_above_local_baseline`. A line through the two integration-window feet
is subtracted, then both signed and positive trapezoidal areas are calculated.
The primary kinetic value is the positive fixed-window area, preserving the
previous validated numerical behavior and fixed boundaries across time.

### Uncertainty calculation

The area figure shows an approximate 95% measurement interval. Local noise is
estimated robustly from first differences; the area standard error is propagated
as `sigma * median_point_spacing * sqrt(number_of_points)`, and the displayed
interval is `area ± z(0.975) * SE`. Zero filling correlates adjacent points, so
this is a lower bound rather than a fully validated experimental confidence
interval. The exact method is recorded in every measurement row and the plot
manifest. Per-detected-peak residual-bootstrap intervals remain available in the
existing `peak_uncertainty.csv` table, but are not substituted for the fixed
window's uncertainty.

## Acquisition timing: exact source

The 06-09-26 timing is real acquisition metadata, not generated sample spacing.
Every source `.dx` file contains a JCAMP header such as:

```text
##LONG DATE=2026/06/09 09:13:16-0400
```

`scripts.nmr._common.parse_acquisition_timestamp` reads the `LONG DATE` metadata
field. `process_fid.py` stores the parsed timestamp and source, and
`chemyx_lab.analysis.time_series.elapsed_hours` subtracts the earliest timestamp.
The resulting hours are:

```text
0.0000, 0.9075, 1.2136, 1.4253, 1.6750, 1.9186, 2.1883, 2.3944
```

The fallback hierarchy is explicit and recorded per measurement:

1. JCAMP `LONG DATE` or `$DATE` acquisition metadata
2. JCAMP run metadata (`$TIMESTAMP`, `TIMESTAMP`, `TIME STAMP`)
3. `sequence-HHMM` in the filename
4. file modification time
5. measurement index, labelled as a fallback rather than represented as hours

Missing timestamps never cause equally spaced times to be invented. Duplicate
or non-increasing timestamps produce an undefined interval rate and a QC-safe
NaN.

### Filename schedule versus metadata acquisition time

The `sequence-HHMM` token in each filename is treated only as a nominal clock
label. For example, `sequence-1015` means nominal time 10:15. The actual event
compared here is the NMR acquisition timestamp recorded in the JCAMP `LONG DATE`
header; it is not a pump action time. The existing metadata hierarchy remains
authoritative, and timing-comparison plots fail closed when a row has only a
filename or file-modification-time fallback.

All timing figures use `target_peak_timing_comparison.csv`. It records
the nominal and metadata timestamps, their sources, both elapsed-time series,
and two complementary offsets:

```text
elapsed timing offset (min) = 60 * (actual elapsed h - nominal elapsed h)
clock-time offset (min) = metadata timestamp - filename nominal timestamp
```

Elapsed series are independently anchored to their first acquisition. This
makes schedule drift visible without confusing it with the initial difference
between 09:00 in the filename and 09:13:16 in metadata. The clock-time offset
retains that absolute per-acquisition difference.

The slide set contains eight views:

1. **Expected vs Actual Elapsed Time** overlays the two elapsed-time lines and
   uses subtle connectors to show their separation.
2. **Timing Offset by Acquisition** directly plots actual-minus-nominal elapsed
   minutes around a zero reference and is the clearest presentation summary.
3. **Filename Time vs Metadata Time** pairs nominal and actual clock markers in
   a dumbbell plot, making each acquisition's absolute mismatch explicit.
4. **Expected vs Actual Elapsed Time with Offsets** adds signed minute labels to
   the elapsed-time connectors while retaining the independently anchored
   relative-drift comparison.
5. **Absolute Timing Offset by Acquisition** plots the unnormalized
   metadata-minus-filename clock offset. Its first point is the true initial
   mismatch, not zero, so it directly shows how late or early every acquisition
   occurred relative to the filename clock label.
6. **Expected vs Actual Elapsed Time with Absolute Offset** overlays the
   independently anchored elapsed-time trends, shades the space between them,
   and labels each metadata point with its unnormalized clock-time offset. The
   first label therefore preserves the true initial mismatch even though both
   elapsed-time lines begin at zero.
7. **Filename vs Metadata Timing Test (V1)** plots nominal elapsed minutes
   against filename clock timestamps and actual elapsed minutes against
   metadata clock timestamps. This approved baseline remains unannotated.
8. **Filename vs Metadata Clock Time V2** preserves the V1 line coordinates,
   shades the region between the two line paths, and labels every metadata
   point with its absolute metadata-minus-filename clock offset.

The slide outputs for views 1, 4, 6, and 7 are retained under
`slides/archive/`. The V2 clock-time comparison remains active in `slides/`
and its filename omits the development suffix `_v2`.

Matching double-column paper figures are exported from the same comparison
table and plotting functions.

## Completion decision

The decision is replayed one acquisition at a time so the reported endpoint uses
only information that would have been available online. Defaults require:

- at least six observations and one hour elapsed;
- evidence of a meaningful earlier rise or fall;
- a four-point recent slope within both 5 area/hour and 10%/hour;
- three consecutive intervals with absolute change no more than 5%;
- acceptable recent peak/integration quality;
- for disappearance, final area no more than 10% of the run maximum (or a
  configured absolute threshold).

The returned status is one of `growing`, `decreasing`, `growth_plateau`,
`low_signal_plateau`, `stable`, `reversal`, `insufficient_data`, `poor_quality`,
or `unresolved`. The result includes direction, complete/incomplete, time,
reason, metrics, thresholds, warnings, and evidence level. A single point cannot
declare completion. Percent changes are suppressed when the preceding area is
below 5% of the run maximum.

For 06-09-26, the framework first identifies a growth plateau at 1.9186 h
(2026-06-09 11:08:23), using areas 38.49, 38.41, 40.25, and 40.49 in the recent
window. The recent slope is 3.35 area/hour (8.49%/hour). Evidence is **moderate**:
the slope estimate meets the configured thresholds, but its 95% interval
(-1.06, 7.75 area/hour) is wider than the equivalence band. Later points rise to
43.63 then fall to 34.42, so the static report adds a post-completion instability
warning. This is precisely why the rule remains configurable and requires
multi-experiment validation.

## Reproduce on the home laptop

From the repository root:

```powershell
conda run -n ai python scripts/nmr/process_fid.py results/raw/nmr/06-09-26 --run-name 06-09-26
```

If Conda is not on PATH, invoke the environment interpreter directly:

```powershell
C:\Users\iyer95\miniconda3\envs\ai\python.exe scripts/nmr/process_fid.py results/raw/nmr/06-09-26 --run-name 06-09-26
```

The command rebuilds `results/processed/nmr/06-09-26/`. All focused numerical
tables are under `statistics/`; figures are under `plots/target_peak/{slides,paper,qc}`.
Each plot is exported as 300 dpi PNG, SVG, and PDF. The target-peak plot manifest
links every figure to its CSV data, inputs, processing settings, thresholds,
configuration, and Git revision.

## Dataset-aware figure titles

Every saved experimental-data figure identifies its source dataset visibly;
the filename is not sufficient. A single-panel title follows
`<dataset display name> <descriptive title>`. A combined figure uses a
dataset-aware suptitle while keeping its panel titles concise.

The authoritative NMR display name is the existing `dataset_display_name` in
the `statistics` and `target_peak` sections of
`configs/nmr/analysis.yaml`. Plotting APIs receive that configured value
explicitly where practical. Shared, validation, and older compatible plotting
paths use `chemyx_lab.analysis.plot_titles.resolve_dataset_display_name`, whose
documented order is configured name, metadata, date-like identifier in an
input/output path, input dataset name, output run name, and finally the visible
fallback `Unspecified dataset`. The final fallback is deliberately visible and
cannot silently masquerade as a known dataset.

`format_dataset_plot_title` normalizes whitespace, preserves capitalization,
handles an empty descriptive title, and detects an existing prefix. Therefore:

```text
06-09-26 Focused Spectral Evolution   # correct
06-09-26 Area vs Time                 # correct
TEST-RUN-001 Completion Detection     # correct
Focused Spectral Evolution            # incorrect: dataset missing
06-09-26 06-09-26 Area vs Time        # incorrect: duplicate prefix
```

PNG, SVG, and PDF versions are produced from the same figure object and must
have matching titles. The target-peak plot manifest records both `dataset` and
`visible_title`. New plotting functions require a figure-object title test; a
new multi-panel function must also test its suptitle and layout margin.

## Recommended visual story

For slides, use the four numbered figures in `plots/target_peak/slides`: focused
spectra, absolute area, local rate, then the completion decision. The raw spectra
and raw areas remain visually dominant.

For a paper, use `paper/main_figure` (focused spectra, absolute area, local rate,
and decision state). Keep the normalized-area, interval-change, percent-change,
overlay/waterfall, and nonuniform-time heatmap figures as supplementary material.
Use the QC dashboard for analysis review, not as the main scientific figure.

Stage/cycle shading is supported only through explicit `target_peak.stages` YAML
entries. No stage boundaries are inferred for 06-09-26 because the dataset does
not contain reliable stage metadata.

## Limitations requiring more experimental data

- One run cannot validate the completion thresholds, false-stop rate, or
  generalization to disappearance steps.
- The final two acquisitions show instability after the candidate plateau.
- The uncertainty interval is a lower-bound propagation model, not replicate
  experimental uncertainty.
- The first spectrum's bounded candidate has low SNR and contacts the integration
  edge; its fixed-window area remains reported, but peak tracking fails QC.
- Metadata-only chemical-shift referencing is not an experimentally validated
  absolute reference. Dynamic tracking is bounded so it cannot chase an unrelated
  peak, but neighboring/overlapping resonances still require review.
- Peak asymmetry and fit residual are not yet primary gates; SNR, prominence,
  width, drift, baseline noise, integration-edge contact, and nearby-peak overlap
  are recorded now.
- Explicit multi-stage datasets are required before validating reversal and cycle
  overview behavior on real experiments.
