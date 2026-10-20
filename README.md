# Chemyx Pump + NMR Workflow

Python control and offline analysis tools for a Chemyx syringe pump and an
Nanalysis/NMReady NMR workflow.

This repository is structured for two laptops:

- Home laptop: development, documentation, dry-runs, and mocked tests only.
- Work laptop: real Chemyx serial and NMR RPC hardware validation.

Updates travel by **HOME Git push -> WORK Git pull -> external hard drive
from WORK to the second WORK laptop**. HOME does not generate offline packages
for this process. See the [Git and drive update guide](docs/03_fixed_time_experiment/04_offline_deployment.md).

Real hardware was not contacted during this restructuring.

## Find commands, configs and documentation

Start with **[00 — Documentation index](docs/00_START_HERE.md)**. Guides are
grouped by instrument/workflow, with numbered filenames in reading order.

| Workflow | Open this | Python/config |
|---|---|---|
| Arduino manual control | [01 — Arduino](docs/01_arduino/00_START_HERE.md) | `arduino/scripts/needle_control.py` / `arduino/configs/arduino_real_COM3.yaml` |
| Chemyx manual control | [02 — Chemyx](docs/02_chemyx/00_START_HERE.md) | `scripts/chemyx_control.py` / `configs/chemyx/manual_control.yaml` |
| Current full 48-hour / 6-hour run | **[03 — Fixed-time experiment](docs/03_fixed_time_experiment/00_START_HERE.md)** | `scripts/04_si6_fixed_time_run.py` / `configs/experiments/si6_fixed_48h_6h.yaml` |
| Stage 2 only, six hours with live review | **[06 — stage2only](docs/03_fixed_time_experiment/06_stage2only_run_guide.md)** | `scripts/06_si6_stage2only_run.py` / `configs/experiments/si6_stage2only_6h.yaml` |
| Re-run Stage 2 analysis on a selected folder | [07 — Stage 2 analysis recovery](docs/03_fixed_time_experiment/07_stage2only_analysis_recovery.md) | `scripts/07_analyze_stage2only.py` / `configs/nmr/analysis.yaml` |
| Stage 2 only with primary SciPy dynamic area | **[08 — stage2only SciPy](docs/03_fixed_time_experiment/08_stage2only_scipy_run_guide.md)** | `scripts/08_si6_stage2only_scipy_run.py` / `configs/experiments/si6_stage2only_scipy_6h.yaml` |
| Re-run primary SciPy Stage 2 analysis | [09 — SciPy analysis recovery](docs/03_fixed_time_experiment/09_stage2only_scipy_analysis.md) | `scripts/09_analyze_stage2only_scipy.py` / `configs/nmr/stage2only_scipy.yaml` |
| Separate bench check | [04 — Instrument checkout](docs/04_instrument_checkout/00_START_HERE.md) | `scripts/03_simple_instrument_checkout.py` / `configs/checkouts/si6_simple_instrument_check.yaml` |
| Saved NMR processing | [05 — Analysis](docs/05_nmr_analysis/00_START_HERE.md) | `scripts/05_analyze_fixed_time_run.py` / `configs/nmr/analysis.yaml` |
| WORK updates and physical-state backup | [06 — Deployment](docs/06_deployment_and_backup/00_START_HERE.md) | Git/drive workflow and backup tools |

The fixed-time controller runs a full 48-hour Phase 1, one protected 1.86 mL
Channel 2 dose and a full 6-hour Phase 2. Missing NMR data is logged without
ending the stage. `--live` starts directly with no interactive prompts. There is
currently no pause/resume. Each launch uses manually restored Channel 1 empty
and Channel 2 at 5 mL. It loads the official needle HOME established by the
Arduino-only script, moves to that HOME, then raises to UP. It never resets the
needle's current location as a new HOME. Both Chemyx HOME definitions are also
preserved: startup only logs your manual return to their existing HOME loading.
Prior-dose JSON does not require
reconciliation. See the numbered run guide for commands and limits.

The [older endpoint-controlled experiment](docs/07_other_experiments/00_START_HERE.md)
uses `scripts/02_si6_experiment.py` and a different YAML. Its Stage 2 can finish
before 6 hours. [Technical reviews](docs/90_reviews/00_START_HERE.md) and
[historical documents](docs/99_archive/00_START_HERE.md) are separate from operating guides.

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

Use the **[numbered documentation index](docs/00_START_HERE.md)** for all groups.
The [rename map](docs/08_reference/02_documentation_map.md) resolves old filenames.

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
