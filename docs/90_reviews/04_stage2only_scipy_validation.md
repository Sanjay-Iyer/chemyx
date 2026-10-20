# 04 — Validation of the 08/09 SciPy Stage 2 workflows

Validated on HOME on 2026-10-19. No instruments were contacted. Launch commands
and configuration links are in the [08 operating guide](../03_fixed_time_experiment/08_stage2only_scipy_run_guide.md)
and [09 analysis guide](../03_fixed_time_experiment/09_stage2only_scipy_analysis.md).

## Implemented behavior

08 runs the existing Stage 2 sampling recipe for six hours, with measurements
scheduled at time 0 and every 30 minutes through the six-hour endpoint. Its
sampling scheduler and settings loader match 06. Existing HOME definitions are
preserved; no Phase 1, reagent dose, or Channel 2 loading reset is introduced.

08 starts an independent review worker before opening instrument handles. The
controller does not import numerical dependencies, validate the analysis
profile, wait for analysis, or read its completion/error/status JSON as a run
decision. A numerical child has a 600-second timeout. 09 independently selects
and processes saved Stage 2 data into a new output directory.

Both use the same numerical processor and approved profile:

1. Existing Fourier processing, ACME phasing and ALS baseline correction.
2. `find_peaks` with minimum prominence **8 times the regional noise estimate**.
3. Bounded target selection and `peak_widths(rel_height=0.95)` boundaries.
4. Interpolated endpoint intensities defining a local straight baseline.
5. Positive trapezoidal integration of the original unsmoothed processed data.

Primary measurements, rates, normalizations and kinetic reports use dynamic
area. Missing dynamic detections remain missing. Fixed 5.70–5.90 ppm area is
retained only for comparison. Ambiguous candidates are visible with flags and
excluded from quality-filtered fits. Analysis never determines run duration.

## Automated checks

**33 new workflow test cases passed**, across these files:

- `tests/test_stage2_scipy_processing.py`: 11 cases, including exact area and
  endpoint parity, missing/invalid results, primary statistics, NumPy 1 API
  compatibility, metadata-only timing and dataset titles across PNG/SVG/PDF.
- `tests/test_si6_stage2only_scipy.py`: 14 cases, including the identical
  scheduler/recipe, simulated full 13-slot runs despite five analysis faults,
  mocked movement preserving HOME and Channel 2, a terminated hung numerical
  child, the actual mock CLI, and a cold subprocess with NumPy/SciPy/Matplotlib/
  nmrglue imports deliberately unavailable.
- `tests/test_stage2only_scipy_reanalysis.py`: 8 cases, including Stage 1
  exclusion, primary/RPC recovery, damaged previous reports, folder selection,
  separate outputs, failure/timeout preservation, honest partial status and
  actual processing of a real FID through the 09 CLI.

The initial combined run passed 31 cases. Two additional cases subsequently
passed: missing scientific dependencies and the NumPy 1 integration API. The
entire 11-case numerical test file was rerun after the compatibility change.
The existing benchmark suite also passed **20 cases** after that shared
integration helper change. Total: **53 distinct passing cases**.

08 configuration-only validation passed for both COM4 and COM6 machine
presets: 13 scheduled measurements, eight NMR scans, no hardware contact.

## Real June 8 and June 9 data

The actual 09 command processed all seven June 8 and eight June 9 raw spectra.
Both datasets reported `COMPLETE`, with no decoding or numerical job errors.
This means processing completed; it does not imply a target was detected in
every spectrum.

| Dataset | Spectra processed | Finite dynamic areas | Missing detections |
|---|---:|---:|---:|
| 06-08-26 | 7 | 2 | 5 |
| 06-09-26 | 8 | 7 | 1 |
| Total | 15 | 9 | 6 |

All 15 results matched the previously reviewed `prominence_8` / `width_95`
benchmark: nine finite areas matched exactly, with maximum absolute difference
**0.0**, and six missing detections matched. Two June 9 candidate areas retain
the benchmark's ambiguity/overlap flags. No fixed-area or zero substitution
was introduced. A final replay from saved ACME/ALS arrays under the final code
also reproduced areas, signed areas, boundaries and detection statuses exactly.

All 15 raw source hashes were rechecked unchanged. All observations used
`LONG DATE header` acquisition timing. The 132 primary plot-manifest entries
were checked for dataset identity, matching titles between formats and
existing output files; PNG title metadata and SVG titles were verified.
The final June 9 area plot was visually inspected, including flagged areas
and the missing-result marker at the actual metadata time.

## Actual background-worker launch

The real 08 worker launcher processed a copied real June 9 FID from a prepared
saved-observation fixture. The worker finished with a `COMPLETE` job, produced
the dynamic primary metric, and published the latest integration preview to
the dashboard. Only the separate validation harness waited for this child;
the instrument controller never waits for analysis.

## Preservation and compatibility

The 11 recorded existing 06/07 and production-processing files remain byte
identical, including their entry points, controller, worker, recovery module,
06 preset, shared analysis YAML, FID processor and phasing modules.

The shared benchmark integration module has one intentional compatibility
change: it now calls the existing `nmr._trapezoid` helper instead of directly
calling NumPy 2's `trapezoid`. This supports the repository's allowed NumPy
1.24 versions, which provide `trapz`. It changes no boundary or integration
formula, and the real-data replay confirms identical numerical results.
The existing benchmark result files were preserved.

Local detailed evidence is under `results/101926_stage2only_scipy/`:

- `numerical_validation.json`: final hashes, replay and launch checks.
- `benchmark_primary_area_parity.csv`: all 15 benchmark comparisons.
- `real_june8/` and `real_june9/`: complete 09 outputs.
- `LIVE_REVIEW_INTEGRATION/`: actual background-launch evidence.
- `unchanged_files_before.json`: original file-hash receipt.

Validation covers offline numerical processing, mocks and process separation.
Physical instrument behavior was not exercised on HOME.
