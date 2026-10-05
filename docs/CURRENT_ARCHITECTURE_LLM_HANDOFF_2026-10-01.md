# Current Arduino, Chemyx, and NMR architecture: LLM review handoff

Reviewed 2026-10-01, from `C:\code\chemyx_pump`, at Git HEAD
`0545a020d00395d81d57974791cb85d9ba3165b5` (2026-09-25, `paper workflow`).

This is a review of the current checkout, not a proposed replacement architecture.
No instrument was contacted. No firmware, runtime configuration, dependency,
or instrument-control code was changed for this review. Paths below are relative
to the repository root so this document can travel with the offline repository.

## 1. User intent and constraints for the reviewing LLM

The user already made a standalone repository/environment for an instrument
laptop without internet. Preserve that installation and the existing repository
structure. Prefer configuration changes and small changes to existing shared
modules if a future requirement needs code. Do not introduce a new framework,
cloud service, instrument-control stack, dependency upgrade, or rewiring as part
of routine adjustments. Do not silently replace the user's recent demo choices
with an older architecture described in the documentation.

The task is to understand three instruments individually and then how the full
experiment coordinates them. In particular, explain which timing and behavior
can be adjusted in configuration and which behavior is fixed in Python.

The repository has multiple generations of tooling. Read the active entry
points and their imports before assuming that an older script, diagram, or
commissioning document describes today's executable behavior.

## 2. Overall architecture

The laptop is the coordinator. The Arduino controls needle movement through a
DM542S stepper driver, the Chemyx controls syringe withdrawal/infusion, and the
NMR acquires spectra over a local Ethernet HTTP RPC interface. NMR processing
runs locally in Python. Internet is unnecessary for instrument communication or
the configured experiment once the environment and files are installed.

```text
scripts/01_three_instrument_system_test.py  [integrated diagnostic]
scripts/02_si6_experiment.py               [full staged experiment]
                     |
                     v
chemyx_lab/workflows/three_instrument_si6.py
  |-- Arduino: TrackedNeedle -> NeedleController -> SerialTransport
  |              -> UNO R4 Minima firmware -> D3 STEP / D4 DIR -> DM542S
  |-- Chemyx: Pump -> pyserial -> pump serial interface
  |-- NMR: run_nmr_acquisition -> NmrRpcClient -> local HTTP RPC
  |-- Processing: same Python interpreter -> scripts/nmr/process_fid.py
  |-- Measurement: QC-passing tracked peak -> area change -> plateau
  |-- Shared legacy helpers: timed pump moves, stages, scheduling, plateau
  `-- Persistence: RunRecorder -> operation_journal.jsonl -> run_state.json
```

The newest workflow reuses `chemyx_lab/workflows/si6_automated_nmr.py` instead
of replacing it. This shared legacy module supplies configuration validation,
stage construction, syringe-capacity checks, timed pump operations, scheduling,
plateau evaluation, output paths, and journal helpers. Its own script remains a
separate two-instrument workflow with manual needle checkpoints.

There is also a valve module/configuration, but the main three-instrument Si6
runner does not actuate a valve. Reagent additions are still operator actions;
they are not performed by an additional dispenser or robot in this workflow.

## 3. Entry points and what each actually does

| Layer | Files | Behavior |
| --- | --- | --- |
| Communication smoke tests | `smoke_test/01_smoke_chemyx.py`, `02_smoke_nmr.py`, `03_smoke_arduino.py` | Only establish communication; do not run the experiment |
| Individual Arduino operation | `arduino/scripts/needle_control.py` | Status, software HOME confirmation, relative logical UP/DOWN, return HOME, STOP, or one manual pulse-count jog |
| Staged Arduino bring-up | `arduino/scripts/test_01_arduino_connection.py`, `test_02_unloaded_motor.py`, `test_03_needle_axis.py`, `test_04_integrated_system.py` | Separate connection, motor, axis, and short integration tests |
| Independent pump diagnostic | `scripts/diagnostics/02_verify_chemyx_movement.py` | Configure pump, infuse, wait, STOP, settle, withdraw, wait, STOP |
| Independent NMR diagnostics | `scripts/diagnostics/03_check_nmr_connection.py`, `04_run_nmr_1d_acquisition.py` | Connection inspection or an actual configured scan |
| Level 1 integrated diagnostic | `scripts/01_three_instrument_system_test.py` | Select needle, pump, NMR, processing, or all; uses the production shared interfaces |
| Level 2 integrated experiment | `scripts/02_si6_experiment.py` | Run the entire configured sequence of reaction-monitoring stages |
| Legacy attended workflow | `scripts/02_si6_automated_nmr.py` | Chemyx + NMR automation with the operator moving the needle |
| Offline analysis | `scripts/nmr/process_fid.py` and other `scripts/nmr/` tools | Process saved data; no instrument acquisition |

### Communication smoke tests

The Chemyx smoke script takes explicit CLI settings, opens a chosen COM port,
sends `1 help\r` by default, prints the reply, and closes the port. Channel 0
omits the prefix. Defaults are 115200 baud, 8N1, 2-second timeout, and
0.2-second response delay. It does not send rate, volume, START, or STOP.

The NMR smoke script uses standard-library HTTP requests, with the prior-rig
default `http://169.254.30.54:5000`. It requests only:

```text
GET /interfaces/iStatus/PingSpectrometer
GET /interfaces/iStatus/RpcEnabled
GET /interfaces/iStatus/SpectrometerStatus
```

Its PASS means the requests succeeded; it is not an acquisition-quality check.

