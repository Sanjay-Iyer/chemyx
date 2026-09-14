# Desktop NMR phase demo

This is a lightweight, exploratory PySide6 and pyqtgraph viewer for testing interactive zero- and first-order phase adjustment on real NMReady JCAMP-DX data. It reuses `build_processing_inspection` for JCAMP decoding, FACTOR scaling, complex FID construction, 0.03 Hz line broadening, 65,536-point zero filling, FFT/fftshift, ppm-axis construction, and production phase metadata.

## Launch

```powershell
cd C:\code\chemyx_pump
conda activate ai
python scripts\nmr\desktop_nmr_phase_demo.py
```

The default file is the Aug. 10 5:15 pull:

`results/runs/automated/chemyx_demo_081026_v3/20260810_171441_si6/raw_nmr/20260810_171806_081626_phsi4_0001_8scan_gain12.dx`

PySide6, pyqtgraph, NumPy, SciPy, and nmrglue are already available in the `ai` environment.

## Controls

- Drag Phase 0 or Phase 1 for live in-memory phasing with the production `inv=True` convention.
- Change Pivot ppm to reference the P1 ramp around another position. The production default is the ppm at nmrglue array index zero, so Reset exactly reproduces production.
- Use Full spectrum, Product region, or Reference region for quick zooms.
- Enable the production reference trace for an optional comparison.
- Open another real `.dx` file with the Qt file dialog.
- Reset restores the phase and pivot read from the current file's production metadata.

This first demo has no baseline tuning and performs no file exports or production writes.
