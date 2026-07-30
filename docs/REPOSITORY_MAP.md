# Active Repository Map

Workflow 02 is the single canonical active experiment workflow.

```text
scripts/02_si6_automated_nmr.py
    -> configs/experiments/02_si6_automated_nmr.yaml
    -> configs/machines/00_machine.local.yaml
    -> chemyx_lab/workflows/si6_automated_nmr.py
    -> chemyx_lab/workflows/instrument_operations.py
    -> chemyx_lab/instruments/{chemyx,nmr}.py
    -> chemyx_lab/analysis/{nmr,outputs}.py
    -> chemyx_lab/{runtime_journal,runtime_state,recovery}.py
    -> results/runs/si6/<timestamp>_si6/
```

## Active entry point and configuration

- `scripts/02_si6_automated_nmr.py`: numbered operator entry point.
- `configs/experiments/02_si6_automated_nmr.yaml`: descriptive stage, cycle,
  monitoring, pump, NMR, analysis, and output configuration.
- `configs/machines/00_machine.example.yaml`: committed machine template.
- `configs/machines/00_machine.local.yaml`: ignored work-laptop-specific copy.
- `configs/nmr/analysis.yaml`: parameters for the standalone NMR scripts under
  `scripts/nmr/`. The convention that every script parameter lives in YAML
  rather than in command-line flags is documented in
  `docs/guides/config_yaml_rules.md`.

The active workflow uses `stage` for a reaction-monitoring phase, `cycle` for a
sample/NMR/return sequence, `measurement` for a scheduled NMR sample, and
`operation` for one withdraw, infuse, pause, acquisition, or checkpoint. The
legacy W/N/I event language is not accepted by the active configuration.

## Core package

- `chemyx_lab/workflows/si6_automated_nmr.py`: configuration validation,
  fixed scheduling, monitoring decisions, safety orchestration, and CLI.
- `chemyx_lab/workflows/instrument_operations.py`: shared pump timing/setup and
  NMR acquisition helpers retained from the retired workflow dependency.
- `chemyx_lab/instruments/`: Chemyx, NMR RPC, and separately operated valve
  adapters.
- `chemyx_lab/analysis/`: DX processing, integrated-area analysis, CSV rows,
  and plots.
- `chemyx_lab/runtime_journal.py`: append-only, flushed, fsynced journal.
- `chemyx_lab/runtime_state.py`: pure replay and atomic state projection.
- `chemyx_lab/recovery.py`: offline `--inspect-run` classification.
- `chemyx_lab/testing/`: offline serial fakes.

## Tests

- `tests/test_si6_*.py`: Workflow 02 configuration, monitoring, safety, journal
  integration, and recovery behavior.
- `tests/test_runtime_journal.py`: journal durability, replay, corruption, and
  state-rebuild behavior.
- `tests/test_chemyx.py`, `test_nmr_instrument.py`, and `test_valve.py`: active
  adapter behavior using fakes.
- `tests/test_nmr_analysis_outputs.py`: offline scientific output behavior.
- `tests/test_config.py`: active generic pump/NMR and machine configuration.

## Diagnostics

Numbered scripts under `scripts/diagnostics/` remain active. Hardware-capable
diagnostics require separate work-laptop authorization. Safe home-laptop modes
include NMR acquisition `--dry-run`, valve `--mock`, and offline DX analysis.
Serial-port discovery is not part of home-laptop validation.

## Results

- `results/runs/si6/`: ignored timestamped Workflow 02 runs.
- `results/raw/nmr/`: preserved historical raw spectra.
- `results/processed/nmr_analysis/`: preserved processed analysis records.
- `results/results_manifest.csv`: tracked provenance and checksums.

## Archive

- `archive/legacy_workflows/01_first_real_chemyx_nmr/`: Git-tracked Workflow 01
  code, configuration, tests, and documentation. It is unsupported and must not
  be imported or executed by active code.
- `_archive/`: older ignored local-only cleanup archive described by
  `docs/ARCHIVE.md` and `docs/ARCHIVE_MANIFEST.csv`.

Historical audits and vendor references remain under `docs/`; their historical
mentions of Workflow 01 are evidence, not active execution instructions.
