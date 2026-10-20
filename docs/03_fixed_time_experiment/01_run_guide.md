> [Documentation home](../00_START_HERE.md) | [Three-instrument fixed-time experiment](00_START_HERE.md)

# SI6 fixed-time acquisition: WORK operating guide

## 1 — Open PowerShell and set configuration variables

Run from the repository root. Set these shortcuts in **each new terminal**:

```powershell
cd C:\code\chemyx_pump
$env:Path = "$PWD\.venv\Scripts;" + $env:Path
$Si6Needle = "arduino\configs\arduino_real_COM3.yaml"
$Si6Machine = "configs\machines\si6_real_COM4.yaml"
$Si6Fixed = "configs\experiments\si6_fixed_48h_6h.yaml"
$Si6Manual = "configs\chemyx\manual_control.yaml"
$Si6Checkout = "configs\checkouts\si6_simple_instrument_check.yaml"
python -c "import sys; print(sys.executable)"
```

For the alternate pump port set `$Si6Machine = "configs\machines\si6_real_COM6.yaml"`.
The commands below pass this to both pump tools and experiment. Arduino stays COM3;
NMR uses the existing Ethernet settings. Standalone manual HOME records belong
to their COM port. The fixed-time runner preserves the original pump HOME
definitions and logs your manual return to their existing HOME loading.
The needle retains the official HOME established by the Arduino-only script.
Startup returns to that saved HOME; it does not define a new needle zero.
No prior-dose reconciliation is required.
Use an existing verified Python if it is installed somewhere other than `.venv`.

## 2 — Manual Arduino

```powershell
python arduino\scripts\needle_control.py status --live --config $Si6Needle
python arduino\scripts\needle_control.py up --live --config $Si6Needle
python arduino\scripts\needle_control.py down --live --config $Si6Needle
python arduino\scripts\needle_control.py up --steps 50 --live --config $Si6Needle
python arduino\scripts\needle_control.py down --steps 50 --live --config $Si6Needle
python arduino\scripts\needle_control.py set-home --live --config $Si6Needle
python arduino\scripts\needle_control.py home --live --config $Si6Needle
python arduino\scripts\needle_control.py stop --live --config $Si6Needle
```

Run individual commands as needed. `up` and `down` move at once, with no
confirmation prompt, and work before any HOME exists.
Manual UP adds one movement count; DOWN subtracts one, using the existing
200 STEP increment and verified physical direction. `up, up, down, down, down`
tracks `+1, +2, +1, 0, -1`. A missing HOME starts an **uncalibrated** relative
count at zero; it is persisted across script exits, and cannot authorize automation.
Configured bounds remain −3 through +5. Inspect clear physical travel before
each jog: there are no limit switches, and a saved zero is not physical evidence.
Do not change the reference to bypass a travel limit.

`--steps N` moves N motor steps instead of 200, for that one command (at most
1600, the configured −3 to +5 travel). A multiple of 200 counts as whole
increments and keeps the count and any saved HOME. Any other size resets the
count and **clears a saved HOME**: finish positioning, then run `set-home`
again before the experiment, which refuses to start without a valid HOME.

`set-home` records the **current stopped location** as zero without movement.
Physically verify that location is the correct sampling HOME in the flask
before explicitly using `set-home`. This Arduino-only action establishes and
logs the official reference. The fixed-time experiment loads this same record
and MOVES to its HOME; it never sets a new needle zero. HOME/DOWN means
absolute 0 and UP absolute 1 within that established reference. `home` returns to calibrated zero; if the
reference is unknown it asks for physical placement/verification before recording
zero. Interrupted movement remains uncertain until inspected and re-referenced.
STOP remains available without a valid position record.

## 3 — Manual Chemyx

Both manual defaults are **1.0 mL at 10.0 mL/min**, 20.0 mm diameter / 20.0 mL
capacity. Adjust each `channels.<n>.manual_transfer` in `$Si6Manual` in one file,
or give one command its own volume and rate with `--volume <mL>` (infuse and
withdraw) and `--rate <mL/min>` (infuse, withdraw and home), for example
`withdraw --channel 1 --volume 2.5 --rate 5 --live ...`. These do not change the
YAML or the fixed-time recipe's sampling or dose settings.

Syringe diameter and capacity are set in two files that must agree:
`channels.<n>.syringe.diameter_mm` / `capacity_ml` in `$Si6Manual`, and
`pump.channels.'<n>'.syringe_diameter_mm` / `syringe_capacity_ml` in `$Si6Fixed`.
The experiment refuses to load if they differ. After a change, run `set-home`
for the affected channel; manual moves and the experiment refuse a HOME saved
with the old syringe settings.

```powershell
python scripts\chemyx_control.py status --channel 1 --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py status --channel 2 --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py withdraw --channel 1 --live --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py infuse --channel 1 --live --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py withdraw --channel 2 --live --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py infuse --channel 2 --live --allow-channel2-infusion --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py set-home --channel 1 --live --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py set-home --channel 2 --live --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py home --channel 1 --live --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py home --channel 2 --live --config $Si6Manual --machine-config $Si6Machine
python scripts\chemyx_control.py stop --channel both --live --config $Si6Manual --machine-config $Si6Machine
```

