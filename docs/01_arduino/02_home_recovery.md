> [Documentation home](../00_START_HERE.md) | [Arduino needle](00_START_HERE.md)

# One-command Arduino HOME and position recovery

On the instrument laptop, from the repository root:

```powershell
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
```

Answer the single `y`. No acknowledgment flag is required. HOME=0 is the sampling
position inside the flask; UP=1 is raised; DOWN is HOME=0. Calibration remains
200 steps/unit with the existing direction. Physical position is not measured
by an encoder or HOME switch.

## Known reference: absolute movement

If the saved reference is valid and matches calibration, HOME checks controller
communication/idle state, asks once, then moves to absolute zero. An already-zero
needle stays there. Completion, idle status, persisted zero and readback must all
verify before success. Normal experiment startup accepts the saved reference;
there is no need to redefine HOME before every experiment.

## UNKNOWN reference: operator-confirmed zero, without movement

For missing, corrupt, interrupted, calibration-mismatched or incomplete recovery
state, HOME does not command movement. It first checks communication and idle
state, then displays:

```text
Needle position is UNKNOWN.

Physically place the needle at HOME=0.
Confirm the needle is stopped and at the
correct sampling position.

Establish current position as HOME=0? [y/N]:
```

Physically place and inspect the stationary needle at the correct sampling depth,
then answer `y`. The command saves the operator-confirmed zero and calibration,
reads the record back and rechecks controller readiness. It never sends a JOG,
firmware HOME or motion-configuration command in this path. Declining leaves the
reference untouched. Corrupt bytes are backed up only after confirmation.

`set-home` and `confirm-home` explicitly choose this reference-setting behavior
even when the saved position is valid. `return-home` aliases the adaptive `home`.
UP/DOWN (and `--steps N`, see [01 — Manual commands](01_manual_commands.md)),
status and STOP need no confirmation:

```powershell
python arduino\scripts\needle_control.py set-home --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py stop --live --config arduino\configs\arduino_real_COM3.yaml
```

## What success and fault clearing mean

```text
ARDUINO HOME COMPLETE

Saved position: HOME = 0
Controller: READY
Position tracking: VALID
Active position fault: CLEARED

Needle ready for normal operation.
```

The following line identifies completed absolute movement or operator-confirmed
reference without movement. The output explicitly states that physical position
was not independently measured. "CLEARED" refers only to resolved needle-position
tracking, not the previous experiment's overall outcome or other instruments.

- Moving, failed communication, active limit inputs, motor timeout and unrelated
  firmware faults reject recovery. HOME does not blindly send `CLEAR_FAULT`.
- Only firmware `COMMUNICATION_LOSS` may be cleared after communication returns,
  idle/limit checks pass, the operator establishes zero and the saved reference
  passes readback. The original latch is retained in the recovery journal.
- An unresolved saved STOP failure blocks HOME. Use the existing standalone
  `stop` command to obtain a verified STOP/idle response first; this restores no
  position by itself. If STOP cannot verify, inspect the hardware/connection and
  do not continue. No extra confirmation or acknowledgment flag is added.
- Pump faults, dose reservations, motion guards and previous experiment journals
  are not modified. HOME does not authorize replay or resume an interrupted dose.

## Records and interrupted verification

The command prints its unique recovery directory under `results.run_root_dir`
from the Arduino config (normally `runs/arduino`). Its append-only
`operation_journal.jsonl` retains the prior state/fault, UTC confirmation time,
recovery basis, controller evidence, persistence verification and outcome.
Existing experiment journals remain unchanged. Corrupt state backups are retained.

`needle_state.json.home_pending.json` blocks experiment/UP/DOWN startup until all
HOME checks and the completion journal succeed. A crash or failed final write,
readback, status or journal leaves recovery blocked, even if the JSON contains a
previously written valid flag. Do not delete the marker or historical records.
After resolving the failure, run `home` again, inspect the sampling position and
answer its single confirmation. It takes the manual-reference path for an
incomplete recovery. The main experiment still has only its single startup `y`;
it never silently re-establishes an unknown reference.
