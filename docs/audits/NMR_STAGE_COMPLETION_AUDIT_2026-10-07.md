> Historical audit of the implementation before the tracked-area refactor.
> Its integral descriptions and source line links do not describe current Stage 1/2 behavior.
> See [current implementation](../SI6_TRACKED_PEAK_IMPLEMENTATION.md).

# Current NMR measurement and two-stage completion audit

Audited 2026-10-07. Repository HEAD: `669b93f3` (`Validate Si6 synthetic spectra and document operator configuration`). No production code/configuration was changed and no instrument was contacted. The four examples below were evaluated directly by the existing `completion_evidence()` function, using the nominal YAML rules and in-memory observations.

## 1. Scientist-friendly summary

| Item | Current implemented method |
|---|---|
| Tracked NMR signal | Nominal target **5.8 ppm**, detection and completion integration **5.70–5.90 ppm**. |
| Peak detection | Phase-corrected real spectrum, global ALS plus regional polynomial baseline correction; Savitzky–Golay smoothing for detection; SciPy local maxima with prominence, separation and width gates. |
| Selected peak | Highest-SNR QC-passing candidate inside the target window. No previous-peak identity/drift requirement in the controller adapter. |
| Peak area | Positive-only trapezoidal integral over a variable-width window around the detected candidate; retained as `peak_area`. |
| Reaction signal | **`completion_area = max(signed fixed-window integral, 0)`** from the unsmoothed corrected real trace. This drives both stages, not a fitted area or peak height. |
| Measurement QC | Provenance/phase/finite-array checks; peak acceptance or independently bounded absence; sideband noise ≤**1%** of first experiment reference height; area uncertainty ≤**0.5%** of first experiment reference integral; negative-integral/lobe checks. |
| Stage 1 endpoint | Decreasing signal: last windows' uncertainty-inclusive upper areas ≤**2.5%** of first Stage 1 area; decline ≥**95%**; range ≤**1%**; absolute normalized slope ≤**0.001/hour**. Minimum **8** observations and **12 h** at every qualifying window endpoint. |
| Stage 2 endpoint | Increasing signal: uncertainty-inclusive lower areas at least **98%** of the greatest Stage 2 area observed so far; growth from first Stage 2 area ≥**25% of that maximum**; range ≤**2%**; absolute normalized slope ≤**0.012/hour**. Minimum **8** observations and **3 h** at every qualifying window endpoint. |
| Consecutive confirmations | **3 overlapping windows**, containing **4 observations in Stage 1** and **5 in Stage 2**. Earliest possible nominal completion: observation **10** in either stage; ideally **18 h** / **4.5 h** after the respective first acquisition. |
| Invalid acquisition | Stops the experiment for review, with normal sample-return cleanup only when physical/journal state is proven. No automatic retry and no failed-peak-to-zero endpoint shortcut. |

Endpoint values come from [nominal two-stage YAML](</C:/code/chemyx_pump/config_templates/experiments/si6_two_stage_nominal.yaml:21>), `workflow.initial_stage.completion`, `workflow.first_addition_stage.completion`, and `analysis.measurement_qc`. Processing comes from [analysis.yaml](</C:/code/chemyx_pump/configs/nmr/analysis.yaml:115>). Full equations and gates follow below.

### Scope and configuration authority

This is the **live production path** selected by `scripts/02_si6_experiment.py` with the nominal two-stage recipe. It is distinct from the legacy magnitude-only runner and the virtual trend-fixture simulation.

The work-laptop `configs/experiments/si6_run.local.yaml` is absent from this checkout. Therefore these are verified **nominal defaults**, not a claim that unobserved work-laptop edits/environment overrides have identical values. The processor merges `configs/nmr/analysis.yaml` with `analysis.local.yaml`; the local file here changes input/output locations only. The integrated subprocess supplies the acquisition path/output name explicitly, overriding those locations.

`build_instrument_settings()` also applies explicit `NMR_*` environment overrides, including target/scans/gain. Actual run evidence is in `config_snapshot.json`, `instrument_settings_snapshot.json`, and per-acquisition `phase_audit/.../processing_metadata.json`. Source: [instrument settings resolution](</C:/code/chemyx_pump/chemyx_lab/workflows/si6_automated_nmr.py:609>), [processing config merge](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:84>), [controller preparation](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:718>).

## 2. Actual production processing chain

The integration calls `instrument_operations.run_nmr_acquisition()` → `si6_automated_nmr.run_process_fid_postprocessing()` → `scripts/nmr/process_fid.py`. The subprocess fixes the regional search at 5.0–6.5 ppm and restricts the simple table to resolved target ± detection half-width. It does not forward experiment `analysis.line_broadening_hz` as the processor's line broadening.

