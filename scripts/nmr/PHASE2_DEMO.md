# NMR Processing Demo V2

This is a lightweight, offline desktop demonstration for exploratory processing of
NMReady JCAMP-DX data. It does not control hardware, edit raw `.dx` files, or write
to production analysis folders.

## Phase 1 fallback

`phase1.py` is a frozen copy of the known-good phasing-only desktop demo. It keeps
the real pyqtgraph spectrum, P0/P1/pivot controls, production reset, zoom presets,
and Open `.DX` behavior.

```powershell
cd C:\code\chemyx_pump
conda activate ai
python scripts\nmr\phase1.py
```

## Phase 2

Phase 2 retains the Phase 1 controls and adds:

- production AsLS baseline correction on/off;
- one baseline-smoothness slider (`log10(lambda)` from 4 through 8; production is
  `lambda = 1.0e6`, with asymmetry `0.001` and 10 iterations);
- a numeric ppm reference shift that changes the x-axis only;
- a draggable green integration region with a live local-baseline area;
- section resets and Reset all to production;
- exploratory settings JSON and processed-spectrum CSV exports; and
- confirmed execution of the existing regional peak/integration analysis helpers,
  with a compact result summary.

Launch it with:

```powershell
cd C:\code\chemyx_pump
conda activate ai
python scripts\nmr\phase2.py
```

An optional `.dx` path can be supplied as the first argument.

## Default real test data

The default is the audited August 10 5:15 pull:

```text
results/runs/automated/chemyx_demo_081026_v3/
  20260810_171441_si6/raw_nmr/
  20260810_171806_081626_phsi4_0001_8scan_gain12.dx
```

Its authoritative JCAMP `LONG DATE` is `2026-08-10 17:27:21`. The file records
8 scans and gain 12. At production processing settings, the smoke-test analysis
found a target at 5.791652 ppm, height 423.2223, SNR 31.2339, linewidth 3.9797 Hz,
Stage-1 area 30.6937, and fixed/manual 5.70–5.90 ppm area 33.7758.

## Using the controls

Use Full spectrum, Product region, or Reference region to change the view. Adjust
P0, P1, and pivot for phase. Toggle the baseline checkbox or move Baseline
smoothness to inspect the production AsLS correction. Reference shift moves ppm
coordinates without changing intensity. Drag either green integration boundary to
update the displayed area.

The status reads `PRODUCTION SETTINGS` only when phase, reference, baseline, and
integration values all match their defaults. Otherwise it reads `EXPLORATORY
SETTINGS` and summarizes the changes.

`SAVE PROCESSING SETTINGS` writes a reproducible JSON record. `EXPORT CURRENT
SPECTRUM CSV` writes `ppm,intensity`. `RUN ANALYSIS WITH CURRENT SETTINGS` asks for
confirmation, then writes `settings.json`, `summary.json`, and `peaks_simple.csv`
in a new analysis directory.

All of these outputs are unique and isolated under:

```text
results/nmr_phase_demo_exports/
```

No production result is replaced. Acquisition time is recorded only when the
JCAMP `LONG DATE` field is available; filename schedule tokens and file mtime are
not presented as acquisition time.

## Scope and limitations

This is deliberately a first chemist-facing demo, not a full NMR application. It
has one spectrum, one baseline method, one manual integration region, and a small
analysis summary. It does not include multiplet fitting, peak deconvolution,
J-coupling analysis, structure assignment, 2D NMR, undo/redo, or project storage.
