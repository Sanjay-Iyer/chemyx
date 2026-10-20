> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../../07_other_experiments/02_legacy_run_guide.md) and [errors/fixes](../../07_other_experiments/07_legacy_errors_and_fixes.md) at the instruments.

# Step 1 Inventory Audit

Date: 2026-07-20
Auditor: read-only second AI agent (`Hubble`)
Final status: PASS

The auditor verified:

- all 37 active Python files and all JSON/YAML/TOML files are inventoried;
- the evidence matrix covers imports, consumers, tests, documentation,
  instrument commands, result provenance, classification, and unique behavior;
- the deploy script investigation is accurate;
- the retained-file map covers controls, docs, workflows, package modules,
  diagnostics, tests, results, and technical references;
- valve machine-configuration migration is explicit;
- diagnostic numbering and allowed decision labels are consistent; and
- Step 1 introduced documentation only and did not run Python or contact
  hardware/network endpoints.

The first audit returned FAIL and the second returned PASS WITH CORRECTIONS.
Those findings were corrected before this final PASS.