| Step | Function; input → output | Current numerical operation / controls |
|---|---|---|
| Acquire/retrieve | `instrument_operations.run_nmr_acquisition`; iFlow settings → JCAMP `.dx` | Nominal 8 scans, gain 12, auto gain false, center 5 ppm, sweep width 20 ppm; recipe `nmr`. An optional magnitude peak printout here does not authorize stage completion. |
| Decode | `nmr.read_jcamp_fid`; split real/imaginary JCAMP pages → complex FID | nmrglue decodes and applies JCAMP `FACTOR`; real/imaginary counts must match each other and `NPOINTS`. |
| Apodize/zero-fill | `nmr._build_complex_spectrum`; complex FID → padded FID | Multiply by `exp(-π LB t)`, **LB=0.03 Hz**, zero-fill to at least **65536** points. `processing.truncation_window: none`; no half-cosine taper. |
| Fourier transform | `nmr.fourier_transform_fid`; padded FID → complex spectrum | `nmrglue.proc_base.fft`: `fftshift(fft(FID))`, negative exponential DFT, no 1/N forward normalization. |
| ppm axis | `nmr.build_ppm_axis`; FFT size and JCAMP metadata → ascending ppm grid | `ppm = center_ppm + fftshift(fftfreq(N, d=1/SWH))/SF`. `$SWH`/`$SWEEP WIDTH`, `$SF`/`$SFO1`/`.OBSERVE FREQUENCY`, `$O1P`/`$SPECTRALCENTER`. Frequency and center have code fallbacks of 60 MHz and 5 ppm. Display reverses the axis. |
| Phase correction | `nmr.build_phased_spectrum`; complex spectrum + `$PHC0/$PHC1` → phased real/imaginary | `processing.phase_method: stored`; nmrglue `ps(..., inv=True)` by default. `Sφ[k]=S[k] exp(-iπ(P0+P1 k/N)/180)`. Missing stored phase numbers default to 0; this path does not optimize phase automatically. |
| Global baseline | `process_fid.process_spectrum_for_peaks` → `nmr.asymmetric_least_squares_baseline`; phased real → baseline-subtracted real | ALS defaults **λ=10^6**, asymmetry **p=0.001**, **10 iterations**. These are function defaults, not recipe/YAML thresholds. `processing.normalization: none`. |
| Regional correction | `nmr.pick_spectrum_region`; global-corrected real → regional corrected real | Region **5.0–6.5 ppm**, cubic local polynomial; up to **10** clipped-fit iterations. Retain residuals between median −**4σ** and median +**2.5σ** for baseline refitting. |
| Detection | Same function; regional corrected real → candidates | Savitzky–Golay nominal window **0.006 ppm**, polynomial order up to **3**. Smoothing is for detection only. SciPy `find_peaks` prominence ≥**5σ**, separation **0.04 ppm**, width ≥**0.015 ppm**. |
| Candidate QC | `process_fid._peak_qc`; candidate metrics → pass/failure reasons | Height SNR ≥**3**, prominence SNR ≥**3**, width **1–10 Hz**, positive-only area >0. `peak_qc.use_manual_thresholds: true`. |
| Target selection / adapter | `_select_simple_peak_rows` → `analyze_tracked_resonance`; candidates/tables → `peak_clear`, `peak_area`, height, ppm, SNR | Restrict interpolated ppm to **5.70–5.90**; select maximum SNR. Additional experiment gates: SNR ≥**5**, prominence SNR ≥**3**, selected positive area ≥**10^-12**. |
| Completion integral / measurement QC | `stage_measurement.fixed_window_measurement`; retained lossless arrays + provenance → trusted `completion_area`, uncertainty, `measurement_valid` | Integrate unsmoothed `regional_quantitative` over fixed **5.70–5.90**; reject invalid phase/provenance, excess noise/uncertainty or negative lobes; independently bound nondetection. |
| Endpoint | `stage_completion.completion_evidence`; same-stage trusted observations and metadata times → evidence/complete | Stage-specific progress, low/near-maximum, range, slope, point/time minimums and 3 consecutive windows. |

Sources: [acquisition](</C:/code/chemyx_pump/chemyx_lab/workflows/instrument_operations.py:99>), [processing invocation](</C:/code/chemyx_pump/chemyx_lab/workflows/si6_automated_nmr.py:748>), [decoding](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:325>), [complex processing](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1722>), [phase](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1637>), [regional detection](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1336>), [processing baseline call](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:499>).

The ALS operation solves `(W + λ DᵀD) z = W y` repeatedly, where D is the second-difference matrix. Subsequent weights are p where y>z and 1−p otherwise. The regional polynomial is an additional baseline correction, not a replacement for this global ALS baseline.

## 3. Exact tracked-peak calculation

### Detection and position

The detector searches the **whole 5.0–6.5 ppm region**, not just the tracked window. The simple-table selection subsequently restricts candidates to `|interpolated_ppm−5.8|≤0.10`.

Within the region, clipped polynomial residuals estimate robust noise:

```text
σdetect = 1.4826 × median(|selected residual − median(selected residual)|)
```

If this is nonfinite/nonpositive, the detector falls back to residual standard deviation, or 1.0 if that standard deviation is zero. Quantitative noise is separately estimated by the same MAD expression on the quantitative residuals. With the active `detection_trace: real`, both paths operate on the same real trace.

The nominal smoothing window is converted to grid points, rounded, made odd, set to at least 5 and limited to available points. Separation is `max(1, round(0.04/Δppm))` points; minimum width is `max(1, 0.015/Δppm)` points. SciPy `find_peaks` uses its default half-prominence width measurement (`rel_height=0.5`). Thus this measured width is not a fitted Lorentzian linewidth and is not invariably the physical FWHM.

Each candidate receives a three-point quadratic interpolation around its smoothed apex. A concave quadratic vertex inside the adjacent grid interval supplies `interpolated_ppm`; otherwise the discrete apex is retained. Peak height is read from the **unsmoothed regional quantitative real trace at the discrete apex**.

```text
height SNR = quantitative corrected apex height / σquantitative
prominence SNR = smoothed-detection prominence / σdetect
width_hz = width_ppm × observe_frequency_MHz
```

Sources: [pick_spectrum_region](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1336>), [candidate QC](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:871>), [simple-table selection](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:1160>).

### Selection, multiple peaks and absence

Eligible candidates must pass processor QC and the target-window check. If several qualify, `_select_simple_peak_rows()` chooses the **highest SNR**, not the nearest-to-target candidate and not a fitted-area maximum. There is no controller-specific continuity/drift check against the previous selected peak. Report-only `target_peak.tracking_max_drift_ppm: 0.08` is not used here.

`analyze_tracked_resonance()` requires exactly one simple-table row and finite numerical ppm/area/height/SNR/prominence-SNR/width values. It also reads the window QC log. A detected peak must have a QC-passing window entry, positive SNR, position within target ±0.10 ppm, SNR ≥5, prominence SNR ≥3, and selected positive area ≥max(`min_peak_area=0`, `area_epsilon=10^-12`).

When nothing passes, the processor **does write a zero-filled simple-table placeholder**. The controller recognizes its missing passing candidate/SNR and sets `peak_clear=False`; it does not blindly treat that placeholder as a chemical zero.

For the completion profile only, `fixed_window_measurement()` can rescue a nondetection as **bounded low signal** when all independent corrected-trace QC passes, no candidate in the window was rejected, the failure reason contains `no QC-passing peak`, and:

```text
max(signed_fixed_integral, 0) + U ≤ 0.025 × experiment_QC_reference_area
```

The first experiment observation cannot use this absence path; it needs a detected reference. Stage 2's first observation may be bounded low signal because the first Stage 1 observation supplies the experiment QC reference. A candidate failing workflow SNR/area gates is not eligible merely because it is weak. A rejected candidate cannot silently become absence. Source: [adapter](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:226>), [bounded-absence rules](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_measurement.py:12>).

