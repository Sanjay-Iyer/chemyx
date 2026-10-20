# 09 — Select a folder and run the primary SciPy Stage 2 analysis

[Experiment index](00_START_HERE.md) | [08 — SciPy Stage 2 run](08_stage2only_scipy_run_guide.md)

[Validation evidence](../90_reviews/04_stage2only_scipy_validation.md): real-data
parity, source preservation and failure-isolation checks.

Use this standalone command to process saved Stage 2 data with the **same 8σ
find_peaks / width-0.95 algorithm used by 08**:

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\09_analyze_stage2only_scipy.py
```

A folder picker opens. Select an experiment run folder, its `nmr_raw` folder,
or a copied folder containing the raw Stage 2 `.dx` files. If a folder picker
is unavailable, paste the directory in the terminal. An explicit path avoids
the picker:

```powershell
python scripts\09_analyze_stage2only_scipy.py "C:\data\my_stage2_run"
```

The command can also analyze data originally collected by 06 or the Stage 2
portion of a full two-phase run. Recorded `stage_1_...` files are excluded.
Primary/RPC duplicates are counted once, and a valid RPC export can recover a
damaged primary export. Prior failed analysis, broken run reports and missing
live-analysis JSON do not prevent recovery from usable raw data.

## What to open

Every invocation creates a fresh directory without overwriting earlier analysis:

```text
<run>/analysis_stage2only_scipy/<timestamp_unique-id>/
  ANALYSIS_SUMMARY.txt
  analysis_summary.json
  observation_inventory.csv
  acquisition_metadata.csv
  processor.log
  stage_2/
    README.md
    target_peak_measurements.csv       # PRIMARY: area is dynamic width-0.95
    processing_summary.json
    plot_manifest.csv
    plots/area_vs_time.png
    plots/000_latest_region.png
    plots/observations/*region_dynamic_width95.png
    statistics/target_peak_rates.csv
    statistics/target_peak_normalized.csv
    statistics/dynamic_kinetic_fits.csv
    integration_coordinates/
    fixed_window_comparison.csv        # comparison only
    secondary_statistics/              # general legacy comparisons
```

Start with **`plots/area_vs_time.png`**, then inspect the individual dynamic
integration PNGs and their quality flags. PNG/SVG/PDF versions carry the same
dataset-aware title. `integration_coordinates/` records the exact samples,
endpoint baseline and positive integrand represented by the shading.

The primary **`area`** column comes from ACME/ALS processing, prominence ≥8σ,
`peak_widths(rel_height=0.95)`, interpolated endpoint feet, and positive
trapezoidal integration. Signed area is recorded separately. `area_fixed` and
the fixed comparison CSV retain 5.70–5.90 ppm for comparison only. A missing
or invalid dynamic result is never replaced by fixed area or zero.

Ambiguous candidate areas are visible and flagged; quality-filtered fits and
plateau review exclude them. A successful numerical check does not establish
chemical identity or full theoretical peak capture. Analysis is for review:
the command never controls instruments, changes HOME, delivers a dose or stops
an active run. Numerical failures affect analysis output only.

Timing comes exclusively from JCAMP acquisition metadata, preferring LONG DATE;
filenames and file modification times are not substituted. Unusable exports
are recorded and skipped while other data are processed. Missing measurements
cannot be recreated.

## Files and optional settings

| Purpose | File |
|---|---|
| Folder-picker entry point | [09_analyze_stage2only_scipy.py](../../scripts/09_analyze_stage2only_scipy.py) |
| Folder inventory/recovery wrapper | [stage2only_scipy_reanalysis.py](../../chemyx_lab/analysis/stage2only_scipy_reanalysis.py) |
| Numerical CLI shared with 08 | [process_stage2only_scipy.py](../../scripts/nmr/process_stage2only_scipy.py) |
| Primary SciPy profile | [stage2only_scipy.yaml](../../configs/nmr/stage2only_scipy.yaml) |
| Shared processing settings | [analysis.yaml](../../configs/nmr/analysis.yaml) |

For a read-only source drive, choose a writable output parent:

```powershell
python scripts\09_analyze_stage2only_scipy.py "E:\my_stage2_run" --output-dir "C:\data\scipy_analysis"
```

`--config` selects shared Fourier/ACME/ALS settings; `--profile` selects the
separate SciPy profile, whose primary prominence and rel_height remain 8σ/0.95.
`--dataset-display-name` changes the visible dataset label. The analysis-only
timeout defaults to one hour and can be changed with `--timeout-seconds`.
No machine or instrument configuration is required for 09. Existing 06/07
scripts, their outputs, and the benchmark data remain unchanged.
