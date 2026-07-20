# Repository Map

| Workflow | Run this script | Experiment config | Machine config | Implementation | Instruments | Results |
| -------- | --------------- | ----------------- | -------------- | -------------- | ----------- | ------- |
| 01 | `scripts/01_first_real_chemyx_nmr.py` | `configs/experiments/01_first_real_chemyx_nmr.yaml` | `configs/machines/00_machine.local.yaml` | `chemyx_lab/workflows/first_real_chemyx_nmr.py` | `chemyx_lab/instruments/chemyx.py` and `chemyx_lab/instruments/nmr.py` | `results/raw/nmr/generated/` |

```text
scripts/01_first_real_chemyx_nmr.py
    -> configs/experiments/01_first_real_chemyx_nmr.yaml
    -> configs/machines/00_machine.local.yaml
    -> chemyx_lab/workflows/first_real_chemyx_nmr.py
    -> chemyx_lab/instruments/chemyx.py
    -> chemyx_lab/instruments/nmr.py
    -> chemyx_lab/analysis/nmr.py
    -> results/raw/nmr/generated/
```

## Run and Configure

- `scripts/01_first_real_chemyx_nmr.py`: the only active user-facing workflow.
- `scripts/first_real_test.py`: temporary compatibility bridge for historical
  commands; workflow 01 no longer imports it.
- `scripts/_bootstrap.py`: direct-script package bootstrap.
- `configs/experiments/01_first_real_chemyx_nmr.yaml`: the only active
  experiment configuration.
- `configs/machines/00_machine.example.yaml`: the only committed machine
  template. Copy it to ignored `00_machine.local.yaml` on the work laptop.

## Package

- `chemyx_lab/config.py`: YAML/JSON compatibility loaders, validation, defaults,
  and CLI/environment/machine/experiment precedence.
- `chemyx_lab/workflows/first_real_chemyx_nmr.py`: complete W -> N -> I workflow
  implementation, timing, confirmation, failure recovery, and result save.
- `chemyx_lab/instruments/chemyx.py`: Chemyx serial framing, commands, response
  parsing, echo checks, and connection safety.
- `chemyx_lab/instruments/nmr.py`: NMR RPC routes, payloads, response handling,
  and status polling.
- `chemyx_lab/instruments/valve.py`: separate MX valve support; not called by
  workflow 01.
- `chemyx_lab/analysis/nmr.py` and `chemyx_lab/analysis/outputs.py`: offline DX processing,
  plots, CSV rows, and manifests.
- `chemyx_lab/testing/mock_serial.py`: offline Chemyx and valve emulators.

Every package subdirectory contains an `__init__.py`; internal modules use
descriptive names and are not numbered.

## Diagnostics

Diagnostics are numbered in recommended order. They do not need experiment
configs. Instrument diagnostics use the machine YAML or explicit CLI values.

| Order | Script | Purpose | Hardware behavior |
| ----- | ------ | ------- | ----------------- |
| 01 | `scripts/diagnostics/01_list_serial_ports.py` | List visible serial devices | Lists only; opens no port |
| 02 | `scripts/diagnostics/02_verify_chemyx_movement.py` | Proven pump movement smoke test | Moves pump unless `--mock` |
| 03 | `scripts/diagnostics/03_check_nmr_connection.py` | Query NMR RPC status | Network reads |
| 04 | `scripts/diagnostics/04_run_nmr_1d_acquisition.py` | Standalone NMR acquisition | `--dry-run` is network-free; real mode acquires |
| 05 | `scripts/diagnostics/05_inspect_mx_controller.py` | Advanced controller reads/position/home | Opens valve serial port |
| 06 | `scripts/diagnostics/06_check_mx_valve.py` | Package-backed valve bring-up | `--mock` is offline; real mode moves valve |
| 07 | `scripts/diagnostics/07_analyze_nmr_results.py` | Batch DX analysis | Offline only |

`scripts/diagnostics/_bootstrap.py` is internal script support, not a diagnostic
the user selects.

## Tests

- `tests/test_first_real_chemyx_nmr_commands.py`: workflow pump/NMR call order.
- `tests/test_first_real_chemyx_nmr_config.py`: workflow config precedence.
- `tests/test_chemyx.py`: exact Chemyx bytes, CR terminator, echo, and safety.
- `tests/test_nmr_instrument.py`: NMR payload/status behavior.
- `tests/test_nmr_analysis_outputs.py`: result folders/CSV/manifests.
- `tests/test_config.py`: config models/loaders/validation.
- `tests/test_valve.py`: exact valve framing and safety.
- `conftest.py`: test import bootstrap.

All tests are designed for offline execution. Use Conda environment `ai`.
The recorded offline results and ordered work-laptop qualification commands are
in `docs/VALIDATION.md`.

## Results and References

- `results/raw/nmr/06-08-26/`: seven preserved successful raw DX files.
- `results/processed/nmr_analysis/`: two preserved timestamped analysis runs.
- `results/results_manifest.csv`: result checksums and move provenance.
- `docs/reference/nmr_rpc_api/`: retained NMR API notes and HTML reference.
- `docs/reference/valve/`: retained Titan UART protocol PDF.
- `docs/diagrams/`: retained source diagrams and render.
- `docs/valve/`: separate valve setup guides.

## Archive

Confirmed legacy, duplicate, generated, and incomplete experimental files are
outside the active tree under `_archive/2026-07-20_repository_cleanup/`.
`docs/ARCHIVE_MANIFEST.csv` records all 97 files and checksums. See
`docs/ARCHIVE.md` for the Git portability warning.