## 4. Exactly what AREA means

### Four different quantities

| Quantity | Calculation | Role |
|---|---|---|
| Peak height / position | Unsmoothed corrected apex / quadratic-interpolated ppm | Detection/identification/QC; not endpoint magnitude. |
| Selected `peak_area` | `trapz(max(y,0), x)` over candidate center ±max(measured width, 0.015 ppm) | Simple-table `integrated_area`; adapter peak acceptance; diagnostic growth percentage. Variable window; not the stage reaction signal. |
| `local_feet_*_area` | Separate integral after subtracting a line between variable-window boundary feet | Additional peak-table diagnostics; not the selected simple-table area or stage endpoint integral. |
| `completion_area` | `max(trapz(y,x) over fixed target window, 0)` | **Actual Stage 1/2 reaction signal.** Unsmoothed real trace after ALS and regional polynomial corrections. |

The peak center interpolation is a fitted parabola for position only. The endpoint does not use a Gaussian/Lorentzian/pseudo-Voigt fitted area. Optional processor statistics fit/bootstrap areas for supplemental reports, not this endpoint.

Sources: [regional candidate integration](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1336>), [local-feet integration](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:602>), [simple-table writer](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:1183>), [fixed-window metric](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_measurement.py:12>).

### Fixed integral and uncertainty equations

The authoritative arrays are `region_ppm` and `regional_quantitative` from the single retained `phase_audit/*/*spectral_evidence.npz`. They are retained before readable CSV rounding. Source: [retain_phase_audit](</C:/code/chemyx_pump/chemyx_lab/analysis/phase_audit.py:99>).

For t=5.8 ppm, h=0.10 ppm, and retained increasing grid x:

```text
target mask: |x−t| ≤ h                 → 5.70–5.90 ppm
side mask:   h < |x−t| ≤ 0.5           → 5.30–5.70 and 5.90–6.30 ppm

A_signed = Σ_j (y[j]+y[j+1])/2 × (x[j+1]−x[j])
A_negative = trapz(max(−y,0), x) over target mask
A_completion = max(A_signed, 0)

σside = median(|y_side−median(y_side)|) / 0.67448975
U = 3 × σside × (2h) = 0.6 × σside
```

Only sampled grid points inside the mask are integrated; there is no interpolation of boundary points onto exactly 5.70/5.90. Boundaries remain fixed throughout the nominal run; they do not move with the apex. U uses the nominal 0.20 ppm width. No further baseline fit or smoothing is applied by `fixed_window_measurement()`.

`max(A_signed,0)` is **not** the same as integrating positive-only intensities. A small negative signed integral can produce `completion_area=0` only after passing independent noise, uncertainty and negative-lobe gates. That is distinct from assigning zero because detection failed.

U is a conservative noise-based bound. It is **not a 95% confidence interval**, fitted-area standard error, bootstrap error, or quadrature sum of independent-bin errors. It does not explicitly estimate systematic baseline/phase uncertainty. The slope fit also has no confidence interval or uncertainty weighting.

Areas have numerical units of processed intensity × ppm, presented as arbitrary units. A sequence `100,80,50,20,5,2` means the same fixed-window signal has decreased to 80%, 50%, 20%, 5%, 2% of its initial integral. It does not directly mean concentration, percent conversion or molar yield: no internal-standard/calibration conversion is applied by this controller.

### Complete individual-measurement gates

All of these are applied before a row enters stage history:

1. Nonempty acquired file, successful production processing, exactly one retained evidence file and readable matching processing metadata.
2. Recorded raw SHA-256 matches the actual acquisition.
3. Per-spectrum maximum normalization is rejected; phase method must not be `none`; retained P0/P1 must be finite. This checks phase provenance, not a newly fitted phase confidence score.
4. Equal-length finite x/y, strictly increasing x, at least **5** target grid points and **10** sideband points.
5. Target `max(abs(y))>0`, finite positive measurable sideband noise. Exactly noiseless/zero traces do not establish absence.
6. The experiment reference must have `peak_clear=True` and selected `peak_area>0`; reference height and integral must be finite and positive.
7. `σside/reference_height ≤ 0.01` and `U/reference_area ≤ 0.005`.
8. `A_signed ≥ −U` and `A_negative ≤ U`.
9. Either a detected adapter-passing peak, or every bounded-absence condition above.
10. JCAMP `LONG DATE` must parse as `%Y/%m/%d %H:%M:%S%z` and be strictly later than the previous run acquisition. No filename/modification-time substitution.

`reference_height` is first spectrum's maximum absolute target-trace height, not necessarily the selected discrete peak height. `reference_area` is its fixed-window completion integral. In live `Services.nmr_measurement()`, the explicit reference is **`rows[0]` across the experiment**, so these measurement bounds do not reset at Stage 2. Stage completion references do reset as described below. Sources: [fixed_window_measurement](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_measurement.py:12>), [measurement orchestration/reference/timing](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:424>).

## 5. Stage 1: every endpoint condition

Let A_i=`completion_area`, U_i=`area_uncertainty`, and t_i=hours from **first Stage 1 JCAMP acquisition**. Let S=A_1. For a recent 4-observation window W ending at observation e:

```text
upper_W = max_W(A_i + U_i)
lower_W = min_W(A_i − U_i)
R_W = (upper_W − lower_W) / S
b_W = least-squares slope of (A_i/S) against (t_i−first_window_time)
P_W = (A_1 − upper_W) / S
```

The slope is exactly `np.polyfit(t−t[0], a/scale, 1)[0]`; equivalently Σ(t−t̄)(A/S−mean(A/S))/Σ(t−t̄)^2.

