# Independent Expert Review

Review date: 2026-08-03  
Review scope: `arduino/` firmware, Python controller and transports, staged-test
scripts, mocks, unit tests, configuration examples, safety documentation, and
the reused Chemyx/NMR production interfaces.  
Review mode: static, unit, and validation-only. No serial port was enumerated or
opened, no network instrument endpoint was contacted, and no physical action was
requested.

## Executive status

**Overall status: BLOCKED for live motion pending physical commissioning.** The
Arduino protocol and staged Python implementation pass the available no-hardware
suite. Test 1 is software-ready, while Tests 2, 3, and 4B correctly remain
blocked by missing hardware facts and prerequisite live records. Test 4A has a
sound no-motion design but shares the legacy Chemyx I/O deadline limitation
described below.

The example YAML files correctly remain fail-closed. The checked-in firmware
also correctly keeps `MOTION_COMMISSIONED=false`, `LIMITS_COMMISSIONED=false`,
and all axis commissioning values at zero. Nothing in this review authorizes
changing those values or running hardware.

## Verification evidence

- `python -m compileall -q arduino`: **PASS** using the repository's `ai`
  environment.
- `pytest arduino/tests -q`: **45 passed**.
- Validation-only invocation of Test 1, Test 2, Test 3, and Test 4 completed
  without opening a hardware endpoint. The example configuration reports 21
  missing live Test 2 requirements and 23 missing live Test 3 requirements, as
  expected for placeholders.
- Firmware was reviewed statically. `arduino-cli` is not installed in the
  review environment, so an UNO R4 Minima compile was **not** independently
  verified.

## Common firmware and host findings

### Remaining software limitations

1. **High -- make the advertised wall-clock limit enforceable across legacy
   Chemyx calls.** The staged scripts check a monotonic deadline, and NMR calls
   receive bounded request budgets. However, the reused `Pump.send_command()`
   has no write timeout and `_read_response()` has no total response deadline;
   a blocking/continuously streaming serial device can outlive the 60/120-second
   script deadline before the next check. Propagate the remaining stage budget
   into bounded pump I/O, or isolate legacy calls behind a mechanism that can be
   terminated at the hard deadline.

### Safety behavior that passed review

- Motion is disabled by default in both host and firmware, and Test 1 contains
  no motor verbs.
- UNO R4 outputs are preloaded before output mode; documentation requires an
  externally fail-safe, verified open-collector/open-drain interface and
  prohibits direct UNO-to-DM542T wiring.
- The firmware uses bounded serial buffering, rejects extra arguments, checks
  numeric range errors, caps steps/speed, rejects both limits active, stops on
  communication loss/timeouts, and marks interrupted commanded position
  uncertain.
- Homing qualifies the upper limit for a stable interval before assigning zero.
  Normal directional-limit activation latches a fault rather than silently
  redefining position.
- The host checks device/board/version identity, positive sequence numbers,
  ACK-before-DONE ordering, sequence equality, firmware-spec ACK verb equality,
  required STATUS fields, and the firmware's commanded-position-only
  declaration. The fake follows the same verb-only ACK contract, including a
  wrong-verb negative test.
- Failed result records include `motion_attempted`; only failed, dispatched
  motion records with the current inspection clearance latch later live motion.
  A successful run of a different motion stage no longer clears an earlier
  failure. The dispatch marker fires after the full newline payload is written
  but before the fallible flush. An expired controller deadline before any write
  has a passing regression that verifies no write, no callback, and no loss of
  established position certainty. Every recorded controller path preserves the
  firmware version after READY; failures before motion remain explicitly marked
  `motion_attempted=false`.
- Test 1 uses best-effort `LED OFF` cleanup if LED ON or BLINK does not complete.
- Test-specific hardware fingerprints no longer invalidate Test 1/Test 2 merely
  because later commissioning flags or mechanical state change.
- Test 2 and Test 3 estimate their complete planned motion before `ENABLE`.
  Test 4B reserves the pump return and safe-UP move before NMR and rejects
  same-direction or unequal-volume “return” actions.
- Test 3 has a durable no-motion switch preflight and repeats the active/released
  switch observation immediately before motion.
- Test 4A rejects moving, enabled, or faulted Arduino state and empty/explicitly
  negative Chemyx/NMR readiness responses.
