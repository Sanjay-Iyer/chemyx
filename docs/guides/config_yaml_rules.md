# Repo rule: Python scripts are configured by YAML, not by flags

Status: active convention. Applies to every runnable script under `scripts/`.

A run should be reproducible from a file that lives in git, not from a command
line someone typed once and lost. Long flag strings are unreviewable, easy to
mistype, and invisible after the fact. So:

> **Every input and every tunable parameter of a script lives in a YAML config
> file. A fully configured run takes no arguments at all.**

The goal is that this works:

```bash
python scripts/nmr/process_fid.py
```

and that naming an input on the command line overrides the configured one for
a one-off, without editing the file:

```bash
python scripts/nmr/process_fid.py results/raw/nmr/06-08-26/
```

If a run needs a flag to be correct, that parameter is missing from the YAML
and should be added.

---

## The rules

### 1. Precedence is fixed

```
built-in default  <  YAML config file  <  command-line flag
```

Built-in defaults are the last resort, the YAML is the normal control surface,
and a flag is a deliberate one-off override. A flag must never be *required*.

### 2. Built-in defaults must be complete

Every parameter carries a working default in Python. Deleting the config file
must leave the script runnable, not broken. This is what keeps the repo usable
from a clean clone on the work laptop (`docs/QUICKSTART.md`) without carrying
machine-specific state around.

Corollary: config files are for *parameters*, never for machine-specific
addresses or secrets. Those follow the existing pattern in
`docs/CONFIGURATION.md` — a git-ignored `*.local.yaml` copied from a checked-in
`*.example.yaml`.

### 3. A missing default config is fine; a missing named one is an error

If `--config` is not passed and the default file is absent, fall back to
built-in defaults silently. If the user explicitly named a config path and it
does not exist, raise `ConfigError` — never silently ignore what they asked
for.

### 4. Unknown keys are a hard error

Config parsing is fail-closed. A typo like `min_peak_widht_ppm` must stop the
run with a message listing the allowed keys, never be silently ignored. A
silently-dropped key produces results that look fine and are wrong.

This means **adding a key to the YAML requires adding it to the script's
allow-list in the same commit.**

### 5. One shared YAML, and every script must tolerate the whole file

`configs/nmr/analysis.yaml` is read by several scripts. Each consumes its own
sections and must *ignore* the rest without erroring. When you add a section,
add its name to `_SECTION_ALLOWLIST` equivalents in every loader that reads
that file — currently `allowed_sections` in
[`scripts/nmr/_config.py`](../../scripts/nmr/_config.py). Forgetting this
breaks sibling scripts, not the one you are editing, so the failure shows up
somewhere unexpected.

### 6. argparse `type=` does not apply to config values

`parser.set_defaults(**config_defaults)` bypasses argparse's `type=`
conversion. A path read from YAML stays a `str` and will fail later on
`Path` operations. Coerce explicitly when loading — see the `output_dir`
handling in `_config_defaults`.

Same trap for anything else `type=` would have converted: ints, floats, and
`Path`s all arrive as whatever YAML parsed them into.

### 7. Config is provenance — record what was actually used

Every run writes its fully-resolved parameters into `summary.json` alongside
the git commit and dependency versions. The config file may change after the
run; the record of what produced a given result must not.

### 8. Comment the YAML, not the argparse help

The YAML is what a person reads when deciding what to change. Units, valid
choices, and the reason a value is what it is belong there, next to the value.

### 9. Tuned thresholds get a master switch

Where a section holds *calibrated* values — thresholds validated against known
data, as opposed to ordinary settings — pair them with a
`use_manual_thresholds`-style boolean. `false` means the built-in defaults win
and the values in the file are ignored; `true` means the file's values are
used. This lets someone experiment without permanently losing the validated
numbers, and makes "am I on the tuned values or my own?" answerable by reading
one line.

Two details that make it work: the values ship *equal* to the built-in
defaults, so flipping the switch on changes nothing until a value is actually
edited; and keys are still validated when the switch is off, so a typo is
caught immediately rather than the first time someone enables it. The active
thresholds are printed at the start of each run and recorded in `summary.json`.