| Criterion | Equation / threshold | Nominal config key under `workflow.initial_stage.completion` | Why it exists |
|---|---|---|---|
| Entire history valid | No `error`; each row measurement-valid (fallback `peak_clear`); all A/U finite and ≥0 | Code invariant | Endpoint cannot rely on invalid data. |
| Timing valid | Strictly increasing metadata times | Code invariant | Valid time-based slope/duration. |
| Initial detected signal | A_1>0 and first row `peak_clear=True` | Code invariant | Establish decreasing reference. No separate absolute completion-integral floor besides >0. |
| Enough observations at each end | e≥8 | `minimum_points: 8` | History requirement is per qualifying endpoint, not just final count. |
| Enough elapsed time at each end | t_e≥12 h | `minimum_duration_hours: 12` | Adequate temporal observation. |
| Low remaining signal | upper_W/S≤0.025 | `low_fraction: 0.025` | Every window point's upper bound must be low. |
| Material decline | P_W≥0.95 | `minimum_progress_fraction: 0.95` | Decline relative to first Stage 1 integral. With the low gate, this is numerically redundant at nominal settings but is still checked. |
| Stable range | R_W≤0.01 | `max_window_range_fraction: 0.01` | Bound uncertainty-expanded variation to 1% of first area. |
| Stable slope | abs(b_W)≤0.001/h | `max_abs_slope_fraction_per_hour: 0.001` | Limit trend to 0.1% of first area per hour. No slope-CI test. |
| Persistence | Every latest 3 overlapping W passes | `window_points: 4`, `consecutive_confirmations: 3` | One stable or low point/window is insufficient. |

All equations/checks are implemented in [stage_completion.completion_evidence](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_completion.py:40>); settings are in [nominal Stage 1 YAML](</C:/code/chemyx_pump/config_templates/experiments/si6_two_stage_nominal.yaml:21>).

```text
stage1_complete = history_valid AND timing_valid AND initial_detected_positive
                  AND pass(window ending N−2)
                  AND pass(window ending N−1)
                  AND pass(window ending N)

pass(W_e) = (e≥8) AND (t_e≥12)
            AND (upper_W/S≤0.025) AND (P_W≥0.95)
            AND (R_W≤0.01) AND (abs(b_W)≤0.001)
```

There is no whole-history monotonic-decline requirement, no fitted kinetic completion time, no slope significance/confidence-interval requirement, and no absolute low-area constant. Endpoint normalization uses the **first Stage 1 area**, not a later maximum.

## 6. Two Stage 1 quantitative examples

These are hypothetical already-QC-qualified metric histories, not acquired or generated spectra. They were passed directly to the production endpoint function. For all four examples, U=0.10 area units, all rows are valid/detected, no errors occur, and metadata times are strictly increasing. Illustrative measurement-reference A_ref=100, H_ref=1000, σside=1/6 yields U=3×(1/6)×0.20=0.10; noise/reference height=0.01667%≤1%, uncertainty/reference area=0.10%≤0.5%. The examples assume the remaining spectrum/provenance/peak gates have passed; scalar histories alone cannot prove those gates.

### A. Completes

```text
observation: 1   2   3   4    5    6    7     8     9    10
area:       100 62  31  12   1.8  1.5  1.4   1.35  1.3  1.3
time(h):     0  2   4   6    8   10   12    14    16   18
```

S=100; final area=1.3, final signed/qualified integral interpreted as 1.3±0.1. Center-value decline is 98.7%; the endpoint uses the upper-bound-based window progress below.

| Window end / included observations | upper_W | lower_W | Progress P_W | R_W | b_W (/h) | Point/time minimums | All conditions |
|---|---:|---:|---:|---:|---:|---|---|
| 8 / 5–8 | 1.90 | 1.25 | 0.981 = 98.1% | 0.00650 = 0.650% | −0.000725 | 8 points; 14 h | PASS |
| 9 / 6–9 | 1.60 | 1.20 | 0.984 = 98.4% | 0.00400 = 0.400% | −0.000325 | 9 points; 16 h | PASS |
| 10 / 7–10 | 1.50 | 1.20 | 0.985 = 98.5% | 0.00300 = 0.300% | −0.000175 | 10 points; 18 h | PASS |

All upper_W values are ≤2.5; every range ≤1.0%; every absolute slope ≤0.001/h. At N=8 the latest three windows end at 6,7,8, and only end 8 passes. At N=9 there are two consecutive passes. **N=10 is the first complete observation.** The initial signal, finite/history and timing gates also pass by construction.

### B. Still declining; does not complete

```text
area:    100,60,30,12,11,10,9,8,7,6
time(h):  0, 2, 4, 6, 8,10,12,14,16,18
```

| Window end | upper_W | Progress | Low upper bound ≤2.5? | R_W≤0.01? | abs(b_W)≤0.001/h? |
|---|---:|---:|---|---|---|
| 8 | 11.1 | 0.889 | NO | 0.032: NO | 0.005: NO |
| 9 | 10.1 | 0.899 | NO | 0.032: NO | 0.005: NO |
| 10 | 9.1 | 0.909 | NO | 0.032: NO | 0.005: NO |

History/initial/timing and point/time requirements pass, but all three windows fail low, ≥95% progress, range and slope. The final 6 is a 94% center-value decline, not completion. An initially low constant series also fails: its relative remaining signal is approximately 100% of its own starting area, not ≤2.5%.

## 7. Stage 2: every endpoint condition

Now use **only Stage 2 rows**: baseline B=A_1 and S=max(A_i) across all Stage 2 observations seen at the current evaluation. Let W contain 5 observations. upper_W, lower_W, R_W and b_W are calculated as above using this S.

```text
P_W = (lower_W − B) / S
near_maximum_W = lower_W / S ≥ 1−0.02 = 0.98
```

| Criterion | Equation / threshold | Nominal config key under `workflow.first_addition_stage.completion` | Why it exists |
|---|---|---|---|
| Entire history valid | Same row/error/finite/nonnegative gates as Stage 1 | Code invariant | Trustworthy endpoint history. |
| Timing valid | Strictly increasing metadata acquisition times | Code invariant | Valid elapsed times/slope. |
| Detected positive signal exists | S>0 and at least one Stage 2 row `peak_clear=True` | Code invariant | All nondetection/zero histories cannot finish. The code checks any detected row, not an explicit detection-after-baseline index. |
| Enough observations | e≥8 at each window endpoint | `minimum_points: 8` | Sustained evidence. |
| Enough elapsed time | t_e≥3 h after first Stage 2 acquisition | `minimum_duration_hours: 3` | Minimum observation period. |
| Material growth | (lower_W−B)/S≥0.25 | `minimum_progress_fraction: 0.25` | Growth must be ≥25% of observed maximum, not 25% of baseline. |
| Near observed maximum | lower_W/S≥0.98 | Reuses `max_window_range_fraction: 0.02` | A stable low level after a high peak is not successful high plateau. |
| Stable range | (upper_W−lower_W)/S≤0.02 | `max_window_range_fraction: 0.02` | Uncertainty-inclusive range ≤2% of maximum. |
| Stable slope | abs(b_W)≤0.012/h | `max_abs_slope_fraction_per_hour: 0.012` | Trend ≤1.2% of maximum per hour. |
| Persistence | Latest 3 overlapping five-point windows all pass | `window_points: 5`, `consecutive_confirmations: 3` | Sustained plateau. |

