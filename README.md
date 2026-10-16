# Chemyx Pump + NMR Workflow

Python control and offline analysis tools for a Chemyx syringe pump and an
Nanalysis/NMReady NMR workflow.

This repository is structured for two laptops:

- Home laptop: development, documentation, dry-runs, and mocked tests only.
- Work laptop: real Chemyx serial and NMR RPC hardware validation.

Real hardware was not contacted during this restructuring.

## Three-instrument Si6 entry points

At the instruments, use **[SI6_SIMPLE_RUN_GUIDE.md](docs/SI6_SIMPLE_RUN_GUIDE.md)**:
Arduino manual test → Chemyx Channel 1/2 manual test → communication check →
short two-stage checkout → inspect results → full experiment.
Keep **[SI6_ERRORS_AND_FIXES.md](docs/SI6_ERRORS_AND_FIXES.md)** alongside it.

Both automatic workflows use `scripts/02_si6_experiment.py` with explicit
machine and Arduino configs. Production uses
`configs/experiments/si6_real_two_stage.yaml`: fixed 48-hour Stage 1, one
protected Channel 2 dose, then Stage 2 minimum 2 / maximum 6 hours with four
qualifying post-minimum observations. Intervals are 120 / 30 minutes.
The separate `si6_real_10min_checkout.yaml` uses nominal five-minute stages,
one configured test infusion, and a complete NMR cycle per stage within its
safe-boundary overall timeout. Fluid/routing descriptions are optional; actual
transfer quantities and experiment identity remain required.

One startup `y` starts either automation; there are no intermediate prompts.
HOME=DOWN=0 is in the flask and UP=1 is out. Current results use
`results/experiments/YYYYMMDD_experimentID[_n]` with separate stage data,
dose evidence, logs and final QC. Retain the commissioned firmware and software
HOME references; no hardware command is run on HOME.

[Current operating index](docs/SI6_START_HERE.md),
[configuration reference](docs/SI6_CONFIG_USER_GUIDE.md),
[phase methods](docs/SI6_NMR_PHASE_METHODS.md), and
[state preservation](docs/RIG_STATE_BACKUP.md) provide supporting detail.
Superseded operating documents and dated audits are retained in
**[docs/archive](docs/archive/README.md)**, with historical banners and repaired
links. They are not the current bench procedure.

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

- [Simple instrument operating guide](docs/SI6_SIMPLE_RUN_GUIDE.md)
- [Errors and fixes](docs/SI6_ERRORS_AND_FIXES.md)
- [Current operating index](docs/SI6_START_HERE.md)
- [Standalone Chemyx quick reference](docs/CHEMYX_QUICK_REFERENCE.md)
- [Offline deployment](docs/OFFLINE_DEPLOYMENT.md)
- [Offline requirements](docs/OFFLINE_REQUIREMENTS.md)
- [State backup and preservation](docs/RIG_STATE_BACKUP.md)
- [Create experiments and use offline NMR GUIs](docs/CREATE_NEW_EXPERIMENT.md)
- [NMR phase selection](docs/SI6_NMR_PHASE_METHODS.md)
- [Experimental results layout](docs/SI6_EXPERIMENT_OUTPUTS.md)
- [Scientific plotting workflow](docs/NMR_TARGET_PEAK_WORKFLOW.md)
- [Historical docs and audit archive](docs/archive/README.md)

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