- Test 4B is sequential and has no concurrent motor/pump/NMR execution path.
  It preserves partial event and NMR artifacts and makes no automatic retry or
  automatic position-recovery move after failure.

## Test 1 -- Arduino connection and LED

**Status: PASS (software review only; no live result).**

### Findings

The command path is Arduino-only: READY identity, PING/PONG, STATUS, LED on/off,
BLINK, and final STATUS. Host motion interlocks are false, firmware motion is
uncommissioned, and the test checks that the driver remains disabled and idle.
The 59-second deadline is established before endpoint setup.

### Corrections made before final review

- Added strict READY identity/version validation and finite serial I/O.
- Added final LED, enabled, and moving-state checks.
- Made Test 1 evidence independent of later motion/limit commissioning flags.
- Preloaded firmware output levels and documented required external fail-safe
  biasing during reset/unpowered states.

### Remaining physical requirements

- Compile and upload the exact reviewed sketch to an UNO R4 Minima.
- Record a unique COM port/fingerprint and confirm the READY identity/version.
- Perform only the documented Arduino USB/built-in LED setup; leave the DM542T,
  motor, limits, needle axis, pump, and NMR disconnected/unpowered for Test 1.

## Test 2 -- Unloaded motor

**Status: BLOCKED.**

### Findings

The script is intentionally fail-closed, requires exact confirmation, computes
a forward/pause/reverse budget before enabling, attempts STOP/DISABLE on
failure, and requires durable operator observations and acceptance after the
run. Its mock path does not unlock live prerequisites.

The firmware, fake, and host now agree on the verb-only JOG ACK contract. Live
execution nevertheless remains blocked by the current example's 21 missing
physical prerequisites, missing prior live evidence, and the checked-in
firmware's deliberate motion-disabled state.

### Corrections made before final review

- Added strict DM542T model, supply, current/microstep, motor, coil-pair, fuse,
  emergency-disconnect, interface, polarity, and unloaded-shaft gates.
- Added firmware/config cross-checks and immutable host/firmware step/speed
  caps.
- Added bounded post-run observations for direction, noise, vibration,
  temperature, approximate return, and explicit acceptance.
- Kept limit switches uncommissioned for the disconnected Test 2 stage.

### Remaining physical validation

- Professional review of the exact open-collector/open-drain interface,
  inversion, common reference, external fail-safe bias, and DM542T 5 V selector.
- Exact NEMA 17 model/datasheet, rated phase current, full steps/revolution, and
  measured coil pairs.
- Verified 24 V supply current rating, fuse, DM542T current and microstep switch
  positions, enable polarity, and emergency driver-power disconnect.
- Motor mechanically disconnected from the axis, shaft area clear, conservative
  step/speed values reviewed, firmware rebuilt with only motion commissioned,
  and Test 1 passed with matching identity.

## Test 3 -- Needle axis

**Status: BLOCKED.**

### Findings

The intended live path requires a matching Test 2 result and a separately
recorded active/released limit-switch preflight. It repeats the five-sample
switch check before enabling, verifies firmware commissioning values, homes
slowly, backs off, moves to a positive safe-UP position, and performs exactly
two bounded DOWN/UP cycles. It does not automatically retry or guess position
after a failed move.

The firmware, fake, and host now agree on the verb-only HOME/JOG/MOVE_ABS ACK
contract. Live execution remains blocked by absent physical commissioning and
missing prerequisite records.

### Corrections made before final review

- Added normally-closed upper/lower limit gates, both-limits faulting, homing
  debounce, hard travel/speed/acceleration limits, and firmware/config equality
  checks.
- Added strict `0 < safe UP < test DOWN < maximum travel` geometry and verified
  `steps_per_mm = full_steps_per_rev * microsteps / lead_mm_per_rev`.
- Added a durable no-motion switch preflight plus the immediate repeated check.
- Added a full pre-enable runtime budget and conservative two-cycle limit.

### Remaining physical validation

- All Test 2 physical evidence and a successful matching live Test 2 record.
- Exact lead screw lead, coupling/orientation, travel direction, maximum safe
  stroke, conservative safe-UP/DOWN coordinates, and physical hard stops.
- Installed normally-closed switches; verified pull-up/polarity behavior,
  active/released states, broken-wire behavior, repeatability, bounce/EMI, and
  correct switch for each motion direction.