Source: [completion_evidence](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_completion.py:40>), [nominal Stage 2 YAML](</C:/code/chemyx_pump/config_templates/experiments/si6_two_stage_nominal.yaml:40>).

```text
stage2_complete = history_valid AND timing_valid
                  AND (maximum_area>0) AND any_detected_Stage2_row
                  AND pass(window ending N−2)
                  AND pass(window ending N−1)
                  AND pass(window ending N)

pass(W_e) = (e≥8) AND (t_e≥3)
            AND ((lower_W−B)/S≥0.25) AND (lower_W/S≥0.98)
            AND (R_W≤0.02) AND (abs(b_W)≤0.012)
```

A zero/low baseline is **allowed**, but not **required**. There is no configured upper limit on Stage 2 starting area. No fixed absolute growth amount is required. Uncertainty is included in lower-bound progress/near-maximum and upper-minus-lower range; slope uses center areas only. No whole-history monotonic rise or slope confidence interval is required. A flat baseline fails the progress test. `expected_duration_hours: 3` is descriptive, not a separate stop/authorization test.

## 8. Two Stage 2 quantitative examples

### A. Completes

```text
observation: 1  2  3   4   5   6   7   8     9     10   11     12
area:        1  8 30  60  82  98  99  98.8  99.1  99   99.05  99
time(h):     0 .5  1 1.5   2 2.5   3   3.5   4     4.5  5      5.5
```

B=1; maximum S=99.1; final center growth=98 area units. U=0.1; every window's lower bound must be ≥0.98×99.1=**97.118**.

| Window end / included observations | upper_W | lower_W | Progress P_W | lower_W/S | R_W | b_W (/h) |
|---|---:|---:|---:|---:|---:|---:|
| 10 / 6–10 | 99.20 | 97.90 | 0.977800 | 0.987891 | 0.013118 | +0.00423814 |
| 11 / 7–11 | 99.20 | 98.70 | 0.985873 | 0.995964 | 0.005045 | +0.00060545 |
| 12 / 8–12 | 99.20 | 98.70 | 0.985873 | 0.995964 | 0.005045 | +0.00070636 |

Each has at least 8 points, elapsed time≥3 h, growth≥0.25, lower_W/S≥0.98, range≤0.02 and slope≤0.012/h. At N=9 the latest window still includes area 82: its range=0.174571, near-maximum fails, and slope=0.070636/h fails. The first passing window ends at 10; then 11; **observation 12 at 5.5 h completes Stage 2**.

### B. Grows, then falls to a lower flat level: does not complete

```text
area:    1,8,30,60,100,30,30,30,30,30,30,30
time(h): 0,.5,1,1.5,2,2.5,3,3.5,4,4.5,5,5.5
```

B=1 and S remains **100**, because the prior maximum is retained in the full Stage 2 history. Windows ending 10,11,12 contain only 30s. For all three:

```text
lower_W=29.9; upper_W=30.1
progress=(29.9−1)/100=0.289          → PASS ≥0.25
range=(30.1−29.9)/100=0.002         → PASS ≤0.02
slope≈0                            → PASS ≤0.012/h
point/time/valid/detected gates     → PASS
near maximum=29.9/100=0.299         → FAIL ≥0.98
```

**No completion**, despite three stable windows and sufficient growth relative to baseline. This isolates the intended protection against a high signal declining into a low plateau. A purely decreasing `100,80,60,40,30,...` history additionally fails growth because (lower_W−100)/100 is negative.

## 9. Exact three-window behavior

At observation N, the function recomputes windows ending at **N−2, N−1, N**. Windows overlap by 3 observations in Stage 1 and 4 in Stage 2. Stage 1's three four-point windows cover 6 distinct recent observations; Stage 2's three five-point windows cover 7.

Every window must individually satisfy its endpoint's minimum observation count/time, all point bounds as encoded by max/min, progress, range and slope. The function does **not** maintain an incrementing persistent counter. `complete=all(check['passed'] for check in checks)`. A new failure breaks the conjunction. For Stage 2 all three windows are recalculated using the **current full-history maximum**, so a newly higher maximum can invalidate a previously passing window.

The function initially returns insufficient evidence below `max(minimum_points, window_points+consecutive_confirmations−1)`: 8 observations for either nominal stage. However, each of the three endpoints must also be ≥8, so the first possible complete result is `N−2≥8`, i.e. **N≥10**. The profile validator independently ensures enough slots using `max(minimum_points,window_points)+consecutive_confirmations−1`.

At ideal nominal spacing from first metadata acquisition: Stage 1's first three eligible endpoints are 8/9/10 at **14/16/18 h**; Stage 2's are 8/9/10 at **3.5/4/4.5 h**. These are earliest possible counts/times, not predicted reaction completion. The passing Stage 2 example first reaches its stable region later and therefore completes at 12.

Sources: [completion implementation](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_completion.py:40>), [profile schedule feasibility](</C:/code/chemyx_pump/chemyx_lab/workflows/si6_profile.py:12>).

## 10. Measurement QC versus chemical completion and failures

**Measurement QC asks:** does this acquisition have trustworthy phase/baseline/provenance/peak or bounded-low-signal evidence and sufficiently small noise/uncertainty?

**Endpoint logic asks:** given the trustworthy same-stage integrals and acquisition times, do the last three windows demonstrate the configured decreasing-low or increasing-high-stable endpoint?

A valid measurement may fail every endpoint gate; that simply keeps monitoring until completion or the configured limit. A failed measurement is different:

