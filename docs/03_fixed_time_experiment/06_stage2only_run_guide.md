# 06 — stage2only: start directly with the six-hour Stage 2

[Experiment index](00_START_HERE.md) | [All documentation](../00_START_HERE.md)

For the complete offline operating, troubleshooting and restart manual, see
[10 — Stage 2 operation and recovery](10_stage2only_operation_and_recovery.md).

Use this separate command when Phase 1 and reagent addition have already been
handled and the reaction is ready for Stage 2. It performs **Stage 2 only**.
The original 48-hour / dose / 6-hour runner and its YAML are unchanged.

| Item | File |
|---|---|
| Clearly named entry point | [06_si6_stage2only_run.py](../../scripts/06_si6_stage2only_run.py) |
| Independent runner | [si6_stage2only.py](../../chemyx_lab/workflows/si6_stage2only.py) |
| Optional background processing | [si6_stage2only_analysis.py](../../chemyx_lab/workflows/si6_stage2only_analysis.py) |
| Independent schedule and recipe | [si6_stage2only_6h.yaml](../../configs/experiments/si6_stage2only_6h.yaml) |
| Pump endpoint / NMR network | [COM4](../../configs/machines/si6_real_COM4.yaml) or [COM6](../../configs/machines/si6_real_COM6.yaml) |
| Existing Arduino calibration and HOME path | [arduino_real_COM3.yaml](../../arduino/configs/arduino_real_COM3.yaml) |
| Existing pump HOME geometry | [manual_control.yaml](../../configs/chemyx/manual_control.yaml), Channel 1 |

## Schedule

**13 scheduled NMR measurements:**

```text
Hours:   0:00  0:30  1:00  1:30  2:00  2:30  3:00
         3:30  4:00  4:30  5:00  5:30  6:00
```

Time 0 is the start of the first sampling cycle after instrument startup.
Each cycle uses the same Channel 1 sampling, needle HOME/UP movements, 8-scan
NMR acquisition, return and wash as the full experiment. The NMR request follows
fluid sampling and settling. These are cycle schedule labels; actual acquisition
time comes from the saved JCAMP metadata, preferably NMReady `LONG DATE`.

The last cycle STARTS at the six-hour endpoint and must finish NMR acquisition
and cleanup, so the script normally finishes after six hours. NMR errors or
missing files are logged and never end the stage early. If an earlier cycle
runs beyond a scheduled slot, missed intermediate slots are logged and skipped
without overlapping cycles or catch-up bursts. The final endpoint measurement
still runs once, as soon as earlier cleanup permits.

After each saved measurement and its physical cleanup, an independent background
worker processes the saved data for review during the run. It uses the existing
processor with ACME phasing, baseline correction, peak integration, plots and
tables. Each snapshot includes the valid saved spectra collected so far, so you
can review both the newest spectrum and the developing time series.

The six-hour clock and instrument sequence never wait for analysis. No quality,
plateau or completion result can shorten the run. A processing exception, missing
analysis dependency, malformed result, plotting failure or hung processor affects
only review output. The worker logs failures and continues with later measurements;
a hung processor is terminated after ten minutes. If the whole review worker
crashes or cannot start, measurement and cleanup still continue automatically.
The controller does not poll, join or read decisions from the worker.

## Prepare, then launch on WORK

- Phase 1 and any required reagent addition are already handled manually or by
  the previous run. **This script never delivers the Channel 2 dose.**
- Restore Channel 1 to its existing empty (0 mL) HOME loading and prepare the
  sampling paths for the existing Stage 2 reaction.
- Leave Channel 2 loading and its displacement record as they are. No refill to
  5 mL, Channel 2 HOME, dose record or reconciliation is required by stage2only.
- Use the official Arduino HOME established with the standalone Arduino script,
  with a valid saved current needle position. Startup moves to that HOME, then UP.
- Both pumps must be stopped and the previous controller must have exited.