The Arduino smoke script opens an explicitly selected port, looks for the
expected READY identity, sends `1 PING`, requires ACK then DONE/PONG, sends
`2 IDENTITY`, and verifies `needle_controller`, `uno_r4_minima`, version
`1.2.1`, and driver `DM542S`. It sends no motion configuration or motor command.

These smoke scripts use their own command-line arguments, not the full Si6
experiment YAML. A smoke-test PASS does not establish calibrated movement,
fluid routing, NMR acquisition, or suitability for a multi-hour experiment.

## 4. Arduino needle workflow in detail

### Active firmware and transport

Active sketch: `arduino/firmware/needle_controller/needle_controller.ino`,
identity version `1.2.1`, for the UNO R4 Minima. The existing wiring is D3 STEP
and D4 DIR. ENABLE and physical limit switches are not connected in the current
demo setup. The current firmware preserves the older bridge's direction:
positive pulses use DIR LOW, negative pulses DIR HIGH.

`arduino/python/controller.py` supplies `NeedleController`. It validates the
READY device/board/version, uses numbered serial commands, and requires matching
ACK followed by DONE. Mismatched sequence numbers, wrong ACK verbs, faults,
and timeouts are errors. The controller uses bounded command waits and attempts
STOP after a dispatched motion that fails to complete.

Python applies runtime configuration with `CONFIG_IO`, `CONFIG_LIMITS`, and
`CONFIG_APPLY`. ENABLE/DISABLE only arm/disarm software pulse generation: they
do not switch an ENA wire or remove 24 V power/holding torque.

Physical firmware HOME rejects with `PHYSICAL_HOME_UNAVAILABLE`; firmware
MOVE_ABS rejects with `USE_HOST_LOGICAL_POSITION`. Host software implements
logical absolute targets by converting position differences to relative JOG.

### Current local configuration

`arduino/configs/arduino.local.yaml` is the current default for the main 01/02
scripts. It is populated for a demo, rather than the uncommissioned example
described by some older docs:

| Field | Current local value | Meaning |
| --- | --- | --- |
| `arduino.port` | `COM3` | Laptop-specific serial assignment |
| `arduino.baud_rate` | `115200` | Serial speed |
| `arduino.expected_version`, `firmware.version` | `1.2.1` | Expected firmware identity |
| `arduino.fingerprint` | `{}` | No USB VID/PID/serial filtering; firmware identity still checked |
| `firmware.motion_enabled` | `true` | Runtime motion configuration is enabled |
| `firmware.limits_enabled` | `false` | No physical limit-switch protection |
| `motion.maximum_speed_steps_s` | `100` | Speed ceiling; firmware also caps at 100 |
| `motion.maximum_acceleration_steps_s2` | `500` | Configured ramp |
| `needle.min_position`, `max_position` | `-3`, `5` | Software logical travel bounds |
| `needle.home_position` | `0` | Chosen software reference |
| `needle.up_position`, `down_position` | `1`, `-1` | Integrated workflow's named targets |
| `needle.steps_per_unit` | `200` | Motor pulses per logical unit |
| `needle.up_step_sign` | `1` | Positive logical travel uses positive pulses |
| `needle.state_path` | `runs/arduino/needle_state.json` | Persistent software position estimate |

The 200-step value is described as the prior 90-degree bench move. It is not
an automatically discovered millimeter calibration. The YAML values alone do
not prove the needle physically clears the flask or reaches the correct depth.

### Position tracking and individual moves

`arduino/python/needle_state.py` supplies `TrackedNeedle` and its JSON store.
For a logical target:

```text
signed motor pulses = (target - saved logical position)
                      * steps_per_unit * up_step_sign
```

At today's values, integrated UP +1 to DOWN -1 requests -400 pulses; DOWN to
UP requests +400. At a 100-pulse/s ceiling, each takes at least about 4 seconds,
plus acceleration and serial overhead. These are commanded pulses, not measured
physical motion.

Before dispatch the host saves `position_valid=false`. Only successful JOG
completion with the expected step-count result restores a valid logical target.
A failure or interrupted movement leaves the state invalid in that session.
Software bounds, integer targets, speed limits, the firmware 200000-step command
cap, and a movement-duration check are enforced.

There are two distinct UP/DOWN interfaces:

* `needle_control.py up` increments the current logical position by one;
  `down` decrements it by one. Repeated UP commands keep incrementing until a
  software bound is reached. They do not mean "go to named UP".
* The full workflow calls `move_absolute()` on the wrapper with the configured
  named logical target +1 or -1. Despite that compatibility method's name and
  argument naming, these are logical units, not raw motor steps.

`confirm-home` records the physically inspected current position as software
zero without motion. `return-home` moves to logical zero. The full workflow's
startup calls software HOME/return-to-zero and then moves to named UP.

`manual-up`/`manual-down` require `--steps` and send one exact pulse-count jog.
They allow `--speed`/`--acceleration`, defaulting to 100 and 500. A manual jog
invalidates logical position and disarms runtime motion configuration afterward.
This is useful for supervised individual adjustment, but it does not calibrate
HOME or verify travel.

### Important current demo behavior

The constructor currently catches an unreadable/uncertain saved state and, if
the estimate is invalid, the calibration changed, or the stored position is
outside bounds, assumes logical HOME=0 and saves a valid state with reason
`assumed_home_demo`. This is a recent executable choice. It means reconnecting
can replace uncertainty with an assumed software reference. It does not prove
the actual needle is at HOME. There is no encoder, switch, or sensor that can
detect a stall, missed steps, manual movement, or drift while unpowered.

Also, `arduino/python/config.py::require_live()` now prints advisory notes for
missing commissioning items instead of raising. The physical and runtime
checks elsewhere still matter, but matching staged test records are not enforced
by that helper as older documentation claims.

