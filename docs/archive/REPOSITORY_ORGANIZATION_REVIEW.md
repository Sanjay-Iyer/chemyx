> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../SI6_SIMPLE_RUN_GUIDE.md) and [errors/fixes](../SI6_ERRORS_AND_FIXES.md) at the instruments.

# Repository organization review — 2026-10-15

Scope: engineering reliability and experimental traceability. Only the main
experiment's output plumbing was reorganized in this task. Recommendations below
are future work, not completed moves. No historical datasets, archives, firmware,
unrelated scripts or packages were relocated, renamed or deleted.

## Current important structure

```text
chemyx_pump/
  chemyx_lab/
    instruments/               Chemyx serial, NMR RPC, separate valve adapters
    workflows/                 shared operations, main Si6, legacy Si6, soak,
                                profiles, simulation, channel rules, dose guard
    analysis/                  NMR/phase/baseline/peak/endpoint/report algorithms
    testing/                   fake devices, synthetic validators, fixtures
    experiment_paths.py        new main-experiment layout/allocator
    runtime_journal.py, runtime_state.py, recovery.py, run_metadata.py
  arduino/
    firmware/needle_controller/ current controller firmware
    python/, mock/, scripts/   protocol/transport/state/HOME/manual operations
    configs/, docs/, tests/    rig settings, operating instructions, regression
    dm542s_hello_world/        commissioning/calibration scripts, tests, records
  scripts/
    01_three_instrument_system_test.py
    02_si6_experiment.py        main automatic three-instrument entry point
    02_si6_automated_nmr.py     retained legacy/manual runner and offline inspection
    03_si6_real_soak_test.py    separate attended commissioning workflow
    diagnostics/, nmr/         individual instrument checks and offline processing
    validate_si6_*.py          offline and synthetic validators
    prepare_si6_release.py, verify_si6_environment.py
  configs/{experiments,machines,nmr}/   active settings, including tracked local YAML
  config_templates/            nominal/development/simulation and machine examples
  docs/{guides,reference,audits,diagrams,valve}/ plus many root-level guides/reports
  tests/                       main instrument, science, workflow and recovery tests
  smoke_test/                  separately invoked device checks
  nmr_template/                bundled NMR reference/vendor resources
  offline/                     build/install/verify scripts, lock, local wheel payload
  results/
    experiments/               new main output folders (ignored)
    raw/, processed/, analysis/ machine-local inputs/products, historical runs
    100426_algovalidation/code/ tracked validation implementation under results
    ...                        retained tracked and ignored inspection/validation data
  runtime/si6_doses/            durable physical-experiment reservations (ignored)
  runs/arduino/                standalone controller/HOME journals (ignored)
  archive/legacy_arduino_firmware/  tracked provenance; other historical workflows
  _archive/, dist/             local historical holding/release payloads
  notebooks/, test_tmp_*/, .codex_pytest_temp_*/  existing exploratory/scratch material
  pyproject.toml, requirements.txt, conftest.py, AGENTS.md, MEMORY.md, README.md
```

Evidence: inspected the actual source tree, tracked-file lists, `.gitignore`,
`pyproject.toml`, output writers/readers, release/copy validators, and operator
guides. No claim that a similarly named script is unused is made without call-site
evidence. Retained legacy modules are still imported by tests/workflows.

## Prioritized recommendations

