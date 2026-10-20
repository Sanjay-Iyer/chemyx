# 08 — Stage 2 only with primary SciPy dynamic peak area

[Experiment index](00_START_HERE.md) | [09 — Re-run SciPy analysis](09_stage2only_scipy_analysis.md)

[Validation evidence](../90_reviews/04_stage2only_scipy_validation.md): 53 passing
test cases and exact parity with the reviewed June 8/9 benchmark.

Use **08** for the same three-instrument Stage 2 sampling experiment as 06,
with the approved dynamic area method in the independent live analysis.
Scripts 06 and 07 keep their original analysis behavior.

## Launch on WORK after preparing the instruments

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\08_si6_stage2only_scipy_run.py --live
```

For Chemyx COM6:

```powershell
python scripts\08_si6_stage2only_scipy_run.py --live --machine-config configs\machines\si6_real_COM6.yaml
```

The default is Chemyx COM4, Arduino COM3 and eight NMR scans. Phase 1 and any
reagent addition are already complete. Restore Channel 1 to its established
empty HOME and prepare the sampling path. Startup moves the needle to the
existing Arduino HOME, then UP; it establishes no new HOME. Channel 2 receives
no infuse/withdraw commands and its loading record is not reset. Existing
motion-reference and connection requirements are the same as 06.

Launch starts immediately without a startup `y`, dose reconciliation, or a new
analysis acknowledgment. New analysis results/JSON never determine admission,
sampling, phase duration, or instrument cleanup.

## Schedule and independence from analysis

The sampling cycle, recipe and schedule match 06: **six hours, every 30 minutes,
including time 0 and 6 hours**, for 13 scheduled measurements. The endpoint
cycle finishes acquisition/cleanup after six hours. A slow cycle can skip
expired intermediate slots; the final endpoint is sampled once after earlier
cleanup. No Phase 1 or Channel 2 dose occurs.

NMR acquisition failures and numerical-analysis outcomes cannot shorten Stage 2.
Analysis runs in a separate background process with no instrument handles. The
controller never imports SciPy, loads the numerical profile, waits for analysis,
polls its process, or reads analysis status as a run decision. Missing packages,
an invalid analysis profile, a crash, plotting failure, or hung analysis only
affect review output. Each numerical job has a ten-minute timeout; later saved
measurements are still attempted by the worker. Hardware/movement failures and
Ctrl+C retain the existing stop behavior. There is no pause/resume; another
launch starts a fresh six-hour run with its own time-0 measurement.

## Primary numerical method

Both 08 and 09 use exactly the same numerical processor:

1. Existing raw-FID interpretation, Fourier transform and **ACME P0/P1 phasing**.
2. Existing **asymmetric least-squares baseline correction**.
3. SciPy **`find_peaks` with prominence at least 8 times the existing regional noise estimate**.
4. Bounded selection near 5.79 ppm and the previous accepted center.
5. **`peak_widths(rel_height=0.95)`** for interpolated integration boundaries.
6. A line between the interpolated endpoint intensities, followed by positive
   trapezoidal integration of the original unsmoothed processed signal.

The remaining approved defaults are search 5.60–6.00 ppm, height at least 5σ,
candidate distance 0.02 ppm, half-prominence width 0.012–0.18 ppm, maximum
movement 0.12 ppm, and 0.006 ppm identification smoothing. The numerical profile
is separate from the instrument/schedule YAML and requires prominence 8σ and
rel_height 0.95. The number 0.95 describes the boundary level, not 95% of total
peak area.

**Primary value:** `target_peak_measurements.csv` → **`area`**.
Primary area/time, normalized-area, rate and kinetic reports use this dynamic
value. Missing detections remain missing; there is no replacement by fixed
area or zero. Ambiguous candidate areas remain visible with quality flags;
quality-filtered kinetic/plateau reports exclude them. None of these labels
control the experiment.

`fixed_window_comparison.csv` retains 5.70–5.90 ppm as a **comparison only**.
The general legacy statistical reports live under `secondary_statistics/` so
they cannot be mistaken for the primary dynamic metric.

## Scripts, configs and outputs together

| Purpose | File |
|---|---|
| Instrument entry point | [08_si6_stage2only_scipy_run.py](../../scripts/08_si6_stage2only_scipy_run.py) |
| Separate controller | [si6_stage2only_scipy.py](../../chemyx_lab/workflows/si6_stage2only_scipy.py) |
| Instrument schedule and recipe | [si6_stage2only_scipy_6h.yaml](../../configs/experiments/si6_stage2only_scipy_6h.yaml) |
| Worker watching saved measurements | [si6_stage2only_scipy_analysis.py](../../chemyx_lab/workflows/si6_stage2only_scipy_analysis.py) |
| Primary SciPy detection/boundary settings | [stage2only_scipy.yaml](../../configs/nmr/stage2only_scipy.yaml) |
| Shared Fourier/ACME/ALS processing defaults | [analysis.yaml](../../configs/nmr/analysis.yaml) |
| Shared 08/09 numerical implementation | [stage2_scipy_processing.py](../../chemyx_lab/analysis/stage2_scipy_processing.py) |

```text
results/stage2only_scipy/live/SI6_STAGE2ONLY_SCIPY_<unique id>/
  nmr_raw/stage_2_0001.dx ... stage_2_0013.dx
  analysis_live/index.html
  analysis_live/worker.log
  analysis_live/stage_2_0013/processed/
    target_peak_measurements.csv
    processing_summary.json
    plots/area_vs_time.png
    plots/000_latest_region.png
    plots/observations/
    statistics/
    fixed_window_comparison.csv
    secondary_statistics/
```

Open **`analysis_live/index.html`** during acquisition; it refreshes every 15
seconds and links the latest completed cumulative analysis. The worker can
finish after the instrument controller exits. Older snapshots and raw data are
preserved. If analysis needs to be retried, use **09** on the run folder.

## Safe local validation on HOME

```powershell
conda activate ai
```

```powershell
python scripts\08_si6_stage2only_scipy_run.py --validate-config
```

```powershell
python scripts\08_si6_stage2only_scipy_run.py --mock
```

Mock mode simulates the complete six-hour schedule without wall-clock waiting
or instruments and does not launch analysis on its synthetic fixture exports.