## 5. Chemyx pump workflow in detail

`chemyx_lab/instruments/chemyx.py::Pump` is the serial wrapper. It adds the
configured channel prefix, sends ASCII with carriage-return termination,
reads the reply, and checks numeric echoes for units/diameter/rate/volume.
Channel 0 means no prefix; the current experiment uses channel 1.

The shared startup configures units, syringe inner diameter, and rate. The
experiment YAML currently uses:

```yaml
pump:
  channel: 1
  syringe_diameter_mm: 20.0
  syringe_capacity_ml: 20.0
  initial_retained_volume_ml: 0.0
  syringe_safety_margin_ml: 1.0
  units: mL/min
  rate_ml_min: 5.0
  default_volume_ml: 5.0
```

Connection settings come from `configs/machines/00_machine.local.yaml`:
COM6, 115200 baud, 2-second timeout, 0.2-second response delay. COM6 is a
prior-rig setting to verify on the work laptop, not a universal assignment.

For each automated move, `run_safe_metered_move()` journals the planned and
dispatched operation, sets a signed volume, sends `start 0`, waits for the
volume/rate duration plus `workflow.pump_extra_seconds` (currently 2 seconds),
and sends STOP. Negative pump volume means withdraw; positive means infuse.

At 5 mL/min, the current move waits are:

| Move | Nominal volume/rate | Wait including 2-second margin |
| --- | --- | --- |
| Withdraw 8 mL | 96 s | 98 s |
| Withdraw 5 mL | 60 s | 62 s |
| Infuse 13 mL | 156 s | 158 s |
| Cleanup withdraw 5 mL | 60 s | 62 s |
| Cleanup infuse 5 mL | 60 s | 62 s |

The host estimates retained syringe volume from successful requested moves;
there is no direct liquid-volume sensor. Configuration validation simulates two
cycles, requires no negative retained volume, a zero net cycle balance, and
maximum retained volume plus reserve within capacity. Current maximum is
13 mL plus a 1 mL reserve, within a 20 mL configured capacity.

STOP confirmation currently means a nonempty response to the STOP command;
it is not independently measured plunger position or volume delivery. Missing
or failed STOP confirmation makes the state uncertain and prevents further
normal coordinated movement.

The independent `02_verify_chemyx_movement.py` diagnostic is different: its
default waits are explicitly 10 seconds each for infuse/withdraw, with a
2-second settle. CLI controls diameter/rate/volume and wait durations. Do not
use that timed diagnostic as evidence that a full configured volume was
delivered. It contacts real hardware unless `--mock` is selected and the
operator declines or confirms the movement prompt; `--yes` bypasses its prompt.

## 6. NMR acquisition and processing workflow in detail

### Connection and acquisition

`chemyx_lab/instruments/nmr.py::NmrRpcClient` uses local HTTP RPC through
Python's standard-library networking. Machine YAML currently specifies:

```yaml
nmr:
  host: "169.254.30.54"
  port: 5000
  scheme: http
  timeout_seconds: 10.0
  poll_seconds: 2.0
  max_wait_seconds: 300.0
```

No internet service is involved. The laptop still needs local Ethernet access
to that instrument address and the instrument's RPC service must be available.

`chemyx_lab/workflows/instrument_operations.py::run_nmr_acquisition()` is
the shared acquisition/retrieval function. Current experiment settings are
iFlow, FID output, 2 scans, receiver gain 12, auto-gain false, spectral center
5 ppm, width 20 ppm, and tracked target 5.8 ppm.

For the configured iFlow route it:

1. Builds a local timestamped `.dx` output path.
2. GETs `/interfaces/iFlow/Settings/1D` and
   `/interfaces/iFlow/ExperimentSettings` as instrument-specific templates.
3. Patches gain, auto-gain, scans, center, width, and export filename while
   preserving the remaining template fields.
4. PUTs `/interfaces/iFlow/Settings/1D`.
5. PUTs `/interfaces/iFlow/RunExperiment` with the experiment settings.
6. Polls `/interfaces/iFlow/ExperimentStatus` until its status appears idle,
   bounded by the configured wait timeout.
7. Extracts JCAMP text from the final payload and writes the `.dx` locally.

The wrapper also supports a generic Experiment route: fetch experiment
settings, start, poll, list results, and request a named result in JDX/FID form.
The configured production path is iFlow; do not change route just to modernize
the API. Acquisition calls also print a magnitude-based diagnostic peak when
possible, but that printed value does not control the newest experiment's
plateau decision.

The independent `04_run_nmr_1d_acquisition.py` exposes CLI scan/gain/route/
window/timeout/output overrides. `--dry-run` prints settings without acquisition.
`--mock-settings` alone only substitutes settings templates and does not make
the subsequent acquisition a mock. Without `--dry-run`, this script can start
a real scan without a separate `--live` switch.

### Local production processing

The newest workflow launches `scripts/nmr/process_fid.py` using `sys.executable`,
so it inherits the same Python environment as the orchestrator. It passes the
actual acquired file, output directory, unique run name, dataset display name,
the 5.0-6.5 ppm processing region, and an explicit tracked simple-table window.
There is no network or LLM call in this processing path.

`process_fid.py` normally merges `configs/nmr/analysis.yaml` and
`configs/nmr/analysis.local.yaml`, then applies CLI overrides. An explicit
`--config` loads that file instead of automatically merging those two defaults.
Current shared production settings include stored phasing, metadata reference,
ALS baseline correction, 65536 zero-fill points, 0.03 Hz line broadening,
normalization none, and detection on the corrected real trace. The analysis
local YAML mainly supplies input/output paths for standalone processing;
explicit acquired-file and output CLI arguments override those paths in the
integrated workflow.