Select the command needed. Channel 2 manual infusion, including an infusion HOME
return, requires its line disconnected from every reactor and routed to waste;
add `--allow-channel2-infusion` for such a HOME return too. Withdrawal requires
the intended refill source. The flag never overrides uncertain/reserved dosing.
Channel 1 HOME loading is 0 mL; Channel 2 is 5.00 mL. Preserve compatible existing
HOME records. `set-home` requires physically verified loading/position and does
not move the plunger. Software displacement is not a volume measurement.

## 4 — Run standalone instrument checkout

This is a separate optional bench workflow, not a software prerequisite for
launching or restarting the fixed-time experiment.

Isolate both test paths from the reactor, route Channel 2 to waste, and retain
known starting references. The existing checkout independently tests needle,
both pump channels and a **two-scan NMR** acquisition; no liquid is pumped into
the NMR during this bench check. Its existing purpose and processing are unchanged.

```powershell
python scripts\03_simple_instrument_checkout.py --validate-config --config $Si6Checkout --machine-config $Si6Machine
python scripts\03_simple_instrument_checkout.py --live --config $Si6Checkout --machine-config $Si6Machine
```

One `y`; inspect its printed result. Restore production fluid paths/loading.

## 5 — Check the NMR through the experiment's own worker

This is an optional separate acquisition check; the experiment does not require
a successful check report or token before starting.

The checkout above uses the older acquisition code. Before committing reagent,
send one acquisition through exactly the worker and 8-scan settings the
fixed-time experiment uses. It opens no pump, needle, position ledger or dose
record, and processes nothing:

```powershell
python scripts\04_si6_fixed_time_run.py --nmr-check --config $Si6Fixed --machine-config $Si6Machine
```

One `y`. Continue only if it prints `NMR CHECK NMR_SAVED` and a
`results\fixed_time\nmr_check\<id>\nmr_raw\nmr_check_0001.dx` path. On
`NMR_FAILED` or `NMR_MISSING` do not start the long run: keep that folder (its
`nmr_check.json` records the instrument's exact status replies before START and
at the end) and bring it back to HOME for diagnosis. It refuses while an
experiment owns the NMR.

## 6 — Run NEW fixed-time experiment

WORK only. Keep WORK powered and awake for 54+ hours: set the power plan's
sleep and hibernate to **Never** (plugged in), turn off USB selective suspend,
and pause Windows Update so it cannot restart the laptop mid-run. Confirm syringe geometry, loading,
correct tubing and both pumps stopped. Before EVERY launch manually restore:

- Channel 1 at its established HOME loading: empty (0 mL).
- Channel 2 at its established HOME loading: 5.00 mL.
- Reactor and fluid paths prepared for a NEW complete experiment.
- Arduino HOME already established with the Arduino-only script and the current
  needle position tracked; the needle may start away from HOME.

Launching `--live` starts directly with no interactive confirmation. The runner loads the saved Arduino reference,
returns the needle to its existing HOME=0, logs the requested/completed HOME
return, then raises to UP=1. It never treats the current starting location as
a new HOME. Missing/uncertain needle tracking is left intact for the standalone
Arduino recovery workflow. Both instruments need their existing HOME definitions
from manual setup; the experiment never invents a missing reference.
Old dose records and off-HOME pump estimates do not require reconciliation;
startup logs that you manually returned the pumps to their established HOME
loading. Only their current displacement bookkeeping is set to zero; the
original HOME receipt, geometry, identity and movement history are preserved.
No pump HOME motion is sent at startup. Manually restore that loading before
every launch; the script does not measure it.

Default COM4 launch:

```powershell
python scripts\04_si6_fixed_time_run.py --live
```

Explicit COM4 selection:

```powershell
python scripts\04_si6_fixed_time_run.py --live --config $Si6Fixed --machine-config configs\machines\si6_real_COM4.yaml --arduino-config $Si6Needle
```

COM6:

```powershell
python scripts\04_si6_fixed_time_run.py --live --config $Si6Fixed --machine-config configs\machines\si6_real_COM6.yaml --arduino-config $Si6Needle
```

No startup `y`, HOME acknowledgment, dose acknowledgment or phase-transition
confirmation is requested. Stage 1 lasts 48 scheduled
hours, observations at 0, 2, …, 46 hours. After confirmed single Channel 2 infusion
of 1.86 mL at 1.00 mL/min (3.14 mL retained), Stage 2 starts a new 6-hour clock,
observations at 0, 0.5, …, 5.5 hours. Chemistry acquisitions use **8 scans**.
`CONFIRMED` in progress messages means the program received and recorded device
completion evidence; it is not a request for operator input.
The new YAML's `nmr.scans` wins over a leftover `NMR_DEFAULT_SCANS` environment
setting; change the selected fixed-time YAML intentionally to change the scan count.
These offsets schedule physical cycle starts; the NMR request follows that cycle's
sampling and settle operations. Later spectral analysis must use the raw JCAMP
acquisition metadata for actual NMR times.
Monotonic clocks never reset for a missing spectrum. No cycle starts at or after
a deadline; an admitted physical cycle/acquisition/cleanup can finish afterward.
Missed schedule slots are skipped without catch-up bursts. The dose and safe
finishing operations are additional wall-clock time beyond the 54 scheduled hours.

