# NMR analysis scripts

For the focused 5.7 ppm kinetics, completion-decision logic, slide/paper figure
sets, and exact acquisition-time provenance, see
[`docs/NMR_TARGET_PEAK_WORKFLOW.md`](../../docs/NMR_TARGET_PEAK_WORKFLOW.md).

Standalone tools for analysing NMReady JCAMP-DX (`.dx`) spectra. They read the
core parser in `chemyx_lab/analysis/nmr.py` but are otherwise self-contained.

## No-argument, per-machine processing

Copy `configs/nmr/analysis.local.example.yaml` to
`configs/nmr/analysis.local.yaml`, then set that laptop's `input.paths` and
`output.directory`. The local file is ignored by Git and automatically merged
over the shared `configs/nmr/analysis.yaml` settings. Routine processing is:

```powershell
python scripts/nmr/process_fid.py
```

Generated `results/analysis/` and `results/processed/` trees are ignored by
Git. They may differ across laptops without blocking `git pull`.

On this laptop, install the analysis dependencies in the `ai` environment:

```powershell
conda activate ai
python -m pip install -r requirements.txt
```

`nmrglue` is required for JCAMP-DX decoding and NMR processing.

## Where data goes / where results go

- **Input**: set `input.paths` in the ignored `analysis.local.yaml` for routine
  processing. A command-line path remains available for one-off runs.
  Directories are searched recursively.
- **Output**: each run creates one timestamped folder under
  `results/analysis/`, **named after the input** so results are traceable, e.g.
  input `results/raw/nmr/06-08-26/` → `results/analysis/06-08-26_<timestamp>_timeseries/`.
  Nothing is ever overwritten (collisions get `_2`, `_3`, …). Each folder holds
  `results.csv`, `summary.json`, and a `plots/` directory.
- **Grouping** (optional): outputs can be filed under a sample-type sub-folder.
  With `group_tokens: [PhSi2, PhSi4, PhSi6]` in the config, a run whose input
  files are named `CEC-PhSi4-flow(...).dx` lands under
  `results/analysis/PhSi4/<input>_<timestamp>_<script>/`. So all your PhSi4 runs
  sit together, each still in its own traceable per-run folder. Set `group:` (or
  `--group PhSi4`) to force a fixed sub-folder; no token match ⇒ no sub-folder.

## Configuration — edit one file, not flags

All parameters live in [`configs/nmr/analysis.yaml`](../../configs/nmr/analysis.yaml).
Edit that file to change settings between runs; you normally pass **no flags**:

```bash
python scripts/nmr/compare_timeseries.py results/raw/nmr/06-08-26/
```

The file has a shared `common:` section plus one section per script. Precedence
is: **built-in default < config file < command-line flag**, so any value can
still be overridden for a one-off run:

```bash
python scripts/nmr/compare_timeseries.py results/raw/nmr/06-08-26/ --target 5.8
```

Use `--config path/to/other.yaml` to load a different config entirely.

## The scripts

| Script | Purpose | Config section |
|--------|---------|----------------|
| `analyze_single.py` | One file, deep peak report + review plot | `analyze_single` |
| `compare_timeseries.py` | A reaction over time: growth, deltas, plateau detection | `compare_timeseries` |
| `batch_report.py` | Many files: stats, rankings, comparison plots | `batch_report` |
| `plot_spectra.py` | **See the spectra**: per-file (full + zoom), overlay, and stacked waterfall, with the detected ~target peak marked | `plot_spectra` |
| `process_fid.py` | Conventional nmrglue processing and phase-corrected real-spectrum plots | command-line options |

```bash
# Visualise the actual spectra (not just statistics)
python scripts/nmr/plot_spectra.py results/raw/nmr/06-08-26/
```

`plot_spectra.py` writes per-file plots split into `plots/zoom/<file>.png`
(narrow: `target_ppm ± zoom_window_ppm`), `plots/zoom_wide/<file>.png`
(absolute `zoom_wide_min_ppm`..`zoom_wide_max_ppm`, default 5–7 ppm), and
`plots/full/<file>.png` (whole spectrum) — each with the y-axis scaled to the
visible peak — plus `overlay_full.png`, `overlay_target.png`, and
`stacked_target.png`.

## Regional discovery and phase-corrected real spectra

`process_fid.py` now discovers every QC-passed peak from 5.0 to 6.5 ppm by
default. It does not force a result at 6.1 ppm. Detection uses a
phase-insensitive magnitude diagnostic; height and area use the phase-corrected
real spectrum.

```powershell
python scripts/nmr/process_fid.py results/raw/nmr/06-09-26 `
  --run-name 06-09-26_region-peaks
