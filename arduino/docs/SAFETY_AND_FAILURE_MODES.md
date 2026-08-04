# Safety and Failure Modes

## Safety boundary

The current approved physical scope is USB-only Test 1. The DM542T requires a
verified open-collector/open-drain signal driver; direct UNO R4 GPIO wiring to
PUL, DIR, or ENA is prohibited. Motor current, microsteps, signal voltage,
inversion, enable polarity, and power-supply capacity are never inferred.

The host defaults to validation-only. Motion is denied in `NeedleController`
unless a staged script explicitly enables it after prerequisites pass. Firmware
also starts disabled and ships with `MOTION_COMMISSIONED = false`.

## Failure response

| Failure | Response | State assumption |
| --- | --- | --- |
| Missing READY/identity mismatch | Close port; no command | No motion dispatched |
| Missing ACK/DONE or sequence mismatch | Abort sequence; motion wrapper attempts STOP | Position uncertain if motion was dispatched |
| Active limit in direction of travel | Reject/stop and fault | Do not command farther toward limit |
| Both limits active | Immediate fault and STOP | Wiring/mechanics require inspection |
| Movement timeout or USB loss | Firmware stops pulses and latches fault | Position uncertain; CLEAR_FAULT only after inspection |
| Pump action failure | Existing workflow attempts/records pump stop | Pump/retained volume may be uncertain |
| NMR acquisition failure | Abort; no automatic retry | Preserve partial output |
| Failure while needle is DOWN | STOP; do not blindly command UP | Inspect and re-home using approved procedure |
| Result persistence failure | Treat evidence as insufficient | Later live test remains locked |

STOP is processed by the firmware's bounded serial loop while pulses are
generated nonblockingly. Limit inputs are checked every loop. Movement and
overall host deadlines are finite. The firmware distinguishes command-derived
position from measured physical position; no encoder is present.

## Cleanup rules

- Stop the current sequence and do not advance to another instrument.
- Attempt Arduino STOP only when communication is available and motion may have
  been dispatched.
- Use the existing Chemyx stop operation when appropriate.
- Do not issue automatic NMR retry.
- Preserve run directories and partial NMR artifacts.
- Record known/unknown device state and set the operator-inspection flag after
  any uncertain live motion before another live attempt. A failed live-motion
  result blocks another matching live motion run. After physical inspection,
  add a unique documented reference to `safety.operator_inspection_clearance`;
  this changes the hardware fingerprint and forces prerequisite tests to be
  rerun for that reviewed configuration.
- Keep the vertical driver enabled if disabling holding torque could cause a
  fall; remove driver power only through the documented emergency method.

Software stop is not a substitute for the physical emergency driver-power
disconnect, fuse, hard stops, guarded wiring, and attended operation.

Limit inputs still require bench validation for contact bounce and EMI. Firmware
halts homing pulses on the first upper-limit sample and requires a short stable
activation before establishing command zero; the host requires repeated stable
active/released STATUS samples. These checks do not replace shielded reviewed
wiring or physical validation. UNO R4 USB disconnect detection must also be
verified on the exact board/core version.
