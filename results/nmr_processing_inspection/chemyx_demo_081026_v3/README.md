# 081026 PhSi4 automated demo v3 NMR processing inspection

This directory is an exploratory, read-only transparency package generated from real JCAMP-DX FIDs. It does not replace production plots or alter production numerical outputs.

## Confirmed production sequence

JCAMP real/imaginary page decode and FACTOR scaling → complex FID → exponential line broadening (0.03 Hz) → zero fill (65536 points) → FFT/fftshift → metadata ppm axis → stored NMReady phase via `nmrglue.proc_base.ps(..., inv=True)` → ALS on the phased real spectrum only (lambda=1e+06, asymmetry=0.001, 10 iterations) → subtraction with no normalization → 5.0–6.5 ppm regional polynomial detrend → detection-only Savitzky–Golay smoothing (0.006 ppm) → peak measurement/integration → QC.

The core peak area integrates the regional quantitative trace over ± the detected linewidth (minimum half-width 0.015 ppm). Fixed-window and left-multiplet areas instead use a straight line joining the requested integration feet and positive-clipped trapezoidal integration.

## Primary worked example

`20260810_154822_081626_phsi4_0001_8scan_gain12.dx` at `2026-08-10T15:57:37` from `LONG DATE header`. Reconstructed core area `2.75`, fixed product area `6.07`, and left-multiplet area `2.81`.

`pre - post` equals the stored ALS baseline with maximum absolute residual `7.28e-12`.

## Sensitivity summary

Maximum absolute percent changes over the representative set and tested reasonable ALS variations: core area `75.69%`, fixed product `38.98%`, starting material `22.03%`, silane `20.64%`, left multiplet `6.48%`. See `variant_06_als_sensitivity/als_sensitivity_metrics.csv` for every value; zero/non-detected cases must be interpreted separately.

## Timing

All ordering and scientific timestamps in this package use JCAMP `LONG DATE`. Filename-derived times are not used as authoritative timing.