```

The reproducible processing order is:

1. `nmrglue.jcampdx.read` decodes and scales the split `PAGE=N=1` real and
   `PAGE=N=2` imaginary NTUPLES arrays.
2. The arrays are combined as `real + 1j * imaginary`.
3. Exponential apodization uses the DX `$LB` value unless overridden.
4. The FID is zero-filled to `$SI` unless overridden.
5. `nmrglue.proc_base.fft` creates the frequency-domain spectrum.
6. `$PHC0` and `$PHC1` are applied in the NMReady-compatible inverse direction.
7. The ppm axis is built from `$SWH`, `$SF`, and `$O1P` and plotted high-to-low.
8. An iteratively clipped regional polynomial removes solvent-tail curvature.
9. Peaks must meet prominence, minimum-separation, minimum-width, positive-area,
   and time-series reproducibility checks.

Outputs go under `results/processed/nmr/<run>/` and include full plots,
5.0--6.5 ppm plots, corrected overlay and stack plots, `results.csv`,
`peaks.csv`, `peak_families.csv`, and `summary.json`. Every result records the
raw SHA-256, Git commit, dependency versions, processing parameters, original
ppm, optional referenced ppm, and reference/alignment shifts. Add
`--export-csv` only when point-wise spectra are needed; normal `$SI=65536`
exports are large.

Fixed-target analysis remains available through `analyze_single.py`,
`compare_timeseries.py`, and `plot_spectra.py`.

### Dataset-aware plot titles

Every NMR figure visibly identifies the dataset. The authoritative display name
is configured as `statistics.dataset_display_name` and
`target_peak.dataset_display_name` in `configs/nmr/analysis.yaml`. Generic and
legacy-compatible helpers that do not receive those sections resolve metadata,
the input dataset directory, and then the output run directory through
`chemyx_lab.analysis.plot_titles`. Single-panel figures use
`<dataset display name> <descriptive title>`; multi-panel figures use the same
convention in a suptitle. The helper prevents duplicate prefixes, and PNG, SVG,
and PDF exports share the same title.

### Nominal and actual acquisition timing

For timing comparisons, `sequence-HHMM` in a filename is the nominal schedule
label and JCAMP `LONG DATE` is the authoritative actual NMR acquisition time.
The focused workflow writes `target_peak_timing_comparison.csv` and timing
figures for overlaid elapsed times, relative elapsed-time drift, paired clock
times, labeled elapsed-time gaps, and the absolute metadata-minus-filename
clock offset. A shaded timeline comparison also combines both elapsed-time
trends with absolute clock-offset labels. The absolute labels are not
normalized, so the first acquisition retains the true initial mismatch.
Filename or modification-time fallbacks are never presented as metadata
timing.

The promoted clock-time comparison keeps a plain two-line V1 in which each
series uses its own timestamps. V2 preserves those coordinates and adds only a
light shaded gap plus absolute offset labels at the metadata points.
Superseded slide variants are generated under `slides/archive/`; the active
V2-derived clock-time figure is generated in `slides/` without `_v2` in its
filename.

### Template-derived optional methods

The repository's `nmr_template/dx_process.py` motivated several explicit
options. They are not default because each changes peak shape or referencing:

```powershell
# One-sided Hanning-like taper plus nmrglue peak-minima autophase
python scripts/nmr/process_fid.py results/raw/nmr/06-09-26 `
  --truncation-window half-cosine --phase-method automatic_peak_minima

# ABD-selected linear baseline points (128 sections, 3x noise, 60-point window)
python scripts/nmr/process_fid.py results/raw/nmr/06-09-26 `
  --baseline-method polynomial --abd-sections 128 `
  --abd-noise-factor 3 --abd-window-points 60

# Explicit toluene methyl referencing, cross-checked against the aromatic envelope
python scripts/nmr/process_fid.py results/raw/nmr/06-09-26 `
  --solvent toluene --solvent-resonance methyl `
  --solvent-validation-resonance aromatic --export-csv

# User-declared reference peak with fail-closed QC
python scripts/nmr/process_fid.py results/raw/nmr/06-09-26 `
  --reference-method validated_peak `
  --reference-expected-ppm 7.00 `
  --reference-search-window-ppm 0.40 `
  --reference-minimum-snr 10 `
  --reference-minimum-prominence-snr 8 `
  --reference-minimum-width-ppm 0.002 `
  --reference-maximum-width-ppm 0.30 `
  --reference-maximum-shift-ppm 0.20
```

The half-cosine is exactly
`0.5 + 0.5*cos(pi*x/n)` for `x=1..n`; it is one-sided, not NumPy's symmetric
Hann. ABD's noise level is the 5th percentile of 128 section peak-to-peak spans
instead of the fragile absolute minimum. Variable-width integration joins the
two selected peak feet with a line and reports both signed real area and
positive-clipped area.

Solvent referencing is never implicit. It searches near a named resonance and
requires minimum SNR, prominence, plausible linewidth, and a bounded shift.
It rejects the correction when a second resonance disagrees. On rejection the
production workflow retains the metadata axis and records the error. Both
`original_ppm` and `referenced_ppm` are exported when pointwise CSV output is
enabled.

Available production modes are:

