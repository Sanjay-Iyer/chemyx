# Interactive viewer

## Notebook (recommended)

From the repository root in the `ai` environment:

```powershell
conda activate ai
jupyter lab notebooks/interactive_nmr_processing_explorer.ipynb
```

The notebook provides a six-file selector; p0/p1 controls; production/manual reference selection and Δppm; none, production AsLS, ABD polynomial, and arPLS baselines; lambda, asymmetry, iteration and polynomial-degree controls; full/reference/silane/starting-material/product zooms; display-mode and boundary toggles; live Stage-1/fixed/left/starting/silane values; reset-to-production; and safe PNG/JSON export.

## Standalone app

```powershell
conda activate ai
python scripts/nmr/interactive_processing_explorer.py
```

Pass a `.dx` path as the positional argument to open another spectrum. The standalone Matplotlib app provides fast phase, baseline-method, view, zoom, boundary toggles, and production reset. Use the notebook for the complete parameter and export interface.

All outputs are exploratory only. Export paths are constrained to `results/nmr_processing_inspection/chemyx_demo_081026_v3_plot_cleanup/interactive/exports`; production results and both audit packages cannot be selected as export targets.
