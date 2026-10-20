# 07 — Offline comparison of SciPy peak boundaries

[NMR documentation](00_START_HERE.md)

Run the reusable real-data benchmark from the repository root:

```powershell
python scripts\compare_scipy_peak_integration.py
```

Defaults are the real `results/raw/nmr/06-08-26` and `06-09-26` datasets. Results
go to `results/101926_peaks`. If that directory exists, a new uniquely named
subfolder preserves all previous results. Custom inputs and output are supported:

```powershell
python scripts\compare_scipy_peak_integration.py "C:\data\day1" "C:\data\day2" --output-dir "C:\data\boundary_review"
```

This is saved-data analysis with no instrument interfaces. It does not change
the behavior of experiment scripts 06–09 or shared production processing.

Each acquisition is processed once with the production Fourier transform,
ACME P0/P1 optimization and ALS baseline. All detection profiles and new boundary
methods then reuse the same unsmoothed real intensity array. Detection/boundary
smoothing affects identification only. Duplicate primary/RPC exports are
inventoried and counted once. JCAMP acquisition metadata is required; LONG DATE
is preferred, and its original value is retained alongside the parsed time.

The benchmark compares 17 documented detection profiles and these boundaries:

| Method | Meaning |
|---|---|
| Fixed reference | Existing 5.70–5.90 ppm production calculation, including sampled endpoint feet |
| Width .50/.75/.90/.95 | SciPy interpolated crossings at those fractions of prominence |
| Prominence bases | SciPy's two base locations; distant bases can capture unrelated signal |
| Guarded local minima | Outward minima near baseline, bounded by distinct neighboring maxima |
| Adaptive noise return | Sustained return within two production-noise sigma of the local base level |

Candidate selection uses the expected/prior center with a hard drift limit, not
the tallest peak. Close maxima are flagged as a possible envelope; geometry
cannot establish whether they are a multiplet or unrelated overlap. A boundary
guard reached before baseline return is explicitly unreliable. Missing dynamic
detections remain missing, not zero-filled. Every outcome has a diagnostic plot.

All new intervals use endpoint-interpolated intensities, a local linear foot
baseline, positive trapezoidal area and separately saved signed area. The fixed
comparator preserves the original sampled-endpoint convention exactly; a
separate table quantifies the endpoint-interpolation difference. The existing
`peaks_simple` variable-width calculation is an additional comparator using its
original regional polynomial correction, clearly identified as a different
baseline convention.

Start review with `cross_dataset_comparison/findings.md` and the clickable
`review_index.html`, then each day's
`parameter_comparisons/*_methods.png` six-panel figures. Every individual PNG
has an adjacent `_integration.csv` containing the exact samples, baseline and
positive integrand shaded in that figure. Review these files as well:

- `analysis_configuration.json`: complete profiles, production arguments and versions.
- `dataset_inventory.csv` and `dataset_summary.csv`: selected, duplicate, failed and processed exports.
- `acquisition_metadata.csv`: the shared authoritative timing record.
- `fixed_production_parity.csv`: numerical agreement with the production target report.
- `integration_audit.csv`: reported versus plotted area for every result.
- `cross_dataset_comparison/area_comparison.csv`: every observation/profile/method.
- `cross_dataset_comparison/parameter_ranking.csv`: exploratory ranking and its component metrics.
- `plot_manifest.csv`, `verification.json`, `preservation_verification.json`: titles, output counts and unchanged input/production evidence.

Ranking does not reward agreement with the fixed area or smoothness alone.
Historical presence annotations in the existing analysis YAML are provisional
additional evidence, not true peak boundaries or true areas. Known-area
Lorentzian checks are explicitly secondary synthetic evidence. No method is
automatically adopted for production based on this benchmark.
