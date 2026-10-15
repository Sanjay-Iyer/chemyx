# Results And Data

All known result files were retained during migration.

New three-instrument chemistry executions are documented in
[Si6 experiment outputs](SI6_EXPERIMENT_OUTPUTS.md): default
`results/experiments/YYYYMMDD_<workflow.experiment_id>[_N]/`, with independent
`step_1`/`step_2` data and shared metadata/logs/final QC. Existing results below
are historical and remain in place.

The `results/raw/`, `results/analysis/`, and `results/processed/` trees are now
machine-local and ignored by Git. Files already present remain on disk, but new
clones obtain data separately from the code repository.

## Layout

```text
results/
  raw/nmr/06-08-26/
  processed/nmr_analysis/
  results_manifest.csv
  README.md
```

## Manifest

`results/results_manifest.csv` records original path, new path, conservative
provenance, file type, SHA-256 checksum, and notes.

## Preservation Rules

- Do not edit raw `.dx` files in place.
- New runs should write to a new run-specific folder.
- If results are moved, record original path, new path, checksum, and notes.
- Do not infer workflow provenance unless supported by filenames, metadata,
  logs, manifests, or source code.
