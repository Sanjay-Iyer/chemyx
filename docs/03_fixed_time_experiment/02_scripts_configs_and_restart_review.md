> [Documentation home](../00_START_HERE.md) | [Three-instrument fixed-time experiment](00_START_HERE.md)

# SI6 time-only run: commands, files and restart review

Reviewed on 2026-10-19 against the current working tree, including its
uncommitted fixed-time implementation. This review used local validation and
mock tests only. It did not contact the instruments or alter live rig state.

The fixed-time runner meets the uninterrupted timing requirement: Phase 1 runs
for 48 scheduled hours, then one protected reagent dose, then Phase 2 runs for
6 scheduled hours. It does not run NMR processing or chemical endpoint logic.
There is no in-place pause/resume. Each new launch starts without interactive
prompts and uses pump loading manually restored to the existing Chemyx HOMEs.
The needle retains the official HOME established by the Arduino-only script.
Startup MOVES to that existing HOME, then raises to UP; neither instrument's
official HOME is redefined. Old dose records require no reconciliation. Valid
saved references need no repeated set-home.

## Timing and NMR behavior

| Step | Current preset |
|---|---|
| Phase 1 | 48 hours; sampling-cycle starts at 0, 2, ... 46 hours (24 slots) |
| Transition | Channel 2 infuses 1.86 mL once at 1.00 mL/min |
| Phase 2 | New 6-hour clock after the confirmed dose and needle return; starts at 0, 0.5, ... 5.5 hours (12 slots) |
| NMR | 8 scans, raw FID acquisition only; no FFT, phase correction, peak analysis or endpoint detection |
| Finish | Pumps positively stopped, needle UP; Channel 1 restored to empty, Channel 2 retains 3.14 mL |

The two stage clocks use monotonic time. A missing or failed spectrum does not
reset either clock or stop the schedule. A cycle admitted before a deadline
finishes its sampling/acquisition/cleanup even if it crosses the deadline. No
new cycle starts at or after a deadline. Missed slots are skipped, not replayed
in a burst. Thus 36 is the expected number of observations, not an unconditional
guarantee. Setup, the dose and any final admitted cycle add wall-clock time
beyond the 54 scheduled hours.

NMR connection errors, missing raw data, failed status reads, invalid worker
replies, an unavailable NMR lock and the bounded worker timeout are nonfatal
to the timed fluidic schedule. The default outer worker timeout is 430 seconds.
There is at most one NMR START per observation; busy or unknown NMR state skips
that acquisition. If the NMR lock is unavailable at startup, acquisitions remain
disabled for that run, while the timed fluidics continue.

The YAML action `pause: 10 seconds` is the settling wait inside a sampling
cycle. It is not an operator pause feature.

## Python entry points and supporting modules

Paths in this document are relative to `C:\code\chemyx_pump`.

| File | Role |
|---|---|
| `scripts/04_si6_fixed_time_run.py` | Entry point for the fixed-time experiment, config validation, mock run and NMR-only check |
| `chemyx_lab/workflows/si6_fixed_time.py` | Two stage clocks, sampling cycle, one dose, cleanup, error boundaries and report |
| `chemyx_lab/workflows/si6_fixed_time_nmr.py` | Separate bounded NMR worker; saves raw FIDs without numerical analysis |
| `scripts/05_analyze_fixed_time_run.py` | Separate repeatable post-run analysis, defaulting to the newest live run |
| `scripts/nmr/process_fid.py` | Existing numerical processor invoked by the post-run analysis script |
| `scripts/chemyx_control.py` | Pump status, manual moves, HOME, STOP, stale-state recovery and dose reconciliation |
| `scripts/chemyx_home.py` | Alternate pump HOME entry point |
| `chemyx_lab/workflows/chemyx_manual.py` | Shared pump settings, STOP checks, manual commands and dose restrictions |
| `chemyx_lab/workflows/chemyx_position.py` | Shared syringe HOME, displacement, motion journal and controller lock |
| `chemyx_lab/workflows/pump_channels.py` | Channel, rate, volume and syringe-capacity validation |
| `chemyx_lab/workflows/dose_guard.py` | Durable RESERVED -> DISPATCH_INTENT -> CONFIRMED dose states |
| `chemyx_lab/workflows/dose_recovery.py` | Recovery for standalone manual/older workflows; not used by this fixed-time runner |
| `chemyx_lab/workflows/si6_automated_nmr.py` | Shared instrument settings and metered pump movement; its old analysis controller is not executed by the fixed-time runner |
| `arduino/scripts/needle_control.py` | Manual needle status, relative UP/DOWN, set-home, absolute home and STOP |
| `arduino/python/manual_jog.py`, `needle_state.py`, `home_recovery.py` | Persistent relative jog count, calibrated absolute position and verified HOME recovery |
| `arduino/scripts/_common.py`, `arduino/python/controller.py`, `run_lock.py` | Needle session, command completion and exclusive port ownership |
| `chemyx_lab/runtime_journal.py`, `runtime_state.py` | Durable operation evidence and derived run-state projection |
| `chemyx_lab/instruments/chemyx.py`, `nmr.py` | Instrument drivers |
| `scripts/03_simple_instrument_checkout.py` | Optional separate bench checkout; does not run the chemistry stages |