Default Chemyx COM4 / Arduino COM3:

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\06_si6_stage2only_run.py --live
```

Chemyx COM6:

```powershell
python scripts\06_si6_stage2only_run.py --live --machine-config configs\machines\si6_real_COM6.yaml
```

The command starts immediately: **no startup `y`, intermediate confirmation,
HOME acknowledgment or dose reconciliation**. Neither official HOME is redefined.
Channel 1 current-position bookkeeping records your manual return to its existing
HOME. Channel 2 receives no infuse/withdraw commands or loading reset; the shared
instrument helpers may send it STOP commands.

Existing unusable needle tracking or Channel 1 HOME, actual concurrent controller
ownership, instrument errors and failures to write required motion records still
stop execution. Ctrl+C stops the run; launching again starts a fresh six-hour
Stage 2 with its own time-0 sample. There is no pause/resume and no repeat dose.

## Results and processing

Each launch creates:

```text
results/stage2only/live/SI6_STAGE2ONLY_<unique id>/
  nmr_raw/stage_2_0001.dx ... stage_2_0013.dx
  analysis_live/index.html
  analysis_live/status.json
  analysis_live/worker.log
  analysis_live/stage_2_0001/ ... stage_2_0013/
  configuration.json
  observations.jsonl
  operation_journal.jsonl
  instrument_commands.log
  run_state.json
  report.json
```

Raw files exist only for acquisitions that successfully save. Missing/failed
measurements remain in the observation records. No dose reservation or dose JSON
is created. Old result and dose files do not gate a new stage2only launch.

Open **`analysis_live/index.html` inside the current run folder** to review the
results during the run. It refreshes every 15 seconds and links the latest
completed plots/tables plus each measurement's processing log and error status.
The console prints this page's full path at startup. If processing is still
running or fails, the last completed results remain available. Earlier snapshots
are retained rather than overwritten. Pending processing may finish after the
instrument run completes; instruments do not wait for it.

Within each measurement's review folder, `processor.log` records processing
errors, `processed/` contains the plots and tables, and `acquisition_metadata.csv`
records actual JCAMP timestamps and source fields. Missing authoritative metadata
fails that analysis only; it never substitutes filename or file-modification
times or prevents the next measurement. Raw data and controller state are never
modified by the review worker. It runs one processor at a time, with one numerical
thread and idle priority on Windows, and has no instrument handles.

For a fresh full Stage 2 analysis or recovery after a live-processing issue,
run the separate [Stage 2 analysis script](07_stage2only_analysis_recovery.md):

```powershell
python scripts\07_analyze_stage2only.py
```

Choose the run folder or `nmr_raw` when the picker opens. Alternatively pass
that directory as a command-line argument. This needs no healthy live-analysis
report and writes a new `analysis_stage2only` folder. The older
[general post-run analyzer](03_post_run_analysis.md) remains available for both stages.

## Local checks on HOME

```powershell
conda activate ai
```

```powershell
python scripts\06_si6_stage2only_run.py --validate-config
```

```powershell
python scripts\06_si6_stage2only_run.py --mock
```

Validation and the accelerated mock do not contact instruments. Mock output is
clearly marked and stored separately under `results/stage2only/mock`. The mock
simulates acquisition without launching the review worker on synthetic spectra;
the processing tests exercise that worker separately with a fixture.
Updates use the existing Git -> WORK -> external-drive route; no package export.

Local verification on 2026-10-19: **44 tests passed** across stage2only,
background processing, documentation and post-run analysis compatibility.
Simulated live transports verified all 13 slots despite NMR connection errors,
no Channel 2 transfer or dose reservation, preserved official HOMEs and unchanged
Channel 2 displacement. Injected analysis launch failures, crashed/hung workers
and malformed analysis status did not shorten the six-hour schedule. A real
hung processor was killed by its own deadline, and the real processor produced
review plots with the correct run identity in the plot manifest. The actual
background launch and completion path was also exercised. A complete command-line
mock saved 13 raw FIDs. Both COM4 and COM6 presets validated. The original full-run
script, implementation and YAML hashes remained unchanged. No hardware was contacted.