### 10. The shipped config is covered by a test

`configs/nmr/analysis.yaml` must parse in CI
(`tests/test_nmr_statistics.py::test_repository_analysis_yaml_parses`). A
config file that only works on one laptop is a bug.

---

## Adding a new parameter — checklist

1. Add the argparse flag with a sensible built-in default.
2. Map YAML key → argparse `dest` in the script's `_config_defaults`.
3. Add the key to the section's allow-list.
4. Add the key to the YAML with a comment explaining units and range.
5. If it is a `Path`/typed value, add explicit coercion (rule 6).
6. If you added a whole new *section*, allow-list it in every other loader
   that reads the same file (rule 5).
7. Run the test suite — the shipped-YAML parse test will catch most mistakes.

---

## Current state: `scripts/nmr/process_fid.py`

Config file: `configs/nmr/analysis.yaml` (override with `--config PATH`).

| YAML section | Keys | Controls |
|---|---|---|
| `input` | `paths` | What gets processed when no path is given on the command line |
| `processing` | `phase_method`, `baseline_method`, `reference_method`, `zero_fill_points`, `line_broadening_hz`, `normalization`, `truncation_window`, `baseline_polynomial_order`, `smoothing_window_ppm`, `abd_sections`, `abd_noise_factor`, `abd_window_points` | FID → spectrum conversion and baseline correction |
| `regional_analysis` | `ppm_min`, `ppm_max`, `detect_all_peaks`, `min_prominence_snr`, `min_peak_distance_ppm`, `min_peak_width_ppm` | Which ppm window is searched and what counts as a peak |
| `peak_qc` | `use_manual_thresholds`, `min_snr`, `min_prominence_snr`, `min_width_hz`, `max_width_hz`, `require_positive_area` | Whether a detected feature is a real resonance or noise |
| `simple_table` | `restrict_to_window`, `target_ppm`, `window_ppm` | Narrows `peaks_simple.csv` to the one tracked resonance |
| `plots` | `display_min_ppm`, `display_max_ppm`, `flattened_overlay.*` | Plot ppm range and the plot-only flattened overlay |
| `output` | `directory`, `run_name`, `export_spectra_csv` | Where results are written |
| `reference` | `enabled`, `method`, `expected_ppm`, QC gates | Chemical-shift referencing (fail-closed; off unless declared) |
| `statistics` | `enabled`, `bootstrap.*`, `fixed_regions`, `outliers`, `plateau`, `kinetics`, `multivariate` | Publication-statistics tables — see `docs/nmr_statistics.md` |

Sections not yet migrated: none. Every `process_fid.py` flag has a YAML key.

### Two behaviours worth knowing

**The output folder is rebuilt from scratch on every run.** `process_fid.py`
deletes and recreates `results/processed/nmr/<input-name>/`. Turning
`statistics.enabled` off and re-running therefore *deletes* the previous run's
`statistics/` tables. Windows will also refuse the delete with
`PermissionError: [WinError 32]` if a file in that folder is open in Excel or a
terminal is `cd`'d into it.

**The run folder is named after the input folder**, so
`results/raw/nmr/06-09-26/` → `results/processed/nmr/06-09-26/`. Listing
several inputs — whether in `input.paths` or on the command line — merges them
into a *single* output folder named after the first one. Process one dataset
per invocation.

**Every output file is prefixed with the run folder's name**, giving
`06-09-26_peaks_simple.csv`, `06-09-26_run_qc.csv`,
`plots/statistics/06-09-26_rate_vs_time.png`, and so on. Files get shared
individually, and a bare `run_qc.csv` is ambiguous the moment it leaves its
folder. The prefix is applied by `_prefix_output_files` as a single rename pass
after everything is written — including files produced by the statistics
library — and recorded paths in `summary.json` are rewritten to match. Anything
a future writer adds is covered automatically, with no new write site to
remember.

---

## Scripts not yet migrated

`analyze_single.py`, `compare_timeseries.py`, `batch_report.py`, and
`plot_spectra.py` already read `common` plus their own section via
`_config.py`, but their sections do not yet cover every flag they expose. Bring
them up to the checklist above when they are next touched.