The YAML currently has process-level peak-QC manual thresholds of SNR 3,
prominence SNR 3, width 1-10 Hz, and positive area. Regional candidate detection
has its own prominence/spacing/width gates. After those checks, the workflow
also requires SNR >=5, prominence SNR >=3, positive-enough integrated area,
and a peak inside 5.8 +/-0.10 ppm. Editing only one set of thresholds may not
relax the other detection/QC gates.

`analyze_tracked_resonance()` reads `*peaks_simple.csv` and
`*peak_qc_log_window.csv`, requires one tracked row and QC evidence, and uses
the integrated area from production processing. A missing tracked peak is a
failed measurement, not a valid zero-area point for automated plateau stopping.
Magnitude spectra are still exported for diagnostic plotting; their area is
not the new workflow's stopping metric.

Other older analysis scripts retain `common.target_ppm: 6.1`; this does not
mean the integrated runner tracks 6.1. The integrated window is 5.70-5.90 ppm,
on the metadata-derived axis. Keep the intended analysis tool and axis clear.

Optional statistics/target-peak reports are separate processing outputs; the
orchestrator's stage decision uses its own plateau calculation, not every
statistical plateau result generated by the analysis suite.

### Authoritative scientific timing

The integrated runner explicitly reads JCAMP `LONG DATE` and parses
`%Y/%m/%d %H:%M:%S%z`. A missing/unparseable value fails the measurement.
`elapsed_hours` is relative to the first acquired spectrum's LONG DATE across
the run, not relative to initial catalyst addition or a filename timestamp.

Laptop wall time records command/journal events; monotonic time schedules
stages. Those clocks and the NMR acquisition clock serve different purposes.
The filename timestamp describes a laptop-generated name and must not replace
the actual metadata acquisition time. Repository instructions also forbid
silently treating nominal sequence HHMM tokens or file modification times as
metadata timing.

## 7. Exact full sampling cycle

The three-instrument runner deliberately enforces this exact nine-action YAML
shape. The two `operator` cycle entries are interpreted as automated needle
DOWN and UP transitions; their prompt text is used by the legacy manual runner.

| Step | Instrument/action | Needle context | Current setting | Estimated retained volume after step |
| --- | --- | --- | --- | --- |
| 0 | Verify needle at named UP | UP | +1 logical target | 0 mL |
| 1 | Chemyx withdraw, then STOP | UP, out of solution | 8 mL | 8 mL |
| 2 | Arduino move to named DOWN | Pump STOP confirmed | -1 logical target | 8 mL |
| 3 | Chemyx withdraw sample, then STOP | DOWN, in solution | 5 mL | 13 mL |
| 4 | Settle | Needle DOWN; pump idle | 300 s | 13 mL |
| 5 | NMR acquire, retrieve, process, time, analyze, plateau | Needle remains DOWN | 2 scans; 5.8 ppm target | 13 mL |
| 6 | Chemyx return infusion, then STOP | Still DOWN | 13 mL | 0 mL |
| 7 | Arduino move to named UP | Pump STOP confirmed | +1 logical target | 0 mL |
| 8 | Chemyx cleanup withdraw, then STOP | UP | 5 mL | 5 mL |
| 9 | Chemyx cleanup infuse, then STOP | UP | 5 mL | 0 mL |
| 10 | Verify idle, record CLEANUP_COMPLETE and cycle_completed | UP | No further move | 0 mL |

Every pump move verifies the expected software needle target first. Every
needle move checks pump motion/uncertainty and successful STOP evidence first.
Operations are sequential; the runner does not launch overlapping cycles.

A plateau found at step 5 cannot skip sample return or cleanup. The scheduler
receives the cycle's observation only after the cycle finishes that sequence.

The five pump waits total 442 seconds; adding 300 seconds settling gives
12 min 22 s before needle motion, serial overhead, NMR acquisition, processing,
and disk output. Therefore a 15-minute stage interval is relatively close to
the cycle's minimum duration. Extra scans or heavy processing can make cycles
late. This estimate comes from configuration, not a hardware timing measurement.

An interval describes cycle START times. The actual NMR acquisition occurs
after the first two pump moves, needle lowering, and settling. A cycle scheduled
at stage minute 60 does not acquire a spectrum exactly at minute 60.

## 8. Stage sequence, scheduling, and plateau

The full experiment first configures/opens services, obtains initial pump STOP
evidence, runs preflight, returns to software HOME, and raises to UP. Each
chemistry stage then requires its operator reagent/mixing confirmation before
its monitoring clock starts.

Current default stages after `prepare()` are:

| Stage | Operator action | Interval | First measurement | Ceiling | Available slots | Early plateau stop |
| --- | --- | --- | --- | --- | --- | --- |
| `initial_reaction` | Add catalyst/starting reagents, mix under N2 | 60 min | After 60 min | 26 h | 25 | Yes in newest runner |
| `first_diphenyl_silane` | Add diphenyl silane | 15 min | After 15 min | 2 h | 7 | Yes |
| `round_1_acetone` | Add acetone | 15 min | After 15 min | 2 h | 7 | Yes |
| `round_1_diphenyl_silane` | Add diphenyl silane | 15 min | After 15 min | 2 h | 7 | Yes |

`repeat_addition_rounds: 1` produces one acetone/silane pair after the first
addition. Increasing it repeats the configured list with unique round names.
Stage names must be unique. The first-addition section is optional in the stage
builder; repeated stages are list-driven.

