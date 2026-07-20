# Results

This directory preserves raw and processed data moved during the 2026-07-20
repository restructuring.

- `raw/nmr/06-08-26/`: original raw NMR `.dx` files.
- `processed/nmr_analysis/`: existing local processed analysis outputs, CSV
  files, manifests, and plots.
- `results_manifest.csv`: original path, new path, workflow/provenance note,
  file type, SHA-256 checksum, and preservation notes.

Raw data should not be edited in place. If a future workflow produces new
outputs, write them to a new run-specific folder and update or generate a
manifest.
