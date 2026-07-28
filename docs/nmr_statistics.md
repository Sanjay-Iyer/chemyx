# NMR statistics pipeline

Optional publication-quality statistics for `process_fid.py`. **Off by default**
— with `statistics.enabled: false` (the shipped setting) the processor produces
exactly the same outputs as before. Enable per run with `--statistics`, or set
`statistics.enabled: true` in `configs/nmr/analysis.yaml`.

```bash
python scripts/nmr/process_fid.py results/raw/nmr/06-08-26/ --statistics
```

When enabled, additional tables are written under
`results/processed/nmr/<input>/statistics/` and plots under
`.../plots/statistics/`. Every table joins back to `peaks.csv` / `results.csv`
via the stable keys `file`, `spectrum_index`, `peak_number`, `peak_id`
(= `peak_number` within a spectrum) and `peak_family_id`.

All computation lives in importable library modules (not the CLI script):

| Module | Responsibility |
|---|---|
| `chemyx_lab/analysis/statistics.py` | robust descriptive stats, CV guards, outlier flags |
| `chemyx_lab/analysis/lineshapes.py` | Gaussian/Lorentzian/pseudo-Voigt fits, fit diagnostics, AICc/BIC model comparison, overlap/resolution |
| `chemyx_lab/analysis/uncertainty.py` | residual/parametric bootstrap of peak parameters |
| `chemyx_lab/analysis/time_series.py` | fixed-window integration, irregular-interval rates, statistical plateau |
| `chemyx_lab/analysis/kinetics.py` | zero/first-order kinetic model fitting |
| `chemyx_lab/analysis/normalization.py` | internal-standard ratio + uncertainty propagation |
| `chemyx_lab/analysis/qc.py` | run-level QC scorecard, control limits |
| `chemyx_lab/analysis/multivariate.py` | spectral similarity, PCA |
| `chemyx_lab/analysis/statistics_report.py` | assembles every table from one spectrum series |
| `chemyx_lab/analysis/statistics_plots.py` | the plot set |

---

## Five kinds of "error" — do not conflate them

| Term | What varies | How this pipeline reports it |
|---|---|---|
| **Repeatability** | same sample, same instrument, re-measured | spread of a peak family across replicate spectra (`peak_family_statistics.csv` SD/MAD/CV) |
| **Reproducibility** | different day/operator/instrument | *not* measured here; requires a designed inter-run study |
| **Processing sensitivity** | same FID, different baseline/apodization/phase/window | `validate_processing.py` (`processing_sensitivity.csv`); a *lower bound* contribution to area uncertainty |
| **Statistical uncertainty** | finite SNR, given one fixed processing | bootstrap CIs in `peak_uncertainty.csv`; slope CIs; kinetic parameter CIs |
| **Chemical variability** | the reaction actually changed | the time-series trend itself (rates, kinetics, plateau) |

A tight bootstrap CI (small statistical uncertainty) says nothing about
reproducibility or processing sensitivity. Report them separately.

---

## Output files

### `peak_uncertainty.csv` — per-peak bootstrap uncertainty
One row per detected peak. A **residual bootstrap** re-fits the peak's line
shape over resampled fit residuals; the spread of the re-fits gives SE and a
95 % percentile CI for center, height, FWHM and area.

Columns: `center_ppm_se/ci95_low/ci95_high`, `height_*`, `area_*`, `width_ppm_*`,
`bootstrap_method`, `bootstrap_iterations_requested`,
`bootstrap_iterations_succeeded`, `bootstrap_qc_pass`, `bootstrap_qc_reason`.

- **Deterministic**: seeded from `bootstrap.random_seed` (plus a per-peak
  offset), so re-runs reproduce intervals exactly.
- **Fail-soft**: if the fit is not identifiable, center/height/FWHM SEs are
  blank (NaN) with a reason, and only an area uncertainty from a *parametric*
  noise bootstrap is reported (`bootstrap_method = parametric_area_only`). No
  fabricated numbers.
- **Minimum data**: a window with ≥ 7 points; ≥ `min_success_count` (20) usable
  re-fits or `bootstrap_qc_pass = False`.
