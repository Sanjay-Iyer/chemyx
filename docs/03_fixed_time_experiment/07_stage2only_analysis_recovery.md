# 07 — Re-run full Stage 2 analysis on a selected folder

[Experiment index](00_START_HERE.md) | [Stage 2 run guide](06_stage2only_run_guide.md)

Use this standalone script if live processing failed, you want to retry analysis,
or you copied the raw data to another laptop. It analyzes saved Stage 2 spectra
only. It does not start an experiment, contact instruments, deliver a dose,
change HOME, or require the live-analysis reports to be healthy.

| Item | File |
|---|---|
| Python command | [07_analyze_stage2only.py](../../scripts/07_analyze_stage2only.py) |
| Recovery implementation | [stage2only_reanalysis.py](../../chemyx_lab/analysis/stage2only_reanalysis.py) |
| Existing processing settings | [analysis.yaml](../../configs/nmr/analysis.yaml) |
| Existing numerical processor | [process_fid.py](../../scripts/nmr/process_fid.py) |

## Select the data folder

Run from the repository root:

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\07_analyze_stage2only.py
```

A folder picker opens. Choose **one** of:

- The experiment's complete run folder, such as
  `results\stage2only\live\SI6_STAGE2ONLY_<id>`.
- Its `nmr_raw` folder.
- A copied folder containing only your Stage 2 raw `.dx` files, even if the
  original run reports and live-analysis files are missing or damaged.

If the Python installation cannot open the picker, the terminal asks you to
paste the folder path. Cancelling starts no processing.

You can also supply the directory directly:

```powershell
python scripts\07_analyze_stage2only.py "C:\data\my_stage2_run"
```

It also accepts a full two-phase run folder. When files have the experiment's
`stage_1_...` / `stage_2_...` names, it selects Stage 2 only. For an unlabelled
copied folder, it processes the raw `.dx` files directly inside that folder;
it does not recursively collect previous analysis outputs or unrelated folders.

## Full analysis and outputs

The script runs the existing production processor once on all usable saved
Stage 2 spectra together: Fourier processing, ACME phasing, baseline correction,
peak picking/integration, individual spectra, overlays, target-peak/time-series
plots, CSV tables and JSON summaries, according to the existing analysis YAML.
This is a fresh full-dataset analysis, independent of the background snapshots.

Every invocation creates a new folder:

```text
<run or selected data folder>/analysis_stage2only/<timestamp_unique-id>/
  ANALYSIS_SUMMARY.txt
  analysis_summary.json
  observation_inventory.csv
  acquisition_metadata.csv
  processor.log
  stage_2/
    plots/
    statistics/
    ...processed spectra, peak tables and processing summaries...
```

When you select `nmr_raw`, outputs are placed beside it under the parent run
folder. The console prints the complete output path. Existing raw data, run
JSON, instrument state, live-analysis output and previous analysis folders are
preserved. A duplicate `.rpc.dx` export can recover an observation whose primary
`.dx` export is damaged; the inventory records which file was used.

Files that cannot be decoded or have no authoritative JCAMP acquisition time
are recorded in the inventory and skipped; other usable Stage 2 files still
process. Timing uses the existing metadata hierarchy, preferring `LONG DATE`.
Filename schedule tokens and file modification times are never substituted.
Plots visibly identify the selected dataset. Missing spectra cannot be recreated.

If numerical processing still fails, read `processor.log` and
`ANALYSIS_SUMMARY.txt`. This wrapper uses the same numerical processor as live
review, so a persistent data/configuration problem may still need correction.
Analysis errors do not communicate with or stop an active instrument run.

## Optional output location or corrected settings

Use a separate writable output location, for example when the data drive is
read-only:

```powershell
python scripts\07_analyze_stage2only.py "E:\my_stage2_run" --output-dir "C:\data\stage2_analysis"
```

`--output-dir` is the parent for a new timestamped folder, not a folder to
overwrite. To use a corrected processing YAML:

```powershell
python scripts\07_analyze_stage2only.py "C:\data\my_stage2_run" --config configs\nmr\analysis.yaml
```

An optional `--dataset-display-name "My experiment"` overrides plot labels.
The processor has a one-hour timeout by default; `--timeout-seconds` changes
only this offline processing limit. No acquisition settings or instrument
configuration are required by this command.

Local verification on 2026-10-19: **17 tests passed**, including the folder-selection
paths, recovery with damaged reports, duplicate-export fallback, Stage 1 exclusion,
metadata timing, preservation of raw/live results, repeated outputs, processing
failures and documentation links. The real command-line processor produced plots
whose visible titles and plot manifests identify the selected dataset consistently
across saved formats. No instruments were contacted.
