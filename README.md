# Chemyx Pump + NMR Workflow

Python control and offline analysis tools for a Chemyx syringe pump and an
Nanalysis/NMReady NMR workflow.

This repository is structured for two laptops:

- Home laptop: development, documentation, dry-runs, and mocked tests only.
- Work laptop: real Chemyx serial and NMR RPC hardware validation.

Real hardware was not contacted during this restructuring.

## Quickest Safe Command

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --validate-only
```

## Primary Workflow

- Script: `scripts/01_first_real_chemyx_nmr.py`
- Experiment config: `configs/experiments/01_first_real_chemyx_nmr.yaml`
- Machine config: `configs/machines/00_machine.local.yaml`
- Implementation: `chemyx_lab/workflows/first_real_chemyx_nmr.py`
- Instruments: `chemyx_lab/instruments/chemyx.py` and
  `chemyx_lab/instruments/nmr.py`
- Results: `results/raw/nmr/generated/`

The workflow preserves the proven sequence:

```text
Withdraw sample -> acquire NMR -> infuse sample back
```

## Layout

```text
chemyx_lab/        Workflows, instruments, analysis, and offline test doubles
configs/           Experiment and machine configuration
scripts/           Workflow 01 and numbered diagnostics
tests/             Offline tests and fakes
results/           Preserved raw and processed data
docs/              Guides, reports, and audits
_archive/          Ignored local archive
```

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Repository Map](docs/REPOSITORY_MAP.md)
- [Configuration](docs/CONFIGURATION.md)
- [Workflow 01](docs/WORKFLOW_01.md)
- [Instrument Commands](docs/INSTRUMENT_COMMANDS.md)
- [Chemyx Guide](docs/CHEMYX_GUIDE.md)
- [NMR Guide](docs/nmr_guide.md)
- [Results and Data](docs/RESULTS_AND_DATA.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Validation](docs/VALIDATION.md)
- [Local Archive](docs/ARCHIVE.md)

## Safety

Do not run real hardware commands from the home laptop. Use `--validate-only`
or `--dry-run` locally. On the work laptop, run dry-run first and confirm the
printed plan before allowing real pump movement or NMR acquisition.
