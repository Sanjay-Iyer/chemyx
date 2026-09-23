# NMR test fixtures

Two unmodified JCAMP-DX spectra used by the three-instrument mocks and tests.
Both have the authoritative `LONG DATE` header.

| File | Source | Tracked resonance (production `process_fid`, window 5.80 +/- 0.10 ppm) |
|---|---|---|
| `tracked_resonance_phsi4_20260810.dx` | `results/runs/automated/chemyx_demo_081026_v3/20260810_171441_si6/raw_nmr/20260810_171806_081626_phsi4_0001_8scan_gain12.dx`, acquired through this workflow's iFlow path (8 scans, gain 12) | 5.792 ppm, SNR 31.2, area 30.69: QC-passing |
| `no_resonance_phsi2_20260609_0900.dx` | `results/raw/nmr/06-09-26/CEC-PhSi2-flow(sequence-0900)-06-09-26.dx`, the 09:00 spectrum recorded before the product formed | best feature 5.714 ppm, SNR 3.3: below the workflow's `analysis.min_peak_snr` |

`MOCK_NMR_FIXTURE` in `chemyx_lab/workflows/three_instrument_si6.py` copies
the first file for every mock acquisition, so mocks exercise the configured
production target window.

Evidence for the target window (`configs/nmr/analysis.yaml` `target_peak` and
`simple_table`; `results/README.md`): every operator-confirmed spectrum in the
06-08-26 and 06-09-26 series has this resonance at 5.785-5.847 ppm on the
metadata-derived ppm axis that production uses. The older 6.1 ppm value is
the same resonance on a vendor display with a shifted reference axis.