The legacy initial-stage flag is false in YAML. The newest runner overrides it
with `three_instrument.initial_plateau_stopping_enabled: true`. Consequently
the same recipe intentionally produces different initial-stage early-stop
behavior in the two entry points.

Scheduling is start-to-start from a monotonic stage origin. With
`measure_immediately: false`, first offset is one interval; true starts at zero.
Slots must start strictly before `max_hours`: 15 min over 2 h yields slots
15, 30, 45, 60, 75, 90, 105, not 120. Late cycles run sequentially and log delay;
they can begin immediately after a previous overlong cycle. The code does not
silently overlap cycles or automatically respace them to completion-to-start.

`max_hours` prevents starting another cycle after the ceiling; it does not
interrupt a cycle already started. Completed plateau evidence counts even if
the cycle finishes beyond the ceiling. All slots can be exhausted before the
ceiling (for example, after the 105-minute slot), so the stage-limit decision
can occur before exactly two wall-clock hours have elapsed.

Omitting `max_measurements` lets duration/interval determine the count. An
explicit count is accepted only if its last scheduled slot is before the
ceiling, and the newest runner rejects a count smaller than the
duration-derived count. Use duration/interval to make a short run rather than
adding a conflicting measurement cap.

Plateau compares consecutive integrated areas within the same named stage:

```text
growth_percent = 100 * (current_area - previous_area) / abs(previous_area)
```

Current accepted interval band is -2% through +5%, inclusive. Three stable
intervals require four valid measurements. Each row must pass target/window,
SNR, prominence, finite-value, and area checks. A large decline is not plateau.
The first measurement of a new stage has no prior same-stage growth value.

With early stopping disabled, the stage still evaluates plateau but runs the
scheduled count. With stopping enabled, reaching the limit without plateau
asks CONTINUE, ADVANCE, or ABORT. CONTINUE starts another full stage-duration
scheduling block and retains same-stage measurement history. ADVANCE records
explicit advancement without plateau and proceeds to the next reagent prompt.
ABORT ends the run at rest. Noninteractive input defaults to abort.

The integrated Arduino session has a finite overall deadline of configured
total stage hours +2 h, at least 1 h (34 h for the default four stages). Long
operator delays or repeated CONTINUE extensions can exceed that session budget;
do not interpret CONTINUE as indefinitely extending every underlying timeout.

## 9. Exactly what can be adjusted without changing architecture

| Desired adjustment | Where/how | Supported today? |
| --- | --- | --- |
| Change Arduino COM port | `arduino.local.yaml::arduino.port` | Yes |
| Change pump COM port / NMR endpoint | Machine local YAML | Yes |
| Change needle height targets | `needle.up_position`, `down_position`, bounds/calibration | Yes, with physically verified logical geometry |
| Change needle speed/ramp | Arduino local `motion.*` | Yes within firmware/host limits |
| Change individual pump volumes | Existing five `workflow.cycle` pump entries | Yes, balanced cycle and capacity checks must pass |
| Change settling before NMR | Existing pause entry `seconds` | Yes |
| Change pump rate/diameter/capacity | Experiment `pump.*` | Yes; verify physical syringe/delivery |
| Change sampling cadence | Each stage `interval_minutes` | Yes |
| Sample immediately on stage entry | Each stage `measure_immediately` | Yes |
| Change maximum monitoring duration | Each stage `max_hours` | Yes |
| Change chemistry stage names/prompts | Stage fields | Yes; additions are still manual |
| Change/repeat later chemistry stages | `repeating_stages`, `repeat_addition_rounds` | Yes within existing stage schema |
| Change scans/gain/acquired window | Experiment `nmr.*` | Yes; keep auto-gain false/FID output; review wait budget |
| Change tracked resonance and stop thresholds | Experiment `nmr.target_ppm`, `analysis.*`; processing YAML as appropriate | Yes, keep processing region/QC consistent |
| Run only NMR or existing-file processing | Level 1 `--nmr-only`, `--process-only` | Yes, these branches open no serial ports |
| Move needle one step at a time | `needle_control.py` logical or manual jog actions | Yes as separate supervised commands |
| Pause for user approval before EVERY integrated cycle step | No current flag | Requires a small runner change |
| Arbitrarily reorder full cycle actions | `cycle_values()` enforces exact SOP | No, requires code changes and verification |
| Add new arbitrary cycle actions | Unknown schema fields/actions rejected | No, requires schema/runner changes |
| Different pump rate or NMR scans per stage/per move | Current settings are global for the run | No direct schema support |
| Change YAML while full experiment is running | Recipe loaded into memory at startup | No hot reload |
| Run instruments simultaneously | Current coordinator is sequential | No |
| Resume automatically after interruption | Recovery is inspection-oriented | No |
| Schedule instrument events at arbitrary clock times | Stages schedule recurring full cycles | No general event-timeline interface |

For ordinary changes, copy the complete recipe to
`configs/experiments/<name>.local.yaml`, edit only the desired supported values,
and pass `--workflow-config`. This preserves the existing architecture and
environment. Machine settings belong in machine YAML, not a new experiment
framework. Templates in `config_templates/` are copy sources; they are not
automatically read at runtime. Current runtime local YAML files are tracked in
Git, so a transfer/update can affect laptop-specific values if copied blindly.

Example: changing initial sampling from hourly to half-hourly means changing
`workflow.initial_stage.interval_minutes` from 60 to 30. With the same 26-hour
ceiling and no explicit cap, the available slots become 51. This does not change
the within-cycle Arduino/pump/NMR order.