| Priority | Actual issue and consequence | Future change and affected files | Migration risk |
|---|---|---|---|
| **High** | `scripts/validate_si6_synthetic_analysis.py` ultimately imports validation implementation from `results/100426_algovalidation/code/`, an exception inside an otherwise generated/ignored tree. A well-intended results cleanup or incomplete export can remove executable dependencies. | Separately migrate that implementation into `chemyx_lab/testing/` after inventorying imports, fixture licenses and recorded provenance. Update validators, offline resources, tests and `.gitignore`; retain archived scientific evidence with hashes. | High if moved without compatibility imports and a fresh extracted-bundle validation. No move made here. |
| **High** | Durable needle reference/pending HOME marker and `runtime/si6_doses` are intentionally local/ignored, while source deployments may be copied separately. A source-only transfer of an already used rig loses the physical-experiment admission history. The new output folder cannot replace it. | Add a separately approved rig-history backup/restore manifest that inventories canonical state plus original run journals, checks hashes, and refuses overwriting uncertain/newer records. Affect release/install procedures and recovery guides, not chemistry identity or automatic replay rules. | High: incorrect restore could permit repeat dosing or trust stale needle state. Preserve current manual reconciliation until independently validated. |
| **Medium** | `pyproject.toml` default `testpaths` lists `tests` and commissioning tests but omits `arduino/tests`, where production controller/HOME regression lives. A default `pytest` can miss these protections. | In a separate CI/test-discovery change, explicitly collect all three roots and audit hardware-capable script discovery. Include one repeatable HOME-safe CI command and isolate script helper imports. Affect `pyproject.toml`, root/per-Arduino conftests and test loaders. | Medium: accidental collection of interactive scripts must be prevented. This task explicitly executed all roots without changing global discovery. |
| **Medium** | Guides overlap and disagree about the active runner: `docs/archive/REPOSITORY_MAP.md` still calls `02_si6_automated_nmr.py` canonical, while the current main rig uses `02_si6_experiment.py`. Multiple historical handoff/validation reports resemble current instructions. | Establish one current operator index, label historical reports with applicable revision/date, and link legacy workflows explicitly. Affect README, repository map, current Si6 guides, offline docs and dated report headers. Directly affected output guides were updated here; wholesale documentation rewrite was not. | Medium: preserve old commands as historical evidence and avoid presenting old human checkpoints as current automation. |
| **Medium** | Several script directories have a top-level `_common.py`/`_bootstrap.py`. Importing CLIs in one Python test process can resolve a cached helper from another directory; this was observed during HOME/NMR regression. Machine `.local.yaml` files are also tracked shared starting points, not guaranteed private per-rig overrides. | Separately package script utilities with qualified imports and add a clear rig-configuration ownership policy. Affect Arduino/NMR CLI imports, test loaders, config docs and startup display. Preserve script entry points with wrappers. | Medium: naïvely changing imports breaks direct-script execution/offline copies; naïvely renaming local configs changes commissioning behavior. This task fixed its HOME test loader's isolation only. |
| **Medium** | Offline assembly exists in both `offline/build_offline_bundle.ps1` and `scripts/prepare_si6_release.py`, while copy tests maintain a resource list. Those inventories can drift as paths/modules grow. | Derive payload inventory from one reviewed release manifest, keeping code/config and physical history separately classified. Validate from an extracted payload with no Git/network. Affect build, install, verifier and copied-directory validator scripts. | Medium: preserve wheels, NMR resources, firmware/toolchain and provenance; don't rebuild old releases in place. Existing release ZIPs do not incorporate uncommitted changes. |
| **Low** | Many disposable `test_tmp_*` and `.codex_pytest_temp_*` directories clutter the checkout; generated files are mostly ignored, but historical tracked results remain tracked despite ignore rules. The audit found 246 tracked paths under `results`. | Future test utilities can allocate a single ignored scratch area with explicit retention/ownership manifests. Audit tracked historical results by checksum before considering an external data archive; keep code fixtures small and immutable. Affect test commands, data/archive manifests and `.gitignore`. | High for historical deletion; low for routing future scratch. No cleanup/deletion was performed. |
| **Low** | `smoke_test`, `scripts/diagnostics`, commissioning scripts and the integrated diagnostic all serve different hardware checks with superficially similar names. `nmr_legacy.py` and archived firmware can look redundant to new contributors. | Add an execution-mode/rig-capability matrix to the future operator index and mark retained provenance roles. First trace callers before proposing deprecation. Affect only documentation initially. | Low for indexing; high for removal without hardware/provenance review. No scripts declared obsolete solely by filename. |

## What is already well separated

Instrument adapters, workflows, pure analysis, fake devices and durable replay
are distinct modules. Phase-correction and endpoint mathematics were not moved
or altered. Immutable raw JCAMP data, saved processing parameters, visible dataset
plot titles, source hashes, metadata-only scientific timing and the protected dose
ledger support reproducibility. The new `step_1`/`step_2` layout makes a single
execution discoverable without pretending a restarted folder is a new reaction.

## Possible future shape — proposal only

Keep existing hardware/package boundaries. A later, separately reviewed change
could consolidate reusable validators under `chemyx_lab/testing`, a qualified CLI
utility module, one `docs` operator index, one release inventory, and one ignored
validation scratch root. Historical scientific datasets/validation packages should
remain immutable, with external archival storage only after checksum and provenance
review. Any compatibility wrappers must be retained until offline copies and
operator scripts are validated. No such structural changes were implemented here.
