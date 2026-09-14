# Processing audit

## Production path confirmed from source

| Order | Function/code | Change |
|---:|---|---|
| 1 | `read_jcamp_fid` | Decodes both JCAMP NTUPLES pages and applies their FACTOR scaling (y/data). |
| 2 | `FidData.complex_points` | Combines decoded real and imaginary values (data representation). |
| 3 | `_build_complex_spectrum` → `nmrglue.proc_base.em` | Applies `exp(-pi × 0.03 Hz × time)` (y). |
| 4 | `nmrglue.proc_base.zf_size` | Zero-fills from the acquired points to 65536 points; this interpolates the digital spectrum but adds no physical information. |
| 5 | `fourier_transform_fid` | FFT plus fftshift (time-domain data → frequency-domain y). |
| 6 | `build_ppm_axis` | Uses `$SWH`/`$SWEEP WIDTH`, `$SF`/observe frequency, and `$O1P`/spectral center (x only). Internal arrays ascend; plots invert to conventional high-to-low ppm. |
| 7 | `build_phased_spectrum` → `nmrglue.proc_base.ps` | Applies stored `$PHC0` and `$PHC1` with `inv=True` (complex y/lineshape). |
| 8 | `process_fid.py` reference branch | `reference_method=metadata`; keeps the metadata ppm grid unchanged. Optional validated/manual/model modes are disabled (x only when enabled). |
| 9 | `asymmetric_least_squares_baseline` | Global spectral background estimated on the full phased real spectrum with lambda=1e+06, p=0.001, iterations=10; corrected y = phased real − global baseline. No normalization. |
| 10 | `pick_spectrum_region` | Restricts detection to 5.0–6.5 ppm, fits an iteratively clipped degree-3 regional polynomial, estimates robust MAD noise, and smooths only the detection trace with a 0.006 ppm Savitzky–Golay window. |
| 11 | `scipy.signal.find_peaks` inside `pick_spectrum_region` | Requires detection prominence ≥5 regional noise units, separation ≥0.04 ppm, and width ≥0.015 ppm. |
| 12 | `pick_spectrum_region` | Measures position by a three-point parabolic apex, height/SNR on the quantitative regional trace, and Stage-1 positive trapezoidal area over apex ± max(detected linewidth, 0.015 ppm). |
| 13 | `_peak_qc` | Requires SNR ≥3, prominence/SNR ≥3, linewidth 1–10 Hz, and positive area. |
| 14 | `_select_simple_peak_rows` | Within 5.70–5.90 ppm, keeps the QC-passing candidate with highest SNR; a non-detection is zero-filled in `peaks_simple.csv`. |

## Referencing result for this dataset

The JCAMP headers describe an internal toluene metadata reference at 5.000 ppm. Production is configured with `reference_method: metadata`, reference correction disabled, solvent identity unknown, and no internal standard. Therefore no reference peak is searched, no constant offset is calculated, and the applied offset is 0.000 ppm for all six acquisitions. The reference audit numerically confirms that intensity is unchanged.

## Phase result

The August worked examples store p0=5° and p1=-10°. They are passed to `nmrglue.proc_base.ps(..., inv=True)`; no automatic re-phasing occurs.

Across the six acquisitions, the stored phase operation changes the full-spectrum real trace by RMS 78.5–94.5 a.u.; the production ALS subtraction changes it by RMS 1516.0–2237.4 a.u. In the 5.70–5.90 ppm product window, the corresponding ranges are 7.1–10.2 a.u. for phase and 86.2–160.0 a.u. for ALS. Baseline subtraction is therefore the larger numerical transformation in this dataset, including around the product signal; this comparison describes magnitude, not correctness.