Use `04_si6_fixed_time_run.py` for this experiment. The older
`scripts/02_si6_experiment.py` has chemical endpoint behavior and is a different
controller.

## Configuration files

| File | What it controls |
|---|---|
| `configs/experiments/si6_fixed_48h_6h.yaml` | Both full stage durations, intervals, physical sampling recipe, one dose, 8 scans and output root |
| `configs/machines/si6_real_COM4.yaml` | Pump COM4 and NMR Ethernet endpoint/timeouts |
| `configs/machines/si6_real_COM6.yaml` | Alternative pump COM6; same NMR endpoint |
| `arduino/configs/arduino_real_COM3.yaml` | Needle COM3, 200 steps/unit, HOME=DOWN=0, UP=1, range -3 through +5 and state path |
| `configs/chemyx/manual_control.yaml` | Manual defaults, syringe geometry, HOME loading and machine selection; its global dose directory is used by manual/older tools |
| `configs/experiments/si6_real_two_stage.yaml` | Referenced by the manual config as a syringe-settings source; its stage/endpoint rules do not control this fixed-time run |
| `configs/checkouts/si6_simple_instrument_check.yaml` | Separate bench checkout settings |
| `configs/nmr/analysis.yaml` | Post-run processing defaults; no authority over either live stage deadline |

Both syringes currently use 20 mm diameter / 20 mL capacity. Channel 1 starts
empty; Channel 2 starts with 5.00 mL. Manual transfers default to 1.0 mL at
10.0 mL/min; those defaults do not change the experiment's transfers. The
fixed-time YAML's 8 scans takes precedence over `NMR_DEFAULT_SCANS`.

## Terminal commands

Set paths in each new PowerShell terminal. On the instrument laptop use its
existing verified Python (the existing `.venv` is the documented default).

```powershell
cd C:\code\chemyx_pump
$env:Path = "$PWD\.venv\Scripts;" + $env:Path
$Si6Fixed = "configs\experiments\si6_fixed_48h_6h.yaml"
$Si6Machine = "configs\machines\si6_real_COM4.yaml"
$Si6Needle = "arduino\configs\arduino_real_COM3.yaml"
$Si6Manual = "configs\chemyx\manual_control.yaml"
```

Configuration validation and accelerated simulation contact no hardware:

```powershell
python scripts\04_si6_fixed_time_run.py --validate-config --config $Si6Fixed --machine-config $Si6Machine
python scripts\04_si6_fixed_time_run.py --mock --config $Si6Fixed --machine-config $Si6Machine
```

On WORK, with the rig physically prepared, the actual experiment is one
command that starts directly. There are no interactive prompts at startup,
between observations or at the phase transition:

```powershell
python scripts\04_si6_fixed_time_run.py --live --config $Si6Fixed --machine-config $Si6Machine --arduino-config $Si6Needle
```

With the preset's default paths, that is equivalent to:

```powershell
python scripts\04_si6_fixed_time_run.py --live
```

For COM6 change `$Si6Machine` to `configs\machines\si6_real_COM6.yaml` and
use the explicit command. A compatible official pump HOME must already exist
for that port from manual setup. Startup preserves it and logs your manual
return to its existing loading; it never establishes a replacement HOME.

Separate checks are available on WORK. They are not automatically invoked
by the run command, and their reports are not required startup tokens:

```powershell
python scripts\04_si6_fixed_time_run.py --nmr-check --config $Si6Fixed --machine-config $Si6Machine
python scripts\03_simple_instrument_checkout.py --live --config configs\checkouts\si6_simple_instrument_check.yaml --machine-config $Si6Machine
```

The NMR-only check uses the actual fixed-time worker with 8 scans and creates
no pump/needle state or dose reservation. It is the check that exercises the
new acquisition path; the separate bench checkout uses the older NMR path.

After acquisition, run analysis independently:

