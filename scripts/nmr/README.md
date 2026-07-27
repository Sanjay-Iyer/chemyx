# NMR analysis scripts

Standalone tools for analysing NMReady JCAMP-DX (`.dx`) spectra. They read the
core parser in `chemyx_lab/analysis/nmr.py` but are otherwise self-contained.

## Where data goes / where results go

- **Input**: point a script at a folder of `.dx` files (or individual files).
  The conventional home is `results/raw/nmr/<run>/`, but any path works — it's
  an argument, nothing is hardcoded. Directories are searched recursively.
- **Output**: each run creates one timestamped folder under
  `results/analysis/`, **named after the input** so results are traceable, e.g.
  input `results/raw/nmr/06-08-26/` → `results/analysis/06-08-26_<timestamp>_timeseries/`.
  Nothing is ever overwritten (collisions get `_2`, `_3`, …). Each folder holds
  `results.csv`, `summary.json`, and a `plots/` directory.

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

```bash
# Visualise the actual spectra (not just statistics)
python scripts/nmr/plot_spectra.py results/raw/nmr/06-08-26/
```

`plot_spectra.py` writes per-file plots split into `plots/zoom/<file>.png`
(the target region, y-axis scaled to the in-window peak) and
`plots/full/<file>.png` (whole spectrum), plus `overlay_full.png`,
`overlay_target.png`, and `stacked_target.png`.

## Common parameters (`common:`)

| Key | Meaning |
|-----|---------|
| `target_ppm` | Peak position to track (ppm) |
| `window_ppm` | Detection half-window around the target (ppm) |
| `output_dir` | Base directory for result folders |
| `run_name` | Fixed output-folder name; `null` = named after input |
| `plots` | Generate PNG plots |

Per-script keys: `analyze_single.plot_window_ppm`,
`compare_timeseries.plateau_threshold_pct` / `plateau_consecutive`,
`plot_spectra.zoom_window_ppm` / `normalize_overlay`.

## Running

From the repository root, with numpy/scipy/matplotlib/PyYAML installed. On the
personal laptop the env is conda `ai`:

```bash
conda run -n ai python scripts/nmr/plot_spectra.py results/raw/nmr/06-08-26/
```

Tests: `python -m pytest tests/test_nmr_scripts.py -q`