Example: changing pre-NMR settling from 300 to 180 seconds means editing the
existing pause entry. It does not change scan settings or sampling intervals.

Example: requesting a 3 mL sample instead of 5 mL requires adjusting the sample
withdraw entry and the matching return volume from 13 to 11 mL if the first
withdraw remains 8 mL. A recipe with an unbalanced retained-volume model is
rejected. The liquid path still needs physical review.

## 10. Configuration precedence and reproducibility

The main 01/02 defaults are:

```text
--workflow-config configs/experiments/02_si6_automated_nmr.yaml
--machine-config  configs/machines/00_machine.local.yaml
--arduino-config  arduino/configs/arduino.local.yaml
```

Main scripts accept --mock or --live as mutually exclusive switches. With
neither, they validate configuration and exit without opening hardware.
The recipe cannot select live mode. Live prompts currently accept y/yes.

This default applies to the new 01/02 scripts. The legacy
`scripts/02_si6_automated_nmr.py` requires `--validate-only` or `--dry-run`
for offline use; otherwise it prompts to start its real attended workflow.
Do not transfer the newer CLI's default-mode assumptions to the older script.

Explicit `CHEMYX_*` and `NMR_*` process environment variables override values
assembled from machine/experiment YAML in `build_instrument_settings()`. For
example, CHEMYX_RATE, CHEMYX_DIAMETER, NMR_DEFAULT_SCANS, NMR_RECEIVER_GAIN,
NMR_TARGET_PPM, and NMR_RPC_MAX_WAIT_SECONDS can override apparent YAML choices.
Account for these when reproducing an instrument run; do not assume YAML is
always the final authority for resolved settings.

The integrated run saves the experiment's effective raw mapping and Arduino
mapping. It does not explicitly snapshot the complete resolved machine settings,
environment overrides, and merged processing YAML into those same two files.
The journal and processing provenance provide additional evidence, but do not
describe those snapshots as a complete standalone environment/configuration
archive without checking their actual contents.

## 11. Diagnostic selections and manual step control

Level 1 defaults to all. Its branches differ:

* `--needle-only`: Arduino connection, software HOME/UP, DOWN, UP again. The
  shared service initialization still opens/configures/stops the pump; this is
  not an Arduino-only serial isolation command.
* `--pump-only`: initialize shared services, home/raise needle, then a configured
  small withdrawal/infusion at UP (0.5 mL each by default). It requires Arduino
  context and opens both serial services.
* `--nmr-only`: NMR connection/acquisition/retrieval/production processing/
  tracked-peak analysis. The separate branch opens neither serial port.
* `--process-only --input-dx <file>`: copy an existing file into a new run,
  process and analyze it. No instrument is contacted. With --mock and no input,
  the included fixture supplies the file.
* `--all`: needle UP/DOWN/UP checks, 0.5/0.5 mL pump checks, then NMR acquisition
  and analysis. This does not execute the full 8/5/13/5/5 sampling SOP; Level 2
  does that.

The repository therefore supports independent stepwise checks and individual
needle commands, plus supported recipe adjustments. It does not expose an
interactive "next action" button/CLI for every step of the full automated SOP.
If requested later, a per-step confirmation hook can be added to the existing
runner rather than replacing the instruments or scheduler, but it is not part
of the current behavior.

The standalone staged Test 4 is also a different workflow. Test 4A is
connection/preflight-oriented. Test 4B lowers the needle, runs a selected pump
action, acquires NMR, raises the needle, then runs the matched pump return. Its
current local config selects cycle indices 3 and 9 (5 mL withdraw/infuse), and
has a 120-second hard budget. At 5 mL/min the pump moves alone need 120 seconds;
settling, needle movement, and NMR add more. Its live duration check therefore
cannot fit those current settings. This does not imply that the multi-hour
Level 2 runner has the same 120-second total experiment limit.

## 12. Failure behavior and recovery

If acquisition, retrieval, processing, LONG DATE, tracked-peak QC, or plateau
evaluation fails, the measurement is not admitted to the successful time series.
When pump STOP, needle DOWN, retained-volume estimate, and persistence are
provable, the runner returns the sample while DOWN, raises UP, performs the
normal cleanup exchange, and stops the experiment for review (analysis
inconclusive, exit 7). It does not quietly continue to the next chemistry stage.

If pump/needle state is uncertain, cleanup fails, Ctrl+C interrupts a cycle,
or journal persistence fails, the runner attempts pump STOP and needle STOP
and leaves the rig for manual inspection instead of commanding further
automatic recovery motion. Reagent-prompt refusal occurs between stages and
ends as operator-aborted (exit 3). Unhandled entry-point exceptions return 1.

`operation_journal.jsonl` is the event history; `run_state.json` is a derived
snapshot. Inspection is available through the legacy script:

```powershell
.venv\Scripts\python.exe -B scripts\02_si6_automated_nmr.py --inspect-run <run-folder>
```

`--rebuild-state` rebuilds the state snapshot from the journal; it does not
resume or physically reconcile the experiment.

Two implementation details need explicit attention in a future review:

1. `check_previous_run_review()` still contains a blocking review check and
   tests exercise it directly. The current production `prepare()` uses advisory
   demo behavior and `open_services()` does not invoke that blocking helper.
   Therefore an `--acknowledge-review` flag exists and is recorded, but current
   main entry-point behavior does not enforce the older documented restart gate.
2. Measurement-failure cleanup precheck compares retained volume to the two
   withdrawal volumes alone. With the default initial retained volume of zero
   this is consistent; changing `initial_retained_volume_ml` to nonzero can
   make that recovery precheck refuse cleanup even if the new estimate is
   otherwise expected. Review that path before using a nonzero initial volume.

