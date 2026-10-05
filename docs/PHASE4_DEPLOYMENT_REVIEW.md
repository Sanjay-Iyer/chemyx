# Offline phase review and transfer checklist

Phase 4 is a local saved-file review tool. It has no instrument acquisition or
motion path. Production acquisition, scientific processing defaults, stage
logic, and plateau calculations remain unchanged.

## Review an acquisition

From the repository root, using the existing installed Python:

```powershell
python scripts\nmr\phase4.py path\to\spectrum.dx --runs-root results\runs\si6
```

Use `results\runs\si6_mock` to browse mocks. Without a file argument, Phase 4
uses an available historical demo or opens a file picker. The default browser
root is the older `results/runs/automated`; supply the current run root above.

1. Click **Browse Runs**, select the run, then the exact acquisition.
2. Compare **Original — Before Phase Correction** with **Automated — Script
   Result**. These are the unphased and production-phased real FFT, respectively;
   they are not the FID or the baseline-corrected integration trace. The ppm axes
   run high-to-low. The information panel identifies dataset, filename, summary,
   and phase provenance. **Overlay comparison** adds a shared comparison plot.
3. The automated trace comes from its exported full numerical spectrum when
   available. Otherwise it is explicitly labelled as reconstructed from the
   recorded phase, line broadening, zero filling, and truncation settings using
   the same audited FFT helper. No baseline correction is applied to these
   phase-comparison traces. Referencing, if enabled, is recorded separately.
4. Click **Copy Automated → Manual** to return to the saved production phase.
   **Reset to stored phase** instead uses the raw file's `$PHC0/$PHC1`; these are
   equivalent only when production used those stored settings.
5. Adjust P0/P1/pivot. The Manual trace updates immediately. Sliders resolve
   0.1 degrees over +/-180 degrees; pivot resolves 0.000001 ppm. Production
   phase is retained exactly in its provenance and Automated trace.
6. Select a separate output directory, preferably `results/manual_reviews`.
   Click **Recalculate & Save Manual Analysis**. This runs the existing
   `process_fid.py` with manual phase, replaying its recorded quantitative CLI
   settings: preprocessing, baseline, regional detection, tracked-window
   integration and processing QC. Optional statistics/target-peak reporting
   still reads the installed analysis YAML. The experiment's additional QC,
   stage decisions and plateau are not recalculated by a review.
7. Inspect the new peak and QC CSVs. **Save Phase Review** stores phase choices
   alone; **Recalculate & Save Manual Analysis** additionally saves numerical
   results. **Open Phase Review** restores the saved production and manual
   choices without changing the original production output.

An acquisition without a successful exact filename match is labelled
**UNPROCESSED**. Its Original/Manual spectra remain available, and another
acquisition's result is never substituted. Numerical manual re-analysis needs
a matching processed result to replay. For an unprocessed file, **Run Automated
Processing** can create its own result in a newly named review-output folder.

Fast Level 2 mocks intentionally reuse processing for identical fixture bytes;
those reused iterations have tables but no individual phase summary, so the
browser labels them UNPROCESSED. Real acquisitions each take the production
processing path. Fixture copies retain their original acquisition times.

## What production retains

Verified representative source:
`results/runs/si6_mock/20261001_160257_si6_mock/raw_nmr/initial_reaction_0001.dx`.
Its processed prefix is
`20261001_160257_si6_mock_initial_reactio_full_spectrum` (`<prefix>` below).

| Information | Exact file/field or limitation |
| --- | --- |
| Raw acquisition | `raw_nmr/initial_reaction_0001.dx`: complex FID real/imaginary pages, acquisition metadata, `$PHC0/$PHC1`, `LONG DATE` |
| Before-phase numerical FFT | Not separately saved by default; reconstructed from the raw FID using recorded FFT preprocessing |
| Production phase | `processed_nmr/<prefix>/<prefix>_summary.json`: matching `records[].phase0_deg`, `phase1_deg`, `phase_method`, `phase_direction`; representative 5/-10 degrees, stored/inverse |
| Production pivot | No independent physical ppm pivot field: production uses nmrglue's array-index-zero P0/P1 convention; the GUI converts this to its displayed manual pivot |
| Preprocessing | Same summary: record `line_broadening_hz`, `processed_points`, `truncation_window`; `parameters` includes zero filling, detection, baseline, reference and QC values |
| Production-phased numerical full spectrum | With `--export-csv`: renamed root spectrum CSV pointed to by `records[].spectrum_csv`, columns `original_ppm`, `referenced_ppm`, `real`, `imag`, `magnitude`; this is before baseline subtraction |
| Full numerical spectrum in default live processing | `output.export_spectra_csv` is false; the representative summary has `spectrum_csv: ""`. Raw data plus exact phase/preprocessing remain available for reconstruction |
| Baseline-corrected numerical trace | Existing statistics `*_target_peak_spectra_long.csv`: `file`, `ppm`, `intensity` regional corrected real trace; full baseline-corrected numeric spectrum is not exported by default |
| Detection/integration | `<prefix>_peaks.csv` and `<prefix>_peaks_simple.csv`: selected `peak_ppm`, `integrated_area`, `snr`, `prominence_snr`, `width_hz`; representative 5.792 ppm, area 30.69, SNR 31.23 |
| Processing QC | `<prefix>_peak_qc_log.csv` and `<prefix>_peak_qc_log_window.csv`: `qc_pass`, `qc_failure_reasons`, measured values, threshold values and individual checks |
| Workflow QC/growth/plateau | Run `time_series.csv` and `operation_journal.jsonl`; distinct from the processor's QC |
| Scientific plots | Existing `plots/full/`, `plots/region/` and statistics outputs show the processed results and dataset identity; they are not independent raw/phase-before numeric exports |
| Configuration provenance | Summary `parameters`, dependencies, processing order and raw SHA-256; the run also retains its existing experiment/Arduino snapshots. This is not a complete frozen copy of all statistics YAML |

