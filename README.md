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

For the **12 / 13 legacy NMR workflow**, use WORK's verified Python from
`C:\code\chemyx_pump`, with the rig prepared. First run the short instrument check:

```powershell
python scripts\13_3InstrumentCheck.py --live
```

After the check finishes and the rig is ready for Stage 2, launch the six-hour run:

```powershell
python scripts\12_stage2only.py --live
```

Run one command at a time. Both start without an interactive prompt. See the
[12 / 13 guide](docs/03_fixed_time_experiment/12_13_legacy_nmr_alternatives.md)
for preparation, configuration and results. `13` takes one NMR scan; `12`
defaults to eight scans at each of 25 scheduled slots, every 15 minutes from
time zero through six hours.

| Workflow | Open this | Python/config |
|---|---|---|
| Arduino manual control | [01 — Arduino](docs/01_arduino/00_START_HERE.md) | `arduino/scripts/needle_control.py` / `arduino/configs/arduino_real_COM3.yaml` |
| Chemyx manual control | [02 — Chemyx](docs/02_chemyx/00_START_HERE.md) | `scripts/chemyx_control.py` / `configs/chemyx/manual_control.yaml` |
| NMR standalone tests (connection, 1 scan, 4 scans) | [09 — NMR instrument](docs/09_nmr_instrument/00_START_HERE.md) | `scripts/nmr_standalone/` / `configs/machines/si6_real_COM4.yaml` |
| Short check before the 12 run | [13 — Three-instrument check](docs/03_fixed_time_experiment/12_13_legacy_nmr_alternatives.md) | `scripts/13_3InstrumentCheck.py` / `configs/experiments/13_3InstrumentCheck.yaml` |
| Full Stage 1 + dose + Stage 2 with simplified legacy NMR | [14 — Full two-stage run](docs/03_fixed_time_experiment/14_full_stage1_stage2.md) | `scripts/14_full_stage1_stage2.py` / `configs/experiments/14_full_stage1_stage2.yaml` |
| Stage 2 only with legacy NMR completion | [12 — Six-hour Stage 2](docs/03_fixed_time_experiment/12_13_legacy_nmr_alternatives.md) | `scripts/12_stage2only.py` / `configs/experiments/12_stage2only.yaml` |
| 3 instruments, simple: Stage 1 + CH2 dose + Stage 2 | [20 / 21 / 22](docs/03_fixed_time_experiment/20_22_three_instrument_runs.md) | `scripts/20_stage1stage2.py` / `configs/experiments/20_stage1stage2.yaml` |
| 3 instruments, simple: six-hour Stage 2 only | [20 / 21 / 22](docs/03_fixed_time_experiment/20_22_three_instrument_runs.md) | `scripts/21_stage2only.py` / `configs/experiments/21_stage2only.yaml` |
| 3 instruments, simple: quick check | [20 / 21 / 22](docs/03_fixed_time_experiment/20_22_three_instrument_runs.md) | `scripts/22_3instrumentquickcheck.py` / `configs/experiments/22_3instrumentquickcheck.yaml` |
| 2 instruments (needle stationary): Stage 1 + CH2 dose + Stage 2 | [23 / 24 / 25](docs/03_fixed_time_experiment/23_25_two_instrument_runs.md) | `scripts/23_stage1stage2.py` / `configs/experiments/23_stage1stage2.yaml` |
| 2 instruments (needle stationary): six-hour Stage 2 only | [23 / 24 / 25](docs/03_fixed_time_experiment/23_25_two_instrument_runs.md) | `scripts/24_stage2only.py` / `configs/experiments/24_stage2only.yaml` |
| 2 instruments (needle stationary): quick check | [23 / 24 / 25](docs/03_fixed_time_experiment/23_25_two_instrument_runs.md) | `scripts/25_2instrumentquickcheck.py` / `configs/experiments/25_2instrumentquickcheck.yaml` |
| Manual Chemyx withdraw / infuse / stop / status, no saved state (use around 20–25) | [26 — simple manual Chemyx](docs/03_fixed_time_experiment/26_chemyx_manual.md) | `scripts/26_chemyxmanual.py` / `configs/experiments/26_chemyxmanual.yaml` |
| Current full 48-hour / 6-hour run | **[03 — Fixed-time experiment](docs/03_fixed_time_experiment/00_START_HERE.md)** | `scripts/04_si6_fixed_time_run.py` / `configs/experiments/si6_fixed_48h_6h.yaml` |
| Stage 2 only, six hours with live review | **[06 — stage2only](docs/03_fixed_time_experiment/06_stage2only_run_guide.md)** | `scripts/06_si6_stage2only_run.py` / `configs/experiments/si6_stage2only_6h.yaml` |
| Re-run Stage 2 analysis on a selected folder | [07 — Stage 2 analysis recovery](docs/03_fixed_time_experiment/07_stage2only_analysis_recovery.md) | `scripts/07_analyze_stage2only.py` / `configs/nmr/analysis.yaml` |
| Stage 2 only with primary SciPy dynamic area | **[08 — stage2only SciPy](docs/03_fixed_time_experiment/08_stage2only_scipy_run_guide.md)** | `scripts/08_si6_stage2only_scipy_run.py` / `configs/experiments/si6_stage2only_scipy_6h.yaml` |
| Re-run primary SciPy Stage 2 analysis | [09 — SciPy analysis recovery](docs/03_fixed_time_experiment/09_stage2only_scipy_analysis.md) | `scripts/09_analyze_stage2only_scipy.py` / `configs/nmr/stage2only_scipy.yaml` |
| Separate bench check | [04 — Instrument checkout](docs/04_instrument_checkout/00_START_HERE.md) | `scripts/03_simple_instrument_checkout.py` / `configs/checkouts/si6_simple_instrument_check.yaml` |
| Saved NMR processing | [05 — Analysis](docs/05_nmr_analysis/00_START_HERE.md) | `scripts/05_analyze_fixed_time_run.py` / `configs/nmr/analysis.yaml` |
| WORK updates and physical-state backup | [06 — Deployment](docs/06_deployment_and_backup/00_START_HERE.md) | Git/drive workflow and backup tools |

For the complete experiment with `12`'s simplified NMR method, see the
[14 run guide](docs/03_fixed_time_experiment/14_full_stage1_stage2.md). On WORK:

```powershell
python scripts\14_full_stage1_stage2.py --live
```

It runs 48-hour Stage 1, one 1.86 mL CH2 boundary dose, then six-hour Stage 2.

The `04_si6_fixed_time_run.py` controller runs a full 48-hour Phase 1, one protected 1.86 mL
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