- It is not appended to the successful history and is not interpreted as zero.
- The runner does not quietly skip it, reset a counter and continue, or automatically repeat acquisition.
- `sample_cycle()` attempts the usual return/clear sequence only after pump STOP, needle DOWN, expected per-channel volumes and journal state are proven; it then raises `MeasurementFailedAfterCleanup`.
- `run_experiment()` stops as `ANALYSIS_INCONCLUSIVE` with operator review required. If physical certainty or cleanup is lost, available STOPs are attempted and manual inspection is required.
- If an invalid row is manually supplied to `completion_evidence()`, the function returns incomplete rather than filtering it out (after its initial count prerequisite).

Sources: [measurement append/validity](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:424>), [sample failure/cleanup](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:536>), [terminal outcome](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:1112>).

### Transition authorization and time limits

Stage 1 success is calculated before sample cleanup, but advancement waits until cleanup returns successfully. `run_experiment.stage_runner.finish()` requires both `StageOutcome.PLATEAU_REACHED` and a fresh complete Stage 1 evidence calculation, plus a durable dose reservation. It records dispatch intent before boundary motion, executes DOWN → Ch2 infusion → UP, and requires exactly one completed Ch2 infusion after intent in the valid replayed journal before recording confirmed dose. Stage 2 admission depends on that confirmation and its operator readiness checkpoint.

Nominal Stage 1/2 start-to-start schedules are 120/30 min, ceilings 48/6 h. The scheduler uses monotonic time from stage entry; completion duration/slope uses LONG DATE from the first stage acquisition. These clocks/origins are distinct. The nominal slot counts are **24/12**, not 25/13: no new cycle starts at the exact ceiling. An already-started cycle can finish past the ceiling and its qualifying endpoint can count. A limit without chemical completion returns inconclusive and does not authorize dose or operator ADVANCE.

Sources: [transition gate](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:1140>), [monitoring scheduler](</C:/code/chemyx_pump/chemyx_lab/workflows/si6_automated_nmr.py:1395>), [dose reservation](</C:/code/chemyx_pump/chemyx_lab/workflows/dose_guard.py:1>).

## 11. Exact numerical controls in one table

Abbreviations: **E** = `config_templates/experiments/si6_two_stage_nominal.yaml`; **P** = `configs/nmr/analysis.yaml`. `S1` means `workflow.initial_stage`; `S2` means `workflow.first_addition_stage`. Units of intensity/integral are arbitrary processed units. Code literals/defaults are identified explicitly.

| Parameter | Stage | Value | Units | Config key / code source | Used by |
|---|---|---:|---|---|---|
| Nominal tracked target | Both | 5.8 | ppm | E `nmr.target_ppm` | Adapter/fixed integration |
| Detection half-width | Both | 0.10 | ppm | E `analysis.detection_window_ppm` | Simple-table selection/adapter |
| Integration half-width / bounds | Both | 0.10 / 5.70–5.90 | ppm | E `analysis.integration_window_ppm` plus target | Fixed integral |
| Sideband outer half-width / bounds | Both | 0.5 / 5.30–5.70, 5.90–6.30 | ppm | E `analysis.plot_window_ppm` | Fixed-integral noise estimate |
| Full detection region | Both | 5.0–6.5 | ppm | P `regional_analysis.ppm_min/max`; runner `PROCESS_FID_REGION` CLI override | Production processor |
| Line broadening for quantitative processor | Both | 0.03 | Hz | P `processing.line_broadening_hz` | Complex FID apodization |
| Zero-fill minimum | Both | 65536 | points | P `processing.zero_fill_points` | FFT preparation |
| Phase method / direction | Both | stored / inverse | — | P `processing.phase_method`; processor default `direct_phase=False` | Phased real |
| Baseline / normalization / reference | Both | ALS / none / metadata | — | P `processing.baseline_method`, `normalization`, `reference_method` | Spectrum processing |
| Truncation taper | Both | none | — | P `processing.truncation_window` | FID preprocessing |
| ALS smoothness / asymmetry / iterations | Both | 10^6 / 0.001 / 10 | algorithm controls | `nmr.asymmetric_least_squares_baseline` defaults | Global baseline |
| Regional baseline degree | Both | 3 | polynomial degree | P `processing.baseline_polynomial_order` | Regional baseline |
| Baseline fit clipping / iteration limit | Both | −4σ, +2.5σ / 10 | sigma / iterations | `nmr.pick_spectrum_region` literals | Baseline refit selection |
| Detection smoothing / minimum grid window | Both | 0.006 / 5 | ppm / points | P `processing.smoothing_window_ppm`; code literal | Detection only |
| Candidate prominence floor | Both | 5 | noise sigma | P `regional_analysis.min_prominence_snr` | SciPy candidate finding |
| Minimum candidate separation | Both | 0.04 | ppm | P `regional_analysis.min_peak_distance_ppm` | SciPy candidate finding |
| Minimum candidate width | Both | 0.015 | ppm | P `regional_analysis.min_peak_width_ppm` | SciPy candidate finding / peak integration |
| Manual processor QC enabled | Both | true | Boolean | P `peak_qc.use_manual_thresholds` | Activates YAML QC values |
| Processor height SNR | Both | ≥3 | dimensionless | P `peak_qc.min_snr` | Candidate QC |
| Processor prominence SNR | Both | ≥3 | dimensionless | P `peak_qc.min_prominence_snr` | Candidate QC |
| Processor width bounds | Both | 1–10 | Hz | P `peak_qc.min_width_hz`, `max_width_hz` | Candidate QC |
| Positive-only candidate area | Both | >0 | intensity×ppm | P `peak_qc.require_positive_area: true` | Candidate QC |
| Adapter height SNR | Both | ≥5 | dimensionless | E `analysis.min_peak_snr` | Detected measurement acceptance |
| Adapter prominence SNR | Both | ≥3 | dimensionless | E `analysis.min_prominence_snr` | Detected measurement acceptance |
| Selected peak-area minimum | Both | max(0,10^-12) | intensity×ppm | E `analysis.min_peak_area`, `area_epsilon` | Detected measurement acceptance |
| Fixed target/sideband grid minima | Both | 5 / 10 | points | `fixed_window_measurement` literals | Integral/noise support |
| Sideband MAD divisor | Both | 0.67448975 | dimensionless | `fixed_window_measurement` literal | σside |
| Area-noise multiplier | Both | 3 | dimensionless | E `analysis.measurement_qc.noise_multiplier` | U=3σ×0.20 ppm |
| Noise/reference-height maximum | Both | 0.01 = 1% | fraction | E `analysis.measurement_qc.max_noise_fraction` | Measurement QC |
| Uncertainty/reference-area maximum | Both | 0.005 = 0.5% | fraction | E `analysis.measurement_qc.max_area_uncertainty_fraction` | Measurement QC |
| Undetected upper bound | Both | 0.025 = 2.5% | fraction of experiment QC reference | E `analysis.measurement_qc.undetected_max_fraction` | Bounded absence |
| Signed negative / negative-lobe bounds | Both | A_signed≥−U / A_negative≤U | intensity×ppm | `fixed_window_measurement` code | Phase/baseline consistency |
| Minimum history observations | S1 / S2 | 8 / 8 | observations | E `S1/S2.completion.minimum_points` | Every qualifying endpoint |
| Minimum observation duration | S1 / S2 | 12 / 3 | hours from first stage acquisition | E `S1/S2.completion.minimum_duration_hours` | Every qualifying endpoint |
| Rolling window size | S1 / S2 | 4 / 5 | observations | E `S1/S2.completion.window_points` | Endpoint windows |
| Consecutive windows | S1 / S2 | 3 / 3 | windows | E `S1/S2.completion.consecutive_confirmations` | Persistence |
| Minimum decline / growth | S1 / S2 | 0.95 / 0.25 | fraction of first S1 / maximum S2 integral | E `S1/S2.completion.minimum_progress_fraction` | Upper/lower-bound progress |
| Low upper-bound region | S1 | 0.025 = 2.5% | fraction of first S1 integral | E `S1.completion.low_fraction` | Decreasing endpoint |
| Expanded-window range maximum | S1 / S2 | 0.01 / 0.02 | fraction of first S1 / maximum S2 integral | E `S1/S2.completion.max_window_range_fraction` | Stability |
| Near-maximum lower bound | S2 | 0.98 = 98% | fraction of observed S2 maximum | Derived `1−S2.completion.max_window_range_fraction` | Lower-plateau rejection |
| Absolute normalized slope maximum | S1 / S2 | 0.001 / 0.012 | fraction/hour | E `S1/S2.completion.max_abs_slope_fraction_per_hour` | Stability |
| Sampling cadence | S1 / S2 | 120 / 30 | minutes start-to-start | E `S1/S2.interval_minutes` | Scheduler |
| Immediate first sample | S1 / S2 | true / true | Boolean | E `S1/S2.measure_immediately` | Scheduler |
| Maximum stage duration / nominal slots | S1 / S2 | 48 / 6; 24 / 12 | hours / cycle-start slots | E `S1/S2.max_hours`; schedule derivation | Scheduler cutoff |
| Expected Stage 2 duration | S2 | 3 | hours | E `S2.completion.expected_duration_hours` | Descriptive only |
| Automatic completion enabled | Both | true | Boolean | E stage `plateau_stopping_enabled`; `three_instrument.initial_plateau_stopping_enabled` overrides S1 | Controller |
| NMR scans / receiver gain / auto gain | Both | 8 / 12 / false | count / gain / Boolean | E `nmr.scans`, `receiver_gain`, `auto_gain` | Acquisition; fixed-gain admission |
| Acquisition spectral center / sweep width | Both | 5 / 20 | ppm | E `nmr.spectral_center`, `sweep_width` | Acquisition; processed axis reads actual metadata |
| Pre-NMR settling | Both | 300 | seconds | E `workflow.cycle[3].seconds` | Sampling cycle |
| Boundary dose / rate | Transition | 1.8 / 1.0 | mL / mL/min | E `S1.after_monitoring[1].volume_ml`, `rate_ml_min` | One Ch2 infusion after completion |

