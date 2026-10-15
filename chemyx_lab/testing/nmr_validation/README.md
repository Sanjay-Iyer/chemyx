# Maintained validation adapters

`algorithm_validation.py` and its `robustness_refinement.py` dependency are the
maintained implementation used by `scripts/nmr/run_algorithm_validation.py`.
Their mathematical/report helpers were copied without numerical changes from
the October 4 package. Repository-root discovery, independent output defaults,
archive-write protection and optional Git provenance are maintenance changes.

Original files in `results/100426_algovalidation/code` are immutable historical
provenance. They are intentionally retained, never imported by the active
entry point. `finalize_validation_audit.py` is a top-level, package-specific
evidence finalizer, not reusable runtime code; it remains archived only.
Executing it would write historical audit outputs, so current validation checks
its source integrity/compilation without rerunning it against the archive.

The prior review incorrectly named `validate_si6_synthetic_analysis.py` as an
archive importer. It already used `chemyx_lab.testing.si6_synthetic_analysis`;
the direct dependency was the thin `scripts/nmr/run_algorithm_validation.py`.

The maintained adapter still needs explicitly preserved historical input data
for replay/reporting. An offline source release does not imply those optional
large datasets are included. Its `--stage inputs`/`--recompute` commands require
appropriate `--input` and `--historical-root` evidence packages. For portable
primary-method validation without historical result packages, use
`scripts/validate_primary_phase_release.py` and the committed DX fixture.
Never choose the immutable historical output directory as a new output.
