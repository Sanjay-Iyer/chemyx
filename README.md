# Chemyx Pump + NMR Workflow

Python control and offline analysis tools for a Chemyx syringe pump and an
Nanalysis/NMReady NMR workflow.

This repository is structured for two laptops:

- Home laptop: development, documentation, dry-runs, and mocked tests only.
- Work laptop: real Chemyx serial and NMR RPC hardware validation.

Real hardware was not contacted during this restructuring.

## Quickest Safe Command

```powershell
conda run -n ai python -B scripts\02_si6_automated_nmr.py --validate-only
```

## Primary Workflow

- Script: `scripts/02_si6_automated_nmr.py`
- Experiment config: `configs/experiments/02_si6_automated_nmr.yaml`
- Machine config: `configs/machines/00_machine.local.yaml`
- Implementation: `chemyx_lab/workflows/si6_automated_nmr.py`
- Instruments: `chemyx_lab/instruments/chemyx.py` and
  `chemyx_lab/instruments/nmr.py`
- Results: `results/runs/si6/<timestamp>_si6/`

Workflow 02 uses descriptive operations for the Si6 sample cycle:

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
docs/              Guides, reports, and audits
_archive/          Ignored local archive
archive/           Git-tracked retired workflows
```

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Repository Map](docs/REPOSITORY_MAP.md)
- [Configuration](docs/CONFIGURATION.md)
- [Si6 Automated Workflow](docs/SI6_AUTOMATED_WORKFLOW.md)
- [Instrument Commands](docs/INSTRUMENT_COMMANDS.md)
- [Chemyx Guide](docs/CHEMYX_GUIDE.md)
- [NMR Guide](docs/nmr_guide.md)
- [Results and Data](docs/RESULTS_AND_DATA.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Validation](docs/VALIDATION.md)
- [Local Archive](docs/ARCHIVE.md)

## Safety

Do not run real hardware commands from the home laptop. Use `--validate-only`,
`--dry-run`, `--inspect-run`, or mocked diagnostics locally. On the work laptop,
rerun offline checks and use a separately authorized staged hardware procedure.
Workflow 02 remains attended-only and experimental.