NMR cancellation methods exist in the client, but the integrated error paths
do not explicitly call them. A timeout or stopped Python process does not prove
an acquisition was cancelled on the instrument. Inspect NMR status separately
after a failed acquisition.

## 13. Output data and scientific provenance

Live experiments use `results/runs/si6/<stamp>_si6_live`; integrated diagnostics
include selection/mode in their names. Mocks use the `_mock` sibling root.
Processing-only runs have their own identity. Name collisions receive suffixes.

The path helper defines raw_nmr/, plots/, time_series.csv, spectra_long.csv,
operations.csv, manifest.json, operation_journal.jsonl, and run_state.json.
The integrated path additionally writes config_snapshot.json,
arduino_config_snapshot.json, and processed_nmr/ subruns. Some path fields are
shared with the legacy workflow: a declared operations.csv or plots/ directory
does not guarantee the newest runner fills every legacy summary artifact.
The newest runner does not call the legacy `update_summary_plots()` helper.

Raw JCAMP files are retained. Per-acquisition processing provides peak/simple
tables, window QC logs, regional plots, and configured statistics/target-peak
outputs. Successful integrated rows go to time_series.csv; spectral diagnostic
points go to spectra_long.csv. Scheduling and timestamp-source evidence is
also journaled; some fields present in in-memory rows are not in the shared
CSV column list. For example, the new LONG DATE source is explicitly recorded
in `nmr_acquisition_time` journal events.

Every saved experimental figure must visibly identify its dataset. Single
panels use dataset + descriptive title; multipanel figures use a dataset
suptitle. Use `chemyx_lab.analysis.plot_titles` and
`format_dataset_plot_title` rather than hard-coding a reusable run name.
All saved formats and manifests must agree. The integrated processing call
passes the run-folder identity explicitly, so the shared analysis YAML's
configured 06-09-26 display name must not overwrite a different run's identity.
New plotting paths require title-convention tests.

## 14. Offline environment and conservative update procedure

The offline installer targets Windows 64-bit CPython 3.11 and creates a
repository-local `.venv`. Run it with `offline/install_offline.ps1`; it installs
the 27 pinned packages from `offline/requirements-lock.txt` via `--no-index`
and `--find-links offline/wheelhouse`. Core dependencies are pyserial, PyYAML,
NumPy, SciPy, matplotlib, and nmrglue; the bundle also supports the Qt analysis
GUIs, pandas, and pytest. `pyproject.toml` has looser dependency ranges; the
offline lock is the reproducible offline installation list.

`offline/build_offline_bundle.ps1` is an online preparation step, not an
offline run-time requirement. It downloads wheels and the Python installer,
verifies installability without network, and writes a manifest.
`offline/arduino_toolchain.ps1` provides a portable CLI/UNO R4 core/toolchain
for offline compilation/upload. The sketch uses no third-party Arduino library.
`offline/serial_drivers.ps1` exports/installs relevant Windows serial drivers.

This checkout contains 27 wheel files, BUNDLE_MANIFEST.txt, the Arduino CLI,
and ARDUINO_MANIFEST.txt. File presence was checked; all bundle hashes and the
other laptop's installed packages were not independently verified in this review.
The work laptop's current environment cannot be inferred solely from this
home-laptop checkout. No environment was rebuilt or packages installed here.

For the work laptop, keep using its known working environment. The documented
offline default is `.venv\Scripts\python.exe`, which avoids ambiguous shell
activation. This review used the existing home `ai` interpreter only for offline
validation/tests; it did not establish that `ai` or any named conda environment
exists on the work laptop. An environment name is separate from environment
variables that override instrument settings.

To transfer a normal code/config update without drastic changes:

1. Preserve the work laptop's verified machine/Arduino local YAML, processing
   overrides, and experiment variants before overwriting tracked files.
2. Copy the intended source/config changes by USB or the existing transfer
   procedure. No Git internet access is needed on the offline laptop.
3. Keep the installed environment if dependencies did not change. Do not copy
   a different laptop's `.venv` or needle_state.json as a substitute for setup.
4. Run validation with the actual existing interpreter and explicit config paths.
5. Run focused mocks/offline tests appropriate to the changed workflow.
6. Reconcile the physical reference and verify endpoints before an attended
   instrument test. A fresh software position assumption is not physical HOME.
7. Re-upload firmware only if the intended sketch changed or loaded identity
   differs. Do not rebuild the Arduino stack for a YAML sampling change.

If a genuinely new dependency becomes necessary later, prepare its compatible
wheels and lock update on a connected computer, then transfer and verify them.
Nothing in the reviewed workflow requires adding a dependency for ordinary
configuration adjustments.

## 15. Documentation drift and concrete review priorities

| Documented/assumed claim | Executable checkout |
| --- | --- |
| Main 01/02 default to uncommissioned arduino.example.yaml | Both default to populated arduino.local.yaml |
| Missing staged commissioning prevents live use | `require_live()` only prints notes; main prepare does not enforce those records |
| Unknown saved position requires explicit operator HOME | TrackedNeedle constructor can assume HOME and save a valid demo estimate |
| A failed previous live run blocks restart until acknowledged | Blocking helper remains, but main call path uses advisory behavior |
| Live requires exact typed confirmation | Current main/demo prompts accept y/yes |
| README firmware reference is 1.2.0 | Active sketch/current expected identity is 1.2.1 |
| Arduino configs use automatic fingerprint discovery | Current local config fixes COM3 with an empty fingerprint; templates differ |
| Short commissioning recipe is a complete single-cycle experiment | It shortens the initial stage, retains later stages, and newest initial plateau stopping remains true |
| Staged Test 4 current recipe fits its brief deadline | Two 5 mL moves at 5 mL/min alone consume its whole 120-second budget |
| Installer fallback message says upload 1.1.0 | Active current firmware is 1.2.1 |