- Verified the vertical axis cannot fall dangerously on lost holding torque and
  that the emergency driver-power disconnect is reachable.
- Firmware rebuilt with exact reviewed travel, speed, acceleration, homing
  speed, polarity, motion, and limit constants; compile/upload evidence; matching
  limit-preflight result; operator clear of the mechanism.

## Test 4A -- Connection-only integrated preflight

**Status: PASS WITH REQUIRED FIXES (software design only; no live result).**

### Findings

The path performs Arduino PING/STATUS, Chemyx HELP, and NMR PING only. It rejects
Arduino movement, enabled driver, latched fault, COM-port collision, and empty or
negative readiness results. No pump start/configuration, NMR acquisition, or
Arduino motion command is present.

The implementation and production-interface assertion pass the local suite. It
is suitable as a no-motion preflight after resolving or explicitly accepting
the strict wall-clock limitation. No claim is made that opening a specific
pump/NMR endpoint is physically action-free; that must be confirmed against the
installed instrument configuration.

### Corrections made before final review

- Added explicit connection-only mode, exact confirmation, distinct-port check,
  physical-action-empty report, readiness validation, and a deadline created
  before endpoint setup.
- Reused the repository's production `Pump.help()` and `NmrRpcClient.ping()`
  interfaces rather than duplicating protocols.

### Remaining physical validation

- Correct local machine configuration, distinct Arduino/Chemyx ports, and NMR
  host/route/timeout settings.
- Instrument-manual confirmation that HELP and PING are read-only for the exact
  installed firmware/software versions.
- Successful matching live Test 1 and successful connection-only responses from
  all three endpoints, with the needle axis and pump confirmed idle.

## Test 4B -- Full sequential integration

**Status: BLOCKED.**

### Findings

The code enforces the requested sequence: known/homed safe-UP with holding
torque, needle DOWN, approved pump action, configured 1D NMR, needle safe-UP,
then an opposite and equal-volume pump return. It reserves post-NMR recovery
time, checks the NMR artifact, records a combined ordered event list, and stops
Arduino/pump activity on failure without automatic retry.

The live path remains blocked by all prior-stage requirements and the legacy
pump hard-deadline limitation. The
checked-in integrated example intentionally has null action/settling/artifact
selections. In addition, the obvious 5 mL forward/5 mL return pair in the
referenced experiment at 5 mL/min consumes 120 seconds of pump travel alone, so
it cannot fit the 120-second hard limit with needle motion, NMR, settling, and
I/O. The current prebudget correctly rejects that sequence before hardware
action; a shorter action must come from an independently approved existing
configuration rather than invented parameters.

### Corrections made before final review

- Added matching successful live-result prerequisites for Tests 1, 2, 3, and
  4A plus continuity confirmation since Test 3.
- Added initial homed/known/safe-UP/enabled/idle/fault-free checks.
- Added opposite-direction/equal-volume return validation and pre-action total
  budget validation.
- Added NMR budget limiting with explicit reserves for safe-UP and pump return,
  artifact suffix/existence validation, sequential instrument interlocks,
  partial event/artifact preservation, and best-effort STOP cleanup.

### Remaining physical validation

- Every Test 1-3 and 4A requirement and matching successful live record; no
  reset, power loss, manual axis motion, or configuration change since Test 3.
- Approved existing pump forward/return actions that fit the full hard-runtime
  budget, syringe/volume/rate/channel verification, reservoir and line routing,
  and safe stop behavior.
- Approved existing 1D NMR diagnostic, solvent/sample compatibility, artifact
  format/location, RPC route behavior, acquisition duration, and cancellation
  behavior.
- Integrated dry rehearsal with real timings but no motion, then supervised
  staged live execution with reachable emergency disconnect and a documented
  response plan for incomplete needle position, pump uncertainty, or partial
  NMR artifacts.

## Final disposition

Do not run live motion from this repository until the exact firmware compiles
for UNO R4 Minima and the physical validations are signed off. Do not run Test
4B until the Chemyx calls are subject to an enforceable total deadline and an
approved full sequence fits inside 120 seconds.
After any failed dispatched live motion, stop, preserve artifacts, treat needle
position and pump completion as uncertain, inspect the apparatus, and create a
new explicit inspection-clearance record before another motion attempt.