- phase: `stored`, `automatic_peak_minima`, `none`, or `manual`;
- baseline: `asymmetric_least_squares`, `polynomial`, or `none`;
- reference: `metadata`, `validated_peak`, `manual_shift`, or `none`.

Source-qualified, multi-region reference hypotheses are also available through
`--reference-model`. They fit methyl and aromatic regions independently and
record both `proposed_shift_ppm` and `applied_shift_ppm`. The model fails
closed—and leaves the metadata axis unchanged—unless the user declares a
matching solvent identity/isotopic form and both regions pass agreement QC:

```powershell
python scripts/nmr/process_fid.py results/raw/nmr/06-09-26 `
  --reference-model protonated_toluene_low_field_neat `
  --solvent-identity protonated_toluene `
  --solvent-isotopic-form h8
```

Do not supply those identity flags merely to make a model pass; they must come
from the sample-preparation record or another independent source.

Stored phase, metadata referencing, no normalization, and full 5.0--6.5 ppm
regional discovery are the defensible defaults. ALS remains optional because
it can absorb broad resonances; the regional/polynomial approach is retained
as the current quantitative default.

### Independent audit

```powershell
python scripts/nmr/validate_processing.py results/raw/nmr/06-09-26 `
  --run-name 06-09-26_scientific-audit
```

This creates `SCIENTIFIC_AUDIT.md`, axis/decoder/reference/phase/window and
processing-sensitivity CSVs, and diagnostic plots under
`results/analysis/nmr_validation/<run>/`.

For a focused comparison against the supplied processed-spectrum image:

```powershell
python scripts/nmr/audit_reference_models.py results/raw/nmr/06-09-26 `
  --pdf-image path/to/processed-spectrum-screenshot.png
```

This produces a 14-section `REVISED_REFERENCE_AUDIT.md`, per-region reference
fits, relative-frequency checks, PDF-coordinate comparisons, and 15 diagnostic
figures. Candidate axes are labeled diagnostic and are never applied to the
production data when sample identity remains unknown.

### Exact legacy reproduction and A-D comparison

```powershell
python scripts\nmr\compare_legacy_processing.py `
  results\raw\nmr\06-09-26 `
  --run-name 06-09-26_legacy-comparison
```

This diagnostic writes the exact June 9 reference shifts, an A-D processing
table, true-integral versus point-sum zero-fill comparison, five overlays,
`LEGACY_AUDIT.md`, and `LEGACY_SOURCE_AUDIT.md`. The implementation is isolated
in `chemyx_lab.analysis.nmr_legacy` and labeled:

```text
legacy_reproduction_only
not_recommended_for_quantitative_analysis
```

It deliberately preserves the old `fft(data)` bug, maximum normalization,
complex-to-real ABD behavior, and first-peak-above-0.9 reference heuristic so
historical results can be explained. It is never used by the production path.

## Common parameters (`common:`)

| Key | Meaning |
|-----|---------|
| `target_ppm` | Peak position to track (ppm) |
| `window_ppm` | Detection half-window around the target (ppm) |
| `output_dir` | Base directory for result folders |
| `run_name` | Fixed output-folder name; `null` = named after input |
| `plots` | Generate PNG plots |
| `group` | Fixed output sub-folder; `null` = auto-detect from `group_tokens` |
| `group_tokens` | Tokens matched in input filenames to pick a sub-folder (e.g. `[PhSi2, PhSi4, PhSi6]`) |
| `line_broadening_hz` | Exponential broadening in Hz; `null` uses DX `$LB` |
| `zero_fill_points` | Final FFT size; `null` uses DX `$SI` |
| `min_prominence_snr` | Minimum baseline-corrected prominence/noise ratio |
| `baseline_window_ppm` | Half-width of the local polynomial baseline fit |
| `baseline_polynomial_order` | Local baseline polynomial order |

Per-script keys: `analyze_single.plot_window_ppm`,
`compare_timeseries.plateau_threshold_pct` / `plateau_consecutive`,
`plot_spectra.zoom_window_ppm` / `normalize_overlay`.

## Running

From the repository root. On the personal laptop use conda `ai`:

```powershell
conda activate ai
python -m pip install -r requirements.txt
python scripts/nmr/process_fid.py results/raw/nmr/06-09-26
```

On the instrument-connected work laptop, update and use conda `llm`:

```powershell
Set-Location C:\code\chemyx_pump
conda activate llm
git pull
python -m pip install -r requirements.txt

python scripts\nmr\process_fid.py results\raw\nmr\06-09-26 `
  --region-min 5.0 --region-max 6.5 `
  --run-name 06-09-26_region-peaks

python scripts\nmr\validate_processing.py results\raw\nmr\06-09-26 `
  --run-name 06-09-26_scientific-audit

python -m pytest -q
python -m ruff check .
git diff --check
```

These are offline file-processing commands. They do not connect to or command
the NMR, pump, robot, or any other live hardware.