- **Caveat**: zero-filled points are **not** independent measurements (see
  below), so bootstrap CIs over an oversampled spectrum are a *lower bound* on
  true measurement error.

### `peak_overlap.csv` — resolution / deconvolution diagnostics
Per peak, distance to nearest neighbour and the chromatographic resolution

```
Rs = 2 * |center2 - center1| / (FWHM1 + FWHM2)
```

`Rs ≥ 1.5` = baseline separation; `Rs < 1` sets `deconvolution_stable = False`
and an `overlap_warning`, meaning independently integrated areas of the two
peaks are correlated and a joint deconvolution would be needed for trustworthy
component areas. A **diagnostic, not an NMR validity rule**.

### `peak_family_statistics.csv` — robust family summaries
One row per peak family (a peak tracked across spectra). Classical **and**
robust statistics for ppm, area, height, width: `mean`, `median`, `sd`, `mad`,
`iqr`, `cv_percent`, plus `detection_frequency`, `observations`,
`expected_observations`, `reproducible`.

- CV is **NaN** when the mean is within `1e-12` of zero (`cv_reason`), because a
  spread divided by a near-zero mean is meaningless.
- MAD = `median(|x − median|)` (raw); robust CV uses the ×1.4826-scaled MAD.

### `peak_outliers.csv` — flags, never deletions
Modified z-score within each family:

```
robust_z = 0.6745 * (x − median) / MAD          (MAD > 0)
robust_z = (x − median) / (1.2533 * meanAD)      (MAD == 0 fallback)
```

`|robust_z| > robust_z_threshold` (default 3.5) sets `is_statistical_outlier`.
`is_processing_outlier` is set when a peak was classified `unresolved_feature`.
The two are kept **separate** (chemical anomaly vs processing failure). Zero-MAD
is handled safely; nothing is ever removed — the original value stays with a
reason.

### `run_qc.csv` — per-spectrum QC scorecard
`reference_shift_ppm`, `region_noise`, `phase0/1_deg`, `peak_count`,
`qc_peak_count`, `qc_pass_fraction`, `median_snr`, `median_width_hz`,
`correlation_to_first`, `cosine_similarity_to_first`, `spectral_rmse_to_first`,
`run_qc_pass`, `run_qc_failure_reasons`. A spectrum passes unless something
concrete is wrong (no resolved peaks, most peaks failing QC, noise not
estimable, a rejected reference correction). Control limits (`±2σ` warning,
`±3σ` control) are available via `qc.control_limits`, which refuses to certify
limits from fewer than 8 observations.

### `time_series_regions.csv` — fixed-window integration
Identical, chemically-defined ppm windows (`statistics.fixed_regions`)
integrated across every spectrum, so area change reflects chemistry, not the
peak picker moving its own limits. Columns include `fixed_window_signed_area`,
`fixed_window_positive_area`, `fixed_window_area_uncertainty` (white-noise
propagation `σ·d·√m`), `fitted_peak_area` (sum of detected-peak areas in the
window) and `area_difference[_percent]` as a fixed-vs-fitted QC diagnostic.

**Fixed-window vs fitted-component area**: the fixed window integrates a
constant ppm range regardless of what is inside it (stable across a series but
includes neighbours/baseline); the fitted-component area attributes intensity to
a specific modelled peak (specific but only as good as the fit and the
separation). Divergence between them is a warning sign, which is why both are
reported.

### `time_series_rates.csv` — rates over real time
Per analysis target (each fixed region and each reproducible family):
`delta_area`, `delta_time_hours`, `absolute_rate_per_hour`,
`relative_rate_percent_per_hour`, and a trailing **rolling OLS slope** with SE
and 95 % CI. All differences use **actual elapsed time** from acquisition
timestamps — never an assumed equal interval. `relative_rate` is NaN when the
previous area is ≈ 0.

### `plateau_analysis.csv` — statistical plateau
Supersedes the old "N consecutive sub-threshold % changes" rule (which cannot
distinguish a real flat line from noise). Fits the slope of the most recent
`minimum_points`, and declares a plateau only when the **entire slope 95 % CI**
lies inside an equivalence margin (`±equivalence_percent_per_hour` of mean area,
or `±equivalence_abs_per_hour`), sustained over `persistence_points` windows. A
sustained **decline** sets `decline_detected` and fails the plateau unless
`allow_declining_plateau: true` — a falling signal is not a completed reaction.
`method` labels which rule produced the row.

