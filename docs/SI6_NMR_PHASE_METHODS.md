# Quantitative NMR phase selection

The current operator entry is [SI6_START_HERE](SI6_START_HERE.md). Edit only
`nmr.processing.phase_method` in `configs/experiments/si6_real_two_stage.yaml`:

```yaml
nmr:
  processing:
    phase_method: acme
    phase_comparison:
      enabled: false
      methods: [stored, acme, combined_objective, ernst_p0, deep_phaser]
      include_unphased_reference: true
```

DEFAULT: ACME in the updated real recipe. Change `acme` to `stored` for historical
DX processing, `combined_objective` for the validated custom objective,
`ernst_p0` for analytical P0 only, or `deep_phaser` for the original neural
models/search. Unphased FFT is a research reference and is rejected as a primary.
Historical/development recipes that omit this section retain stored processing.
No new confirmation or acknowledgment flags are introduced.

Changing phase changes real absorption, baseline inputs, candidate detection,
integrated areas and possibly threshold-sensitive completion. No method or
cross-method median is ground truth. The four-observation stage rules, moving
positive-area integration, baseline and identity filters remain unchanged.
This software validation does not commission any method or chemistry threshold
for unattended live operation; verify on the actual offline instrument machine.

The call path is `three_instrument_si6.prepare/open_services` (preflight),
`run_process_fid_postprocessing --phase-method`, `process_fid.main`,
`nmr.build_phased_spectrum`, `phase_methods.apply_phase`, then the existing
`process_spectrum_for_peaks`, retained candidate evidence,
`stage_measurement.tracked_peak_area_observation` and `area_completion_evidence`.
Thus endpoint areas and saved spectra use the same selected method. Full-spectrum
magnitude solvent detection runs independently before quantitative phasing.
Three missing solvent observations latch the configured timed fallback; absent
sample evidence is never target disappearance.

| Method | Implementation and limitations |
|---|---|
| stored | Existing DX `$PHC0/$PHC1`, inverse sign; missing fields retain historical zero defaults. |
| acme | Exact historical validation optimizer: nmrglue 0.11 ACME score, SciPy Nelder-Mead, starting P0/P1 = negative DX PHC0/PHC1 (direct convention), 1000 iterations/2000 evaluations, `xtol=ftol=1e-4`. Shared `phase_optimizers.minimize_acme` prevents drift between validation and primary processing. The DX values are only the initial guess; the optimized result supplies areas. Nonconvergence is a failure. An initial engineering attempt using library-default 400 evaluations and zero start was rejected and replaced with this existing validated path. |
| combined_objective | Existing `combined_objective_v1`; original four equal weights, fixed grid, four bounded Powell starts and tolerances. Stored phases converted to direct starting convention. Custom comparator, not a published universal estimator. |
| ernst_p0 | Existing global complex integral, P1=0; rejects integral cancellation. Baselines, finite limits and first-order dispersion can affect it. |
| deep_phaser | Existing COLMARvista author models/search, reversed descending-ppm input, author maximum normalization, all-true mask. Returned endpoint phases converted back to original ascending FFT with k/N correction. Low-field domain differs from common training examples; inspect results. |

All use `S[k] exp(sign*i*pi/180*(P0+P1*k/N))`, index-zero pivot, ascending
internal ppm, high ppm displayed left. Phase metadata records actual sign,
parameters, optimizer outcome, objective, package versions and elapsed time.
Primary failure aborts through the existing safe cleanup policy, retains an error
summary, supplies no target area and cannot create a chemical endpoint or dose.
There is no silent method substitution.

NumPy/SciPy/nmrglue and their pinned offline wheels support the classical methods.
DEEP uses local Node, TensorFlow.js 4.22.0 and WASM CPU, not required PyTorch or
Python TensorFlow. `offline/resources/deep_phaser/resource_manifest.json`
inventories and checksums the exact Node executable, author JS, two graph JSONs,
two weight shards, TFJS and WASM runtime. These binary resources are separate
ignored deployment assets, included by an offline release build. Missing or
changed resources reject DEEP primary before transports open. Preflight actually
loads both models; inference is additionally bounded to 500 predictions and
900 seconds, with invalid/nonfinite predictions rejected. No model registry or
runtime download is used. Preserve third-party license files in that payload.

Optional comparisons run only after the instrument session exits. Set
`phase_comparison.enabled: true` to request them; initially leave it false.
Alternative failures are recorded with unavailable areas, never substitute for
the primary or change the experiment exit status. No comparison controls motion,
endpoints or dosing. Per-acquisition comparisons reuse the authoritative saved
unphased complex FFT, production baseline and tracker, without copying raw data.
Method lists do not affect primary selection. Comparison processing can take
minutes and must be reviewed on the offline laptop before enabling it.

Per-step `nmr_processed/<acquisition>/phase_comparison/<method>/` holds numerical
real/imaginary arrays, phases, candidates, target measurement and timing. The
comparison root records failures, shared CSV and real/imaginary/target overlays
in PNG/SVG/PDF with visible dataset titles. Step `analysis/phase_method_summary.csv`
and `final_qc/phase_method_comparison` collect area/time, descriptive median
differences, phase quality and runtime. One shared CSV supplies metadata timing;
missing timestamps produce an unavailable record instead of a fabricated plot.
Existing outputs are never overwritten by rerunning comparison.

```powershell
python -B scripts\verify_si6_environment.py --machine-config configs\machines\si6_real_COM4.yaml
python -B scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
python -B scripts\nmr\benchmark_primary_phases.py --output results\<NEW-benchmark-directory>
python -B scripts\nmr\compare_phase_methods.py results\experiments\<dated-run>
python -B scripts\validate_primary_phase_release.py --output results\<NEW-offline-preflight> --deep
```

These commands do not contact hardware. `compare_phase_methods.py` requires comparison
enabled in the saved recipe and retained primary evidence. Benchmarks use all
15 archived June 8/June 9 FIDs when those optional datasets are present. They
measure initialization, phase, downstream, total time, failures and output bytes;
Python allocation tracking excludes native/Node memory. See the final hardening
report for measured results and limitations. Sampling intervals are 7200/1800 s;
hardware acquisition, transfer, pump travel and cleanup time remain instrument-side
measurements. HOME timings alone cannot prove that the complete cycle fits.

`validate_primary_phase_release.py` prohibits Python serial/network connections
and Node network requests, processes the committed fixture with every selected
method, and checks post-session comparison outputs in both stage directories.
Its duplicate fixture references are labeled `OFFLINE-COMPARISON-VALIDATION`,
not experimental reaction data. Use `--deep` to include actual local neural
inference; no model download is attempted.

The October 15 archived-cohort benchmark (15 FIDs per method) measured median /
maximum phase-plus-quantitative-processing times of stored 1.60/4.61 s, ACME
3.75/5.42 s, Combined 19.70/65.11 s, Ernst 1.40/8.99 s, and DEEP
201.93/340.61 s. These reuse the decoded common FFT and exclude the complete CLI
figure/report workload. See the [engineering validation report](SI6_HARDENING_PHASE_VALIDATION_REPORT_2026-10-15.md)
for measurement scope, whole-CLI timing, numerical differences and deployment evidence.
All four classical methods agreed on target detection in all 15 acquisitions;
DEEP missed three weak targets they detected and alone satisfied the retrospective
Stage 1 rule on June 8. Keep ACME as selected; DEEP is implemented and offline
tested but these data do not establish its suitability for unattended endpoints.
