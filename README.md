# Chemyx Pump + NMR Workflow

Python control and offline analysis tools for a Chemyx syringe pump and an
Nanalysis/NMReady NMR workflow.

This repository is structured for two laptops:

- Home laptop: development, documentation, dry-runs, and mocked tests only.
- Work laptop: real Chemyx serial and NMR RPC hardware validation.

Real hardware was not contacted during this restructuring.

## Three-instrument Si6 entry points

```powershell
python -B scripts\01_three_instrument_system_test.py
python -B scripts\01_three_instrument_system_test.py --mock --all
python -B scripts\02_si6_experiment.py --mock
```

`01` diagnoses the needle, Chemyx, NMR acquisition, retrieval, and processing.
`02` runs the configured Si6 sampling stages. Both default to validation only;
the active needle firmware is 1.2.0 on the validated D3 STEP / D4 DIR wiring,
with supervised Python software-position tracking (see
[Arduino needle controller](arduino/README.md)).
`--mock` contacts no hardware and writes to `results/runs/si6_mock/`. `--live`
requires commissioning, reviewed positions, and an attended confirmation, and
writes to `results/runs/si6/<stamp>_si6_live` (or `_diagnostic_<test>_live`).
See
[the exact sampling order and gates](docs/THREE_INSTRUMENT_ARCHITECTURE.md) and
the ordered [live commissioning checklist](docs/LIVE_COMMISSIONING_CHECKLIST.md).

## Legacy two-instrument workflow

- Script: `scripts/02_si6_automated_nmr.py`
- Experiment config: `configs/experiments/02_si6_automated_nmr.yaml`
- Machine config: `configs/machines/00_machine.local.yaml`
- Implementation: `chemyx_lab/workflows/si6_automated_nmr.py`
- Instruments: `chemyx_lab/instruments/chemyx.py` and
  `chemyx_lab/instruments/nmr.py`
- Results: `results/runs/si6/<timestamp>_si6/`

The older Chemyx/NMR-only Workflow 02 uses operator needle checkpoints:

```text
withdraw -> operator checkpoint -> withdraw -> pause -> NMR -> infuse
-> operator checkpoint -> withdraw -> infuse
```

## Layout

```text
chemyx_lab/        Workflows, instruments, analysis, and offline test doubles
configs/           Experiment and machine configuration
scripts/           Canonical Workflow 02 and numbered diagnostics
tests/             Offline tests and fakes
results/           Preserved raw and processed data
offline/           Pinned versions and scripts for a laptop with no internet
docs/              Guides, reports, and audits
_archive/          Ignored local archive
archive/           Git-tracked retired workflows
```

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Offline Laptop Setup](docs/OFFLINE_SETUP.md)
- [Repository Map](docs/REPOSITORY_MAP.md)
- [Configuration](docs/CONFIGURATION.md)
- [Si6 Automated Workflow](docs/SI6_AUTOMATED_WORKFLOW.md)
- [Three-instrument architecture](docs/THREE_INSTRUMENT_ARCHITECTURE.md)
- [Live commissioning checklist](docs/LIVE_COMMISSIONING_CHECKLIST.md)
- [Canonical Arduino firmware](arduino/docs/FIRMWARE.md)
- [Instrument Commands](docs/INSTRUMENT_COMMANDS.md)
- [Chemyx Guide](docs/CHEMYX_GUIDE.md)
- [NMR Guide](docs/nmr_guide.md)
- [Focused NMR Plotting Workflow](docs/NMR_TARGET_PEAK_WORKFLOW.md)
- [Results and Data](docs/RESULTS_AND_DATA.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Validation](docs/VALIDATION.md)
- [Local Archive](docs/ARCHIVE.md)

## Safety

Do not run real hardware commands from the home laptop. Use `--validate-only`,
`--dry-run`, `--inspect-run`, or mocked diagnostics locally. On the work laptop,
rerun offline checks and use a separately authorized staged hardware procedure.
Workflow 02 remains attended-only and experimental.

## Scientific figure identity

Every saved experimental-data figure visibly includes its dataset or experiment
name. NMR plotting uses the configured dataset display name where available and
the centralized resolver documented in the focused NMR workflow; filenames
alone are not treated as figure identity.