### `kinetic_fits.csv` — kinetic model fitting
For each target × model (`zero_order`, `first_order_decay`,
`first_order_formation`, `first_order_formation_lag`): `rate_constant` ± SE and
CI, `half_life`, `t90`, `t95`, `plateau_area`, `lag_time`, fit diagnostics
(`fit_rmse`, `r_squared`, `aic`, `aicc`, `bic`, `durbin_watson`,
`residual_lag1_autocorrelation`, `ljung_box_pvalue`), and `is_best_by_aicc`.

- **Model selection uses AICc, never R² alone.** R² never decreases when
  parameters are added, so it always favours the most flexible model; AICc/BIC
  penalise unjustified complexity.
- **Correlated residuals**: OLS assumes independent residuals; a densely-sampled
  reaction usually violates this, making SEs *optimistic*. This pass does not
  implement AR(1)/GLS; instead it reports DW, lag-1 autocorrelation and the
  Ljung-Box p-value and raises
  `residual_autocorrelation_optimistic_uncertainty`. Non-identifiable fits (huge
  SE) fail `fit_qc_pass` and are excluded from the AICc winner.

### `spectral_similarity.csv` — whole-spectrum change
Every spectrum resampled onto one common aligned ppm grid, then
`correlation_to_first/previous` (Pearson), `cosine_similarity_to_first`,
`spectral_rmse_to_first`, `integrated_absolute_difference`, `spectral_angle`
(degrees). Two peaks both trending with time are **not** thereby chemically
related.

### `statistics_summary.json`
Provenance: which analyses ran, bootstrap settings, fixed regions, plateau
method, kinetic models tested, warnings, and (via the run's `summary.json`) the
git commit and dependency versions. `summary.json` gains a `statistics` block
mirroring this.

---

## Plots (`plots/statistics/`)

| Plot | Shows |
|---|---|
| `noise_vs_time.png` | region noise drift across the run |
| `linewidth_vs_time.png` | median FWHM (Hz) — shimming/lock stability |
| `reference_drift_vs_time.png` | applied reference shift over time |
| `phase_vs_time.png` | phase0/phase1 stability |
| `qc_pass_rate_vs_time.png` | fraction of peaks passing QC per spectrum |
| `spectral_similarity_vs_time.png` | Pearson & cosine similarity to first |
| `area_with_ci_vs_time.png` | fixed-window area ± 1 SE per target |
| `rate_vs_time.png` | rolling slope (area/hour) per target |
| `kinetic_model_comparison.png` | data + best-by-AICc model curve |

---

## Two warnings worth repeating

- **Zero filling does not create independent experimental information.** It
  interpolates the spectrum to more points; it does not add measurements.
  Uncertainties derived by resampling zero-filled points understate true
  measurement error.
- **A peak-area ratio is not quantitative without qNMR conditions.** Reporting a
  molar ratio requires a relaxation delay long enough for full recovery, a
  well-characterised pulse angle, and adequate SNR. When those are unknown,
  `internal_standard` output is a *relative* quantity only; the relevant
  acquisition parameters are recorded so the limitation is explicit.

---

## Configuration

See the `statistics:` block in `configs/nmr/analysis.yaml`. Every value is
validated on load (`chemyx_lab/analysis/analysis_config.py`); unknown keys and
out-of-range values raise a specific error before any processing starts.

## Deferred (documented, not implemented in this pass)

- Per-region processing-sensitivity uncertainty budget combining
  `validate_processing.py` alternatives (baseline/apodization/phase/window) by
  root-sum-of-squares — the components exist; wiring them into a single
  `processing_uncertainty.csv` is future work.
- Species balance (`species_balance.csv`) — needs chemistry-specific
  signal→species assignments and equivalent-nuclei counts.
- Peak-family and lagged correlations, PCA scores/loadings CSVs and plots — the
  `multivariate` module supports PCA; the CSV/plot surface is future work.
- Full GLS/AR(1) kinetic errors and MCR-ALS — MCR-ALS needs a dependency the
  project does not currently carry; both are noted as future extensions.