Configuration check and HOME-only accelerated simulation (no hardware):

```powershell
python scripts\04_si6_fixed_time_run.py --validate-config --config $Si6Fixed
python scripts\04_si6_fixed_time_run.py --mock --config $Si6Fixed
```

## 7 — Results

Each intentionally new launch creates a unique `SI6_FIXED_<UTC>_<random>` directory
under `results/fixed_time/live` (or `mock`). Old results are never overwritten.

| File | Evidence |
|---|---|
| `nmr_raw/*.dx` | Available raw TD JCAMP FIDs; no fake file for failures |
| `nmr_raw/*.request.json`, `*.result.json` | Live NMR request, status/receipt, retrieval failure diagnostics |
| `observations.jsonl` | Every finished observation, `NMR_SAVED`, `NMR_MISSING`, or `NMR_FAILED`, error, elapsed schedule, cleanup |
| `operation_journal.jsonl` | Required motion/STOP evidence and monotonic stage starts, deadlines and completion |
| `run_state.json` | Derived instrument operation state |
| `instrument_commands.log` | Driver commands and acknowledgments |
| `configuration.json`, `report.json` | Config snapshot, mode, stage timing, observations and overall outcome |
| `channel2_dose.json` | Copy of dose evidence and canonical record path |

New fixed-time dose evidence is local to its results folder:
`state/si6_doses/<experiment_id>.json` (mock: `mock_state/si6_doses`). Old global
`runtime/si6_doses/*.json` files remain untouched and are not read by this runner.
Pump HOME and movement history remain `runtime/chemyx_state`; needle state remains
`runs/arduino/needle_state.json` with manual movement journal beside it.
Mock state is inside its unique results directory. No NMR phase correction, FFT,
peak analysis, metadata timestamp validation or endpoint calculation is run.
Missing spectra and their exact errors are logged; hardware completion is never
inferred from an NMR result. The NMR child has an outer timeout (default 430 s),
and a later START requires positively confirmed idle status. Uncertain/busy NMR
skips that acquisition while verified physical sampling/cleanup continues.
After its one START the worker keeps polling (up to `max_wait_seconds`, 300 s)
through running, lagging, unrecognized or failed status reads, and saves raw TD
only from a completion that proves it is this acquisition (fresh receipt or an
observed running-to-idle change). Raw text is written byte-for-byte as received.

## 8 — Errors and recovery

Ctrl+C stops the current experiment and attempts STOP on the instruments.
There is no in-place pause/resume. To start again, manually restore the four
starting conditions in section 6 and launch the same command. It starts directly
and always begins a NEW 48-hour / one-dose / 6-hour run.
The new run schedules its own reagent dose; it does not continue the old reaction.

No prior-dose reconciliation or override flag is required. Old RESERVED,
DISPATCH_INTENT, CONFIRMED and unreadable dose records remain as history.
Original pump HOME definitions are preserved; current displacement bookkeeping
records your manual return to their existing HOME loading. Missing or incompatible
pump HOME must be established during manual setup. The needle uses its
existing Arduino-only HOME and tracked position. The experiment records
`startup_needle_home_requested` / `startup_needle_home_completed` and moves to
that saved HOME; it never overwrites an uncertain record with a new zero or
removes a pending needle HOME-recovery marker. Use the standalone Arduino
workflow if the reference has actually become uncertain.

NMR missing data, acquisition/retrieval timeouts, busy/unknown NMR and NMR lock
failure are logged without shortening either stage. NMR analysis is not called.

Current pump/needle connection, STOP or motion failures still stop the current
run. Required new motion/position/dose evidence must be writable. Another
controller actually holding the pump/Arduino lock must exit before launch;
a stale owner file alone is not an active controller. These checks concern the
current execution, not an old experiment status.

The standalone manual pump/needle tools and older experiment controller retain
their separate state rules. The Arduino-only script remains the authority for
setting/logging the official needle HOME; its valid saved reference is used by
the experiment. See [current restart behavior](05_restart_behavior.md).

## Update through Git and the WORK external drive

HOME source changes are committed and pushed to Git by the user. WORK pulls
them, then transfers the updated source and needed local assets to the second
WORK laptop using the external hard drive. HOME does not generate an offline
package or release ZIP for this workflow.

Follow [04 — Git and drive update](04_offline_deployment.md). The existing
Python environment is sufficient for the restart change; there is no new
dependency or firmware requirement. Preserve the relevant rig's configuration,
state and results during the WORK-side transfer.
