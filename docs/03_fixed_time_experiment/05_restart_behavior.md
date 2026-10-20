# 05 — Automatic restart with existing Chemyx and Arduino HOME

[Fixed-time experiment index](00_START_HERE.md) | [All documentation](../00_START_HERE.md)

Updated 2026-10-19 for `scripts/04_si6_fixed_time_run.py`.

## Establish the official needle HOME with the Arduino-only script

Use the existing Arduino-only script to explicitly define and log the correct
sampling location. This is an operator action, not an experiment startup step:

```powershell
python arduino\scripts\needle_control.py set-home --live --config arduino\configs\arduino_real_COM3.yaml
```

`set-home` records the current physically verified stopped location as HOME=0
without movement. Its action is logged under the Arduino run directory with
an `operation_journal.jsonl`. Normal manual UP/DOWN moves share the same saved
reference and update the current tracked position. `home` moves back to that
existing HOME when the reference is valid. Detailed commands are in
[Arduino manual commands](../01_arduino/01_manual_commands.md).

## Start a new fixed-time experiment

1. Previous controller exited; both pump channels stopped.
2. Pumps manually returned to their established HOME loading: Channel 1 empty
   (0 mL), Channel 2 at 5.00 mL; new reaction paths ready.
3. The official Arduino HOME is already established and its current position
   is tracked. The needle may be away from HOME at a known position.

On WORK:

```powershell
cd C:\code\chemyx_pump
python scripts\04_si6_fixed_time_run.py --live
```

Launching `--live` starts immediately, without a startup `y` or later prompts.
The experiment:

1. Loads the same saved needle reference used by the Arduino-only script.
2. Commands an absolute return to HOME=0 using that reference; a needle already
   at HOME needs no movement.
3. Records `startup_needle_home_requested` and `startup_needle_home_completed`
   in the experiment's `operation_journal.jsonl`, including starting position,
   target, whether movement was needed and `reference_redefined: false`.
4. Raises to UP=1 and runs the sampling sequence relative to that SAME HOME.

The experiment does not change the official HOME location or replace it with
the needle's starting location. It updates the tracked current position as
moves complete. The -3/+5 software limits remain relative to the established
HOME. No additional HOME confirmation is requested when the saved reference
is valid.

## Previous dose records and restart behavior

Old RESERVED, DISPATCH_INTENT, CONFIRMED or unreadable dose records do not block
a new fixed-time experiment. No `reconcile-dose` or override flag is required.
For Chemyx, startup logs that you manually returned the pumps to their EXISTING
HOME loading. It updates current displacement bookkeeping to zero, preserving
the original HOME receipt, identity, syringe geometry and movement history.
It sends no pump HOME motion and does not measure the manually restored loading.
Old off-HOME/UNKNOWN pump displacement therefore needs no extra acknowledgment,
but the official HOME must already exist and match the port and syringe setup.
The experiment never calls `set-home` for either physical instrument.

If needle tracking is missing, corrupt, pending or uncertain, the experiment
leaves it intact and does not guess a new HOME. Restore that reference through
the Arduino-only workflow before automatic movement. This is the existing
HOME requirement, not a prior-dose reconciliation requirement.

Every launch starts a NEW 48-hour Phase 1, its own 1.86 mL dose, then a NEW
6-hour Phase 2. It does not resume an already-dosed reaction. NMR analysis never
controls these clocks; missing NMR remains nonfatal to the schedule.
Current motion/STOP faults and actual concurrent controller ownership still
stop execution. Dose evidence is local to each new results folder; historical
dose records remain untouched.

Updates travel through the [Git and external-drive workflow](04_offline_deployment.md).

The automatic-launch review tests the full 48-hour / 6-hour schedule with fake
live transports, no input allowed, Chemyx HOME redefinition forbidden, and both
successful and failed NMR outcomes. Validation results are in
[02 — Scripts, configs and restart review](02_scripts_configs_and_restart_review.md).
The HOME return was tested from saved positions -2, 0, 1 and 5, including actual
mock Arduino-only set-home/jog commands followed by experiment startup. No real
hardware was contacted.