```powershell
python scripts\05_analyze_fixed_time_run.py
```

Rerunning this analysis creates a new analysis folder and never repeats pump,
needle or reagent operations.

## New launch policy (updated 2026-10-19)

Every launch is a new experiment with Channel 1 empty, Channel 2 at 5.00 mL,
and new reaction paths. The needle uses the saved Arduino-only HOME and tracked
current position. The runner starts directly, moves to that HOME,
logs the return, then raises to UP. It does not define a new HOME for either instrument.

| Previous software state | New fixed-time launch |
|---|---|
| RESERVED, DISPATCH_INTENT, CONFIRMED or unreadable old dose record | Ignored as admission history; original bytes retained |
| Old pump UNKNOWN/off-HOME displacement with a compatible existing HOME | Record manual return to that HOME loading; preserve original HOME definition |
| Missing/corrupt pump snapshot, original HOME recoverable from its journal | Recover original HOME, then record manual return to it |
| Pump HOME never established, unrecoverable or incompatible with this port/syringe | Manual setup must supply a valid HOME; experiment never invents or replaces it |
| Needle at a known position away from its established HOME | Move to the same saved HOME=0, then UP=1 |
| Needle UNKNOWN/missing/corrupt or HOME-pending marker | Preserve it; Arduino-only recovery must restore a usable reference before automatic motion |
| Old failed report or stale PID/owner file alone | Does not block launch |
| Another controller actually owns the rig | Must exit; no concurrent physical control |
| Current serial/STOP/motion failure or required new evidence cannot be written | Current run fails; physical completion is not fabricated |

Dose evidence is now local to each new results folder:
`state/si6_doses/<experiment_id>.json`, or `mock_state/si6_doses` in a mock.
The global `runtime/si6_doses` history is neither scanned nor rewritten by the
fixed-time runner. The guard still allows at most one dose in the current run.

The shared pump ledger logs `chemyx_position_manual_return_home` and restores
current displacement bookkeeping to zero based on the user's prelaunch manual
return to the existing HOME loading. This sends no pump motion, does not measure
the plunger and preserves the original HOME receipt, geometry, identity and
movement totals. The needle
loads its existing reference without resetting it. Its current tracked position
updates only as movement completes. The experiment logs
`startup_needle_home_requested` and `startup_needle_home_completed`, including
starting position, target=0, movement required and `reference_redefined: false`.
The Arduino-only HOME-establishment journal is preserved. No saved estimate is
a physical measurement.

A new run starts both complete clocks again and later delivers its own dose;
it is not a continuation after an already-dosed reaction.

The full schedule and chemistry settings remain 48 hours / 120 minutes,
1.86 mL at 1 mL/min, then 6 hours / 30 minutes, with 8-scan raw NMR acquisition.
NMR and optional output failures do not shorten either phase.

See [05 — Restart behavior](05_restart_behavior.md) for operator instructions.
The standalone manual tools and older production runner keep their existing
reconciliation rules; this policy is scoped to `04_si6_fixed_time_run.py`.

## Local validation

Regression tests use the live-mode control path with pump and Arduino
transports replaced by fakes. They test returning from several known positions
to the existing HOME before UP, prohibit writes that establish a new needle
zero, and exercise an actual mock Arduino-only set-home/jog sequence followed
by experiment startup using the same state file. Missing/corrupt/pending or
uncertain needle state remains unchanged. Existing full-duration, prior-dose
restart and NMR-failure tests remain. No physical instrument is contacted.

The automatic-launch/HOME-preservation review adds full 48-hour / 6-hour
live-path simulations with fake transports, successful and failed NMR, input
forbidden, and Chemyx `set_home` forbidden. Original pump HOME fields must remain
identical, including after interrupted movement and a prior dose. Missing or
incompatible official HOME is never invented. No real hardware is contacted
and no offline package is generated. Validation results are recorded below.

Final validation on 2026-10-19: **154 tests passed in 653.69 seconds** across
fixed-time scheduling/NMR/restart tests, saved-HOME live-path simulations,
post-run analysis, documentation links and standalone pump manual control.
Both COM4 and COM6 configuration validation passed with 8 scans. These were
local tests with fake instrument transports; no real hardware was contacted.

The two full live-path simulations each completed 48 hours, exactly one dose,
then 6 hours and all 36 sample slots. One used successful NMR replies; the
other raised an NMR connection failure at every acquisition. Both prohibited
interactive input and live Chemyx HOME establishment, preserved original HOME
fields and reopened the pump ledger afterward without recovery notices.