These keys can be checked directly in [E](</C:/code/chemyx_pump/config_templates/experiments/si6_two_stage_nominal.yaml>) and [P](</C:/code/chemyx_pump/configs/nmr/analysis.yaml>); algorithm constants are in [nmr.py](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1336>) and [stage_measurement.py](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_measurement.py:12>).

### Values present elsewhere that do not control this endpoint

- E `analysis.line_broadening_hz: 0.3` affects the separate magnitude spectrum diagnostics; the quantitative processor uses P's **0.03 Hz**.
- E `analysis.plateau_max_growth_percent: 5`, `plateau_max_decline_percent: 2`, `plateau_consecutive_intervals: 3` control the legacy `plateau_reached` branch. This two-stage branch calls `completion_evidence` instead. Some journal messages still label `required_intervals` with the legacy field.
- P `common.target_ppm: 6.1` belongs to fixed-target analysis tools, not the live two-stage target.
- P `target_peak.expected_center_ppm: 5.79`, label `5.7 ppm`, drift **0.08 ppm**, minimum SNR **8**, prominence SNR **5**, and its completion thresholds (recent window 4, minimum observations 6, minimum elapsed 1 h, slopes 5 a.u./h and 10%/h, percent change 5%, low fraction 10%, meaningful trend 25%, 3 stable measurements) belong to supplemental focused reports. They do not replace the controller rules above.
- P `statistics.bootstrap` uses **500** pseudo-Voigt residual bootstrap iterations, confidence **0.95**, seed **12345**. Its plateau has minimum points **4**, slope equivalence **1%/h**, persistence **3**, declining plateau disabled. These are report statistics, not the controller's area uncertainty or endpoint slope-CI test.

Sources: [two-stage versus legacy branch](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:424>), [statistics/focused report invocation](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:1412>), [supplemental YAML](</C:/code/chemyx_pump/configs/nmr/analysis.yaml:268>).

## 12. Source map, implementation caveats and documentation discrepancies

