# 11 — Simplified Stage 2 demo: `10_stage2only`

[Experiment index](00_START_HERE.md) | [Script](../../scripts/10_stage2only.py) | [One experiment YAML](../../configs/experiments/10_stage2only.yaml)

This attended research demo runs a sequential Arduino → Chemyx CH1 → NMR →
cleanup cycle on a monotonic clock. Defaults are **360 minutes, every 15 minutes,
25 scheduled observations from 0:00 through 6:00**, eight NMR scans, with
Channel 2 addition disabled. It is separate from the unchanged `06` and `08` runners.

## Launch

On HOME, from the repository root:

```powershell
cd C:\code\chemyx_pump
```

```powershell
conda activate ai
```

```powershell
python scripts\10_stage2only.py --config configs\experiments\10_stage2only.yaml --mock
```

Mock mode uses the existing Arduino and Chemyx fakes and an accelerated clock;
it makes no hardware or NMR network connections. Its `.dx` files are unchanged
copies of a real fixture, including the fixture's original JCAMP timestamps.
Consequently the mock analysis time axis does **not** pretend to span six hours.

On WORK, after preparing and inspecting the rig, use WORK's verified Python:

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\10_stage2only.py --config configs\experiments\10_stage2only.yaml --live
```

`--live` starts without further prompts. Before launch, manually prepare CH1
empty and verify its syringe/tubing. Prepare CH2 with the configured 5 mL loading
only if addition is enabled. Both defaults are 20 mm diameter / 20 mL syringes.
Verify COM3 for Arduino, COM4 for Chemyx, and the NMR address in the single YAML.
Use distinct serial ports; change Chemyx to COM6 in this YAML when appropriate.

The needle uses the **existing official reference** at
`runs/arduino/needle_state.json`, established by the standalone
[Arduino manual script](../01_arduino/01_manual_commands.md). Startup moves to
that saved HOME=0, then UP=1; it never defines a new zero. Unknown, incompatible
or interrupted references require physical inspection using the
[Arduino recovery guide](../01_arduino/02_home_recovery.md). The demo does not
guess HOME or delete an incomplete recovery marker. No commissioning journals
or previous experiment reports are required.

## Physical cycle and clock

Each cycle preserves the established sampling procedure:

1. With the needle UP, withdraw 8 mL on CH1 at 5 mL/min.
2. Move to the existing HOME/DOWN position; withdraw another 5 mL at 5 mL/min.
3. Settle 10 seconds, acquire NMR once and save the raw FID.
4. Raise to UP; infuse all 13 mL at 5 mL/min.
5. Withdraw and infuse 5 mL at 5 mL/min for cleanup, ending empty and UP.

Pump moves use the established `start 0` command, calculated travel time plus
two seconds, and a command-specific positive STOP acknowledgment. The driver
does not measure syringe contents or independently sense completed plunger travel.

Times stay relative to the original experiment start, after startup positioning.
An overdue slot is reported as missed, including an overdue endpoint. There are
no overlapping cycles or catch-up bursts. Normally the final cycle **starts at
6:00 and finishes its acquisition/cleanup afterward**. Analysis never changes
the configured duration. Ctrl+C stops the run; restarting starts a fresh clock
and folder after manual physical preparation. There is no pause/resume.

To change duration or interval, edit `experiment.duration_minutes` and
`experiment.interval_minutes`. `include_final_observation: true` includes the
duration endpoint. Volumes, rates, delays and calibrated needle positions are
also in the same YAML; retain physically verified values.

## Optional Channel 2 addition

Set `channel_2.enabled: true` to enable one configured **1.86 mL infusion at
1 mL/min**. After Time 0 acquisition and all physical cleanup finish, the script
waits **180 seconds**, moves the needle HOME, infuses on explicit CH2, then raises
the needle UP. Edit `volume_ml`, `rate_ml_min`, `initial_volume_ml` and
`delay_after_time_zero_seconds` in that section as needed.

The dose is attempted once per invocation. A numerical-analysis failure or NMR
failure does not prevent it when Time 0 physical cleanup is verified. A physical
failure prevents dosing and further movement. An uncertain infusion is never
automatically repeated. If disabled, no CH2 dose, delay or commands are issued.

## NMR, analysis and saved output

Acquisition is automatic: **NMR settings → START once → wait for completion →
retrieve raw FID → save `.dx` → continue**. There are no pre-START idle checks or
NMR confirmations. The dedicated helper uses the unchanged HTTP client:

```text
GET  /interfaces/iFlow/Settings/1D
GET  /interfaces/iFlow/ExperimentSettings
PUT  /interfaces/iFlow/Settings/1D
PUT  /interfaces/iFlow/RunExperiment          # START once, eight scans
GET  /interfaces/iFlow/ExperimentStatus       # After START only; poll receipt/scans
GET  /interfaces/Service/Acquire              # After matching requested scan count
GET  /interfaces/iFlow/ExperimentStatus       # Reconfirm same receipt; retrieve raw TD
```

Completion requires the START receipt's experiment number and timestamp (or this
attempt's unique echoed export path), the requested scan count, and the documented
`Status.InProgress: false` / `LastAcquireSuccessful: true` response. Numeric iFlow
status codes are not interpreted. Raw FID must have an FID type, nonempty data
table and END record; its text and acquisition metadata are preserved.

A lost START reply or unconfirmed completion stays **pending in memory for this
invocation**. Later observations monitor that same acquisition without sending
another START. If it completes with a provable identity, its raw data is saved to
the original observation, then normal acquisition can continue. If it cannot be
identified or confirmed complete, further NMR STARTs remain skipped for this run;
the six-hour clock, sampling and cleanup continue. There is no acknowledgment
file, operator prompt or blind retry. A completed acquisition with missing/bad
raw data reports failure without retaining an unresolved-START restriction.
The actual START response and the failing step/error are printed. Physical
movement uncertainty still stops further physical commands.

There are **no pump HOME-ledger checks, old dose/history checks, NMR ownership
locks, acknowledgment files, acquisition subprocesses or request/result JSON
protocols**. Essential current Arduino position tracking, calibrated travel,
syringe volume/rate limits, command acknowledgments and overlap checks remain.
The demo assumes it is the only NMR controller.

One optional analysis child starts before instrument handles open. It receives
no instrument handles, is never joined, and cannot stop or delay the timed loop.
Missing dependencies, bad metadata, slow processing or a crash affect review only.
Its settings are `analysis.enabled`, `every_n_acquisitions`, and plot switches.
Processing reuses production **ACME / ALS**, `find_peaks` prominence ≥8σ and
`peak_widths(rel_height=0.95)`, with positive endpoint-baseline trapezoidal area.
Missing dynamic peaks remain missing; fixed-window area is never substituted.

Every fresh folder under `results/experiments/stage2only_demo_<timestamp_id>/` contains:

| Output | Purpose |
|---|---|
| `config_used.yaml`, `experiment.log` | Configuration snapshot and immediate terminal progress copied to a log |
| `nmr_raw/stage_2_0001.dx`, etc. | Original raw exports; `.rpc.dx` can preserve a separate RPC export |
| `analysis/observation_0001.png`, `observation_0001_target.png` | Processed spectrum and dynamic integration review |
| `analysis/peak_areas.csv`, `peak_area_vs_time.png` | Peak measurements and cumulative trend |
| `analysis/analysis_summary.json`, `plot_manifest.csv` | Review status, missing peaks and dataset/title evidence |
| `analysis_live.log`, `summary.json` | Numerical progress and separate experiment/acquisition/analysis counts |

Plot timing comes from JCAMP metadata, preferring LONG DATE, never filenames or
file timestamps. The saved config names this actual run for visible plot titles;
optional `analysis.dataset_display_name` overrides it. The controller can finish
while analysis is pending. The surviving child updates final analysis counts in
`summary.json` after its last pass; check `analysis_summary.json` and the child log
if it fails. Zero spectra is reported as `COMPLETE_NO_SPECTRA`, with a nonzero exit.

Re-run lightweight review independently, substituting the actual run folder:

```powershell
python -m chemyx_lab.analysis.stage2_demo_analysis --run-dir "C:\data\my_run" --config "C:\data\my_run\config_used.yaml" --raw-dir "C:\data\my_run\nmr_raw"
```

For optional comprehensive analysis, use [09](09_stage2only_scipy_analysis.md),
passing the actual raw directory explicitly (especially if `raw_directory` changed):

```powershell
python scripts\09_analyze_stage2only_scipy.py "C:\data\my_run\nmr_raw"
```

## Validation and WORK verification

HOME validation updated on 2026-10-20: **164 tests passed** (124 demo tests,
including 45 NMR HTTP contracts, plus 40 existing driver/06/08 checks). The real
HTTP client with mocked transport sends 25 separate STARTs over the accelerated
six-hour schedule, with no status calls before a new START. Tests cover uncertain
dispatch, timeout/pending recovery, stale/partial receipts, raw validity, both
dose modes, channels, timing, logging, processing/title conventions and blocked
real network/serial access. All 353 existing source/config/firmware hashes stayed
unchanged. Independent review's partial-receipt issue was fixed and reviewed again
with no unresolved findings.

The earlier full CLI mock saved 25 byte-preserved raw spectra; its independent
child completed 25 analyses and 51 PNG plots using LONG DATE timing. The retained
fixture has an ambiguous target, visibly flagged for review. The physical adapter,
analysis implementation, experiment YAML and existing workflows were unchanged by
the focused removal of pre-START checks.

The physical adapter reuses `arduino.scripts._common.controller_session`,
`TrackedNeedle`, and `chemyx_lab.instruments.chemyx.Pump`. Acquisition reuses
`NmrRpcClient` and its existing iFlow settings builders. The numerical child
reuses `build_phased_spectrum`, production `process_spectrum_for_peaks`, and the
approved `scipy_peak_comparison` detection/integration functions; it changes none
of their algorithms.

WORK must still verify calibrated needle travel, pump ACK wording (`pump start` /
`pump stop`), actual syringe/flow behavior, the acquisition receipt, scan count,
documented `Service/Acquire` flags and raw exports. See the retained
[vendor HTTP reference](../08_reference/vendor/nmr_rpc_api/html/rpc-api.md).
The shared HTTP timeout budget is not a hard wall-clock
bound for DNS, continuously trickling responses or filesystem operations.

Deploy through **HOME Git push → WORK Git pull → external drive → WORK2**.
HOME does not prepare an offline package.


## Pre-flight: `11_3InstrumentCheck` (short three-instrument check)

Run this before the six-hour `10_stage2only` run. It uses the same drivers,
COM ports, NMR host and Arduino reference as 10 (read from
`configs/experiments/10_stage2only.yaml`, never modified); only the test
settings in `configs/experiments/11_3InstrumentCheck.yaml` are applied in
memory (CH1 only, 1 mL withdraw/infuse at 5 mL/min, 1 NMR scan).

```powershell
python scripts\11_3InstrumentCheck.py --mock   # HOME: no hardware
```

```powershell
python scripts\11_3InstrumentCheck.py --live   # WORK: attended, no prompts
```

Sequence: needle UP, DOWN, CH1 withdraw 1 mL (needle DOWN), CH1 infuse 1 mL,
needle UP, NMR 1 scan (one START, never repeated), raw `.dx` saved, optional
analysis. Prepare the rig as for 10 first (CH1 empty, saved Arduino HOME
reference present). Watch the plunger and needle; Ctrl+C stops the run.

Output: `results/experiments/3instrument_check_<timestamp>/` with
`config_used.yaml`, `experiment.log`, `nmr_raw/instrument_check_0001.dx`,
`analysis/instrument_check_0001.png` and `summary.json`. A failed step is
named in the terminal and in `summary.json`; after a needle/pump failure the
NMR step is skipped.

PASS means the instrument feedback confirmed the step (needle status, pump
START/STOP replies, NMR receipt/scan count/completion and a valid raw FID).
It does **not** prove the full 13 mL sample reaches the flow cell, the 8-scan
setting, the optional Channel 2 dose or six-hour endurance. With only 1 mL
moved, the flow cell may not contain sample, so analysis may report no target
peak; that does not fail the hardware check.