The primary baseline is applied to the manually phased real spectrum before
regional detection, local integration and QC when manual processing runs.
The optional full-spectrum CSV contains phased complex values before baseline
subtraction; do not interpret it as the baseline-corrected integration input.

## Separate manual outputs

Each calculation creates a unique `phase4_manual_<timestamp>/` folder under
the selected review directory. It contains the processor's ordinary summary,
numeric full spectrum, peak tables, QC tables and dataset-titled plots, plus:

```text
manual_phase_review.json
```

That JSON records source/run identity and hash, creation time, original
production summary and P0/P1/convention, manual P0/P1/pivot, `review_only: true`,
`analysis_role: manual_review`, the new processing summary, and table filenames.
The numerical peak/QC results are in the accompanying processor CSVs. Saving a
review cannot overwrite source data, production processed directories, final
summary directories, or an existing non-review file.

Manual folders stay outside `processed_nmr`. Automated final summary reporting
continues reading production `time_series.csv`, journal and `processed_nmr`.
It does not ingest these separate manual reviews:

```powershell
python scripts\nmr\summarize_run.py results\runs\si6\<run-folder>
```

## Transfer file checklist

Copy the whole current repository working directory, including uncommitted
changes and new files. A Git-only transfer currently misses the new untracked
files below. Preserve the WORK laptop's verified local configuration and
physical state when updating its existing checkout.

Tracked modifications to include:

* `README.md`, `config_templates/README.md`, `scripts/nmr/README.md`
* `scripts/02_si6_experiment.py`
* `scripts/nmr/phase4.py`

New source/documentation/tests to include:

* `chemyx_lab/analysis/final_nmr_summary.py`
* `scripts/nmr/summarize_run.py`
* `config_templates/experiments/README.md`
* All six experiment YAMLs: `standard_si6`, `reaction_30min`, `reaction_60min`,
  `reaction_mixed_intervals`, `short_attended`, `long_monitoring`
* `docs/CREATE_NEW_EXPERIMENT.md`
* `docs/CURRENT_ARCHITECTURE_LLM_HANDOFF_2026-10-01.md`
* `docs/PHASE4_DEPLOYMENT_REVIEW.md` (this guide)
* `tests/test_experiment_templates.py`, `tests/test_final_nmr_summary.py`,
  `tests/test_nmr_gui_offline_smoke.py`, `tests/test_phase4_deployment.py`

Required existing files remain part of the full repository copy, especially
the instrument/workflow modules, Phase 1-4 scripts and their helpers, Arduino
host/firmware, both bundled NMR fixtures, configuration schemas/defaults,
`requirements.txt`, `offline/requirements-lock.txt` and offline install scripts.

Git-ignored payload to include explicitly:

* `offline/wheelhouse/` (27 pinned Windows/Python 3.11 wheels)
* `offline/installers/python-3.11.9-amd64.exe`
* `offline/BUNDLE_MANIFEST.txt`
* Existing `offline/arduino/` toolchain/core and `ARDUINO_MANIFEST.txt` if you
  need offline firmware compilation/reflashing
* Existing `offline/drivers/` exports/manifests, when required by WORK
* Saved run folders/raw input datasets/manual reviews you want to reopen

Use the WORK environment or create its `.venv` through the existing offline
installer. Do not transfer HOME's conda interpreter path or a HOME virtual
environment as the runtime. Exclude disposable pytest folders/caches.

On WORK, verify Arduino COM/physical HOME, Chemyx COM, NMR local Ethernet, one
real acquisition and an attended short multi-iteration experiment with its
real final summary. HOME checks cannot establish hardware readiness.
