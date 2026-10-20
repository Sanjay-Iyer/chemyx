# 05 — NMR analysis

[All documentation](../00_START_HERE.md)

For spectra from the fixed-time experiment, start with
[03 — Post-run fixed-time analysis](../03_fixed_time_experiment/03_post_run_analysis.md):

```powershell
python scripts\05_analyze_fixed_time_run.py
```

| Guide | Purpose |
|---|---|
| [01 — Phase methods](01_phase_methods.md) | ACME and alternative phase processing; older live endpoint settings are workflow-specific |
| [02 — Processing guide](02_processing_guide.md) | General saved-data processing |
| [03 — Statistics reference](03_statistics_reference.md) | Definitions and limitations of numerical metrics |
| [04 — Target peak workflow](04_target_peak_workflow.md) | Focused analysis and dataset-aware plots |
| [05 — Phase review](05_phase_review.md) | Saved-file review interface |
| [06 — Results and data](06_results_and_data.md) | Historical/general data layout; current fixed-time outputs use `results/fixed_time` |
| [07 — SciPy peak-boundary validation](07_scipy_peak_integration_validation.md) | Offline real-data comparison of fixed and dynamic integration; plots and numerical audits |

The underlying processor is [scripts/nmr/process_fid.py](../../scripts/nmr/process_fid.py).
Shared processing defaults are in [configs/nmr/analysis.yaml](../../configs/nmr/analysis.yaml).
The fixed-time wrapper uses its run identity for plot titles and ACME by default.
Actual acquisition time comes from JCAMP metadata (prefer LONG DATE), not the
nominal filename schedule. These analysis tools do not control the fixed-time clocks.
