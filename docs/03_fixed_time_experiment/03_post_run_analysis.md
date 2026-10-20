> [Documentation home](../00_START_HERE.md) | [Three-instrument fixed-time experiment](00_START_HERE.md)

# SI6 fixed-time experiment: post-run NMR analysis

The fixed-time controller only saves raw spectra; it never processes them.
Analysis is a separate, repeatable batch step that reads the saved raw files.
It cannot affect a running experiment: it holds no instrument, ledger or NMR
lock and never writes into `nmr_raw`.

## Command (run after the experiment, from the repository root)

```powershell
python scripts\05_analyze_fixed_time_run.py
```

This analyses the newest `results\fixed_time\live\SI6_FIXED_*` run. To choose a
run, pass its folder:

```powershell
python scripts\05_analyze_fixed_time_run.py results\fixed_time\live\SI6_FIXED_<UTC>_<random>
```

Each invocation writes a new `<run>\analysis\<YYYYMMDD_HHMMSS>\` folder, so earlier
analyses are never replaced. Rerun it whenever you like; no physical step is
repeated.

## What it does

1. Builds `observation_inventory.csv` from the run's `report.json` (or
   `observations.jsonl`) and the per-observation `nmr_raw\*.result.json`. Every
   scheduled observation gets one row: stage, number, scheduled hours, NMR status,
   the raw file analysed, its JCAMP `LONG DATE`, its SHA-256, and a note.
   Missing or failed observations are listed and never replaced by zeros or
   synthetic spectra. Only files whose JCAMP header declares an FID are analysed.
   When a partial export and a `.rpc.dx` coexist, the file the worker reported as
   saved is used.
2. Runs the existing production processor `scripts\nmr\process_fid.py` three
   times, each in its own process: both stages (`all_stages`), `stage_1` and
   `stage_2`. It always uses `--phase-method acme` and the run's identity as the
   plot dataset name (for example `SI6_FIXED_… Stage 1`). The processor does the
   FFT, ACME phase correction, ALS baseline correction, peak detection and
   integration. It writes full and region spectrum plots per acquisition,
   overlays and stacks, peak-area-versus-time (LONG DATE) and trend figures, and
   CSV and JSON results and summaries. A file that fails processing is recorded
   in that group's `processing_failures\`, and the group continues. A group that
   fails does not stop the others.
3. Writes `analysis_summary.json` and `ANALYSIS_SUMMARY.txt` with the counts, each
   group's status and log, and the list of observations that were not analysed.

Exit code 0 means every group completed. 1 means at least one group failed (see
its `*_process_fid.log`). 2 means there were no saved spectra; the inventory is
still written.

## Notes

* Run it after the experiment. It is safe during a run (it only reads), but the
  processing is CPU-intensive. On HOME, 7 spectra took 262 s across the three
  groups; a full 36-spectrum run should take roughly 15 to 30 minutes.
* Do not point `process_fid.py --output-dir` at a run or `nmr_raw` folder
  yourself: it replaces an existing output folder of the same name. The entry
  point above always gives it a brand-new folder.
* Requires the analysis packages already in the offline bundle (NumPy, SciPy,
  matplotlib, nmrglue).