| Responsibility | Actual source |
|---|---|
| Production acquisition | [instrument_operations.run_nmr_acquisition](</C:/code/chemyx_pump/chemyx_lab/workflows/instrument_operations.py:99>) |
| Processor subprocess/config precedence | [run_process_fid_postprocessing](</C:/code/chemyx_pump/chemyx_lab/workflows/si6_automated_nmr.py:748>); [process_fid._resolved_config_mapping](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:84>) |
| Decode/FFT/axis/phase | [read_jcamp_fid](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:325>), [fourier_transform_fid](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1063>), [build_ppm_axis](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:458>), [build_phased_spectrum](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1637>) |
| Baseline/peak identification | [process_spectrum_for_peaks](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:499>), [pick_spectrum_region](</C:/code/chemyx_pump/chemyx_lab/analysis/nmr.py:1336>) |
| Candidate/adapter QC | [_peak_qc](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:871>), [_select_simple_peak_rows](</C:/code/chemyx_pump/scripts/nmr/process_fid.py:1160>), [analyze_tracked_resonance](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:226>) |
| Retained quantitative evidence | [retain_phase_audit](</C:/code/chemyx_pump/chemyx_lab/analysis/phase_audit.py:99>) |
| Fixed integral, uncertainty, measurement QC | [fixed_window_measurement](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_measurement.py:12>) |
| Both stage endpoints and consecutive windows | [completion_evidence](</C:/code/chemyx_pump/chemyx_lab/analysis/stage_completion.py:40>) |
| Timing/reference/endpoint row assembly | [Services.nmr_measurement](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:424>) |
| Sample cleanup / failure | [sample_cycle](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:536>) |
| Dose transition authorization | [run_experiment.stage_runner.finish](</C:/code/chemyx_pump/chemyx_lab/workflows/three_instrument_si6.py:1140>) |
| Profile validation/scheduling | [validate_profile](</C:/code/chemyx_pump/chemyx_lab/workflows/si6_profile.py:12>), [run_monitoring_stage](</C:/code/chemyx_pump/chemyx_lab/workflows/si6_automated_nmr.py:1395>) |
| Stage reports | [si6_stage_reports.write_stage_reports](</C:/code/chemyx_pump/chemyx_lab/analysis/si6_stage_reports.py:9>) recomputes the same completion function and plots fixed integrals, not a separate endpoint algorithm. |

Observed discrepancies/limits, with executable behavior treated as authoritative:

1. **Magnitude descriptions are stale.** The nmr.py module header, `pick_spectrum_region` and `_peak_qc` docstrings, plus some analysis.yaml comments, describe magnitude detection. The active YAML and `process_spectrum_for_peaks` select **real** detection.
2. **Positive-area terminology is imprecise.** `_peak_qc` prose says negative area means rejection, but the actual gate checks **positive-only area>0**, not signed-area>0. Independent fixed-window signed/negative-lobe gates protect the completion measurement. The processor can label a feature unresolved based on signed area without `_peak_qc` explicitly gating that classification.
3. **Manual thresholds differ from built-ins.** analysis.yaml comments say the manual values equal the built-ins, but YAML `min_snr=3` differs from parser built-in **8**. Since the manual switch is true, the processor currently uses **3**, followed by adapter **5**. Turning that switch off changes behavior.
4. **CSV placeholders are not chemical zeros.** Simple-table comments describe a continuous zero-filled trace. The live controller requires independent bounded-absence evidence; it rejects missing/rejected measurements otherwise.
5. **CSV rounding affects peak gates, not the fixed integral.** Simple-table ppm is originally rounded to **3 decimals**, area/height/SNR/prominence-SNR/width to **2 decimals** before the adapter reads it. A later pretty-print pass cannot restore precision. The fixed integral uses retained NPZ arrays directly. Thus the effective selected-peak-area gate also depends on rounding; a tiny positive area can round to zero despite the 10^-12 configured epsilon.
6. **Two reference systems exist.** Live measurement QC uses first experiment spectrum across both stages; Stage 2 endpoint uses first Stage 2 baseline and greatest Stage 2 area. Stage 2's near-zero baseline allowance does not reset the measurement-QC reference.
7. **Three-hour expected duration is not actual earliest completion.** The nominal point/window requirements make Stage 2 completion impossible before observation 10, ideally **4.5 h** from its first acquisition. Stage 1's 12 h minimum similarly does not permit nominal completion before **18 h**. These are consequences of the actual rules, not a code change proposed here.
8. **No slope confidence interval in the controller.** Slope-CI and bootstrap text/tables in supplemental statistics are separate output products. The controller uses an unweighted point-estimate linear slope, plus bounded-area range/progress checks.

## 13. Scientist explanation and decision sequence

### Stage 1

> We begin with a detected, QC-qualified target resonance and a positive fixed 5.70–5.90 ppm integral. Every NMR acquisition measures that same baseline-corrected real-spectrum integral and a sideband-noise-derived uncertainty bound. Stage 1 is complete only when three consecutive overlapping four-observation windows, each with enough history and elapsed time, show uncertainty-inclusive remaining signal no greater than 2.5% of the first Stage 1 integral, at least 95% decline, and sufficiently small range and slope. Sample cleanup must succeed before dosing is authorized.

### Stage 2

> After the confirmed Channel 2 addition, we begin a new stage history whose first trustworthy integral is the Stage 2 baseline. The tracked signal must grow materially and then remain near the greatest valid Stage 2 integral observed. Stage 2 is complete only after three consecutive overlapping five-observation windows meet the point/time requirements, show conservative growth of at least 25% of the observed maximum, stay within 2% below that maximum after uncertainty, and pass range/slope stability gates. A lower flat signal after a high excursion cannot qualify.

```text
NMR acquisition + retrieval
↓
decode FID → apodize/zero-fill → FFT/metadata ppm axis → stored phase
↓
ALS + regional baseline → real-trace peak finding and QC
↓
trusted fixed-window integral + uncertainty + metadata acquisition time
↓
Stage 1 decreasing/low endpoint test
↓
3 passing overlapping windows → finish sample return/cleanup
↓
Stage 1 COMPLETE → durable dose authorization
↓
Channel 2 dose → durable confirmation → Stage 2 readiness checkpoint
↓
repeat acquisition / trusted-integral measurement
↓
Stage 2 increasing / near-maximum endpoint test
↓
3 passing overlapping windows → finish sample return/cleanup
↓
Stage 2 COMPLETE → final reports

Measurement QC failure → proven-safe cleanup when possible → stop/review
Stage time limit without endpoint → inconclusive; transition refused
```

Verification scope: code/configuration inspection, installed nmrglue FFT implementation inspection, and four direct in-memory production endpoint evaluations. No full live run, new spectrum processing run, dependency installation, or code/config change was performed for this audit.
