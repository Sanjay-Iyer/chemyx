# Results

Layout (reorganized 2026-07-27). Raw data is never edited in place; each
processing run writes a new folder. Old/superseded runs live under `archive/`
(git-ignored at every depth) so current results are unambiguous.

```
results/
  raw/nmr/                     Raw NMReady JCAMP-DX (.dx) FIDs — inputs, do not edit
    06-08-26/                    reference/test series (7 files)
    06-09-26/                    PhSi2 flow series (8 files, 09:00-11:30)
  processed/nmr/               Current processed outputs (region peak analysis)
    06-09-26/                    <- CURRENT result for the 06-09-26 series
  analysis/nmr_reference_audit/
    06-09-26/                    <- CURRENT chemical-shift referencing audit
  archive/
    2026-07-27_nmr-reprocessing/ superseded runs, intermediate iterations, logs
  results_manifest.csv         provenance of the originally-preserved raw/processed data
  README.md                    this file
```

`raw/`, `analysis/`, and `processed/` are machine-local working trees and are
ignored by Git. Raw datasets and generated results may differ across laptops
without affecting pulls. Publish deliberately shared data through a separate
artifact store rather than the code repository. Per-machine paths belong in the ignored
`configs/nmr/analysis.local.yaml` file.

## Current results

**`processed/nmr/06-09-26/`** — region-based peak analysis of the 06-09-26 series.
- `peaks.csv` — every detected peak, per file (position, height, area, SNR, width, QC).
- `peak_families.csv` — peaks clustered across the series into reproducible families.
- `results.csv`, `summary.json` — run summary + full parameters/provenance.
- `plots/` — per-file and overlay spectra.

Key finding: **one reproducible peak family, `P001`, at median 5.785 ppm**
(range 0.011 ppm across 7 of 8 spectra; absent only at 09:00 before it forms).
Detected without a fixed target — the processor scans the 5.0-6.5 ppm region.

**`analysis/nmr_reference_audit/06-09-26/`** — evidence on absolute referencing.
The peak sits at ~5.78 ppm on the metadata-derived axis (reference **unverified**),
or ~6.0-6.1 ppm under a dilute-toluene convention (which fails both-region QC).
See `REVISED_REFERENCE_AUDIT.md`. Production keeps the unshifted metadata axis
until the solvent/standard identity is confirmed (fail-closed).

## Peak detection: region, not fixed target

The production processor (`scripts/nmr/process_fid.py`) finds peaks in a ppm
*range* and does not assume a fixed position — appropriate when the true shift
is unknown but consistent. Defaults (config `nmr_processing.peak_analysis` /
CLI `--region-min/--region-max`):

- region: **5.0-6.5 ppm**   - min SNR: 5   - min width: 0.015 ppm   - min separation: 0.04 ppm
- families tracked with 0.04 ppm tolerance; flagged reproducible if seen in >= 50% of spectra.

Regenerate the current result:

```bash
conda run -n ai python scripts/nmr/process_fid.py results/raw/nmr/06-09-26 --run-name 06-09-26
```

(The older `scripts/nmr/{plot_spectra,compare_timeseries,batch_report}.py` use a
fixed `target_ppm +/- window` instead — use those only for a known target.)

## Archive

`archive/2026-07-27_nmr-reprocessing/` holds superseded material moved out of the
way on 2026-07-27:
- `old-processed_06-08-26_pre-axis-fix/` — 06-08-26 outputs from before the ppm
  axis-sign fix (showed the mirrored ~6.1 peak); kept for reference only, invalid.
- `intermediate_06-09-26/` — earlier 06-09-26 processing iterations.
- `prior-analysis-archive/`, `prior-processed-archive/`, `validation-intermediate/`
  — consolidated from previously-scattered archive folders.
- `pytest-logs/` — test-run logs.

Everything under any `archive/` folder is git-ignored. Move disposable runs
there instead of deleting source data.