The short attended recipe's initial stage is immediate, 5-minute interval,
0.05-hour ceiling, giving one possible initial slot. The cycle is longer than
that ceiling, but an already-started cycle can finish. It cannot establish three
stable intervals from one measurement. With newest initial plateau stopping
enabled, expect a stage-limit decision after that cycle; ADVANCE enters the
still-configured later stages. Do not describe the entire recipe as automatically
one-and-done.

A conservative next change, if the user requests it, is to make documentation
accurately describe accepted demo behavior and add targeted entry-point tests
for any intended guard policy. Do not treat this review as authorization to
change HOME assumptions, commissioning policy, firmware, wiring, or dependency
versions. A passing unit test of an unused blocking helper is not proof that
the production entry point enforces it.

## 16. Local verification performed for this review

Interpreter: `C:\Users\iyer95\miniconda3\envs\ai\python.exe`, CPython
3.11.15. Core imports succeeded. All three commands passed without hardware:

```text
scripts/01_three_instrument_system_test.py             [default validation]
scripts/02_si6_experiment.py                           [default validation]
scripts/02_si6_automated_nmr.py --validate-only
```

Focused tests: **111 passed**, 26 nmrglue/NumPy deprecation warnings, in
257.81 seconds. Command (temporary output directory used only for this review):

```text
python -B -m pytest -q -p no:cacheprovider
  tests/test_three_instrument_si6.py tests/test_chemyx.py
  tests/test_nmr_instrument.py arduino/tests/test_software_needle_state.py
  arduino/tests/test_manual_jog.py --basetemp .review_tmp_20261001
```

These suites cover the integrated Si6 runner, Chemyx wrapper, NMR wrapper,
software needle state, and manual jog. Passing the helper-level restart tests
does not establish that the main entry point invokes the blocking helper.
Hardware behavior, offline installation on the separate work laptop, and
full-duration experiments were not exercised. The review's temporary test
directory was removed after the run.

Useful commands for future no-hardware checks, using the documented offline
interpreter on a work laptop where that .venv already exists:

```powershell
# Execute from the repository root. These three only validate configuration.
.venv\Scripts\python.exe -B scripts\01_three_instrument_system_test.py
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py
.venv\Scripts\python.exe -B scripts\02_si6_automated_nmr.py --validate-only

# These execute mocks, write results, and contact no instruments.
.venv\Scripts\python.exe -B scripts\01_three_instrument_system_test.py --mock --all
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --mock

# Validate a supported edited recipe without executing it.
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --workflow-config configs\experiments\your_run.local.yaml
```

Level 2 mocks default to four accelerated cycles per stage, use fake serial
transports and the included real resonance fixture, process the first spectrum
with production process_fid, and reuse processing tables for the subsequent
byte-identical fixture copies. They exercise the software path but do not
predict real reaction kinetics, real processing time for changing spectra, or
the physical instrument's timing.

## 17. Primary source map for further review

| Topic | Source/function |
| --- | --- |
| New CLI defaults/modes/selections | `scripts/01_three_instrument_system_test.py`, `scripts/02_si6_experiment.py` |
| Exact accepted cycle shape | `three_instrument_si6.py::cycle_values` |
| Within-cycle coordination/cleanup | `three_instrument_si6.py::Services`, `sample_cycle` |
| Demo preflight and live-service initialization | `three_instrument_si6.py::prepare`, `open_services` |
| Stage decisions | `three_instrument_si6.py::run_experiment`, `operator_stage_decision` |
| Recipe/schema/capacity | `si6_automated_nmr.py::load_si6_config`, `validate_syringe_capacity`, `build_stages` |
| Cadence/ceiling | `si6_automated_nmr.py::run_monitoring_stage`, `duration_measurement_slots` |
| Timed Chemyx moves/STOP interpretation | `si6_automated_nmr.py::run_safe_metered_move`, `attempt_emergency_stop` |
| NMR acquisition/retrieval | `instrument_operations.py::run_nmr_acquisition`, `instruments/nmr.py` |
| New production measurement | `three_instrument_si6.py::analyze_tracked_resonance` |
| Subprocess processing arguments | `si6_automated_nmr.py::run_process_fid_postprocessing` |
| Processing configuration resolution | `scripts/nmr/process_fid.py::_resolved_config_mapping` |
| Software needle estimate/HOME assumption | `arduino/python/needle_state.py::TrackedNeedle` |
| Serial protocol/runtime configuration | `arduino/python/controller.py`, `protocol.py`, active sketch |
| Advisory commissioning helper | `arduino/python/config.py::require_live` |
| Separate brief integration test | `arduino/scripts/test_04_integrated_system.py`, `arduino/python/workflows.py::run_test_04b` |
| Local install and transfer | `offline/install_offline.ps1`, `requirements-lock.txt`, `docs/OFFLINE_SETUP.md` |
| Intent/documentation to reconcile | `docs/THREE_INSTRUMENT_ARCHITECTURE.md`, `docs/LIVE_COMMISSIONING_CHECKLIST.md`, `config_templates/README.md` |

When asking another LLM to review this system, provide this document plus the
primary sources above and the actual work-laptop configuration/environment
versions. Ask it to distinguish documented intent, current executable behavior,
mock evidence, and physically validated behavior, and to propose only minimal
changes compatible with the existing offline architecture.
