# Live commissioning checklist: three-instrument Si6 workflow

Status on 2026-09-22: **software, mock, and offline-bundle verified on the home
laptop; not live-hardware verified.** Firmware 1.2.0 requires a fresh compile with the bundled
toolchain but has not been uploaded. The integrated workflow has never
contacted the Arduino, Chemyx, or NMR.

Work top to bottom. Each stage must pass before the next begins. Record the
date, operator, result, and run folder for every step.

## Ground rules

- Attended only. The operator keeps the 24 V driver switch and the pump STOP
  within reach for every live step.
- Run every command from the repository root with `.venv\Scripts\python.exe`.
  Result lookups (`runs\arduino`, `results\runs\...`) are relative to the
  current directory.
- Copy `arduino\configs\arduino.example.yaml` to
  `arduino\configs\arduino.local.yaml` and use that one file for Tests 1-3 and
  every live `01`/`02` run (`--config` / `--arduino-config`). Both scripts
  default to the uncommissioned example file. The live preflight matches the
  Test 2 and Test 3 result records against this file's hardware fingerprint.
- Never use the archived bridge scripts
  (`arduino\dm542s_hello_world\04_needle_up.py`, `05_needle_down.py`) or their
  configs on this rig. They use a different pinout and protocol.
- Stop at the first unexpected result. After any stop, complete
  [Reconciliation](#reconciliation-after-any-stop) before anything moves again.
  A live run refuses to start while the previous live run needs review; after
  reconciling, pass `--acknowledge-review <run id>`. There is no resume.

## 0. Offline transfer package

| Item | Status 2026-09-22 | How it was built or verified |
|---|---|---|
| Python wheels | **Ready** | `offline\build_offline_bundle.ps1`: 27 pinned wheels, offline `--no-index` install proven; `offline\BUNDLE_MANIFEST.txt` |
| Python installer | **Ready** | `offline\installers\python-3.11.9-amd64.exe`, PSF signature checked |
| Arduino CLI and UNO R4 core | **Ready** | `offline\arduino_toolchain.ps1 -Build`: arduino-cli 1.5.1 (SHA-256 checked), `arduino:renesas_uno` 1.6.0, gcc 7-2017q4, dfu-util; firmware compiled with the network blocked. The repository path must be 50 characters or fewer (for example `C:\code\chemyx_pump`); the bundled compiler fails in deeper folders |
| Firmware | **Compiled, not uploaded** | 47,828 bytes flash (18 %), 4,608 bytes RAM; no third-party Arduino library |
| Chemyx USB-serial driver | **Missing** | Must be exported on the laptop where the pump already works: `offline\serial_drivers.ps1 -Export` (Administrator) |
| Arduino drivers | Built into Windows 10/11 for the COM port; bootloader driver ships in the core (`post_install.bat`) | Install only if an upload cannot find the DFU device |
| Source tree | **Not committed** | Copy with robocopy (docs/OFFLINE_SETUP.md), or commit the untracked files first |

Offline acceptance on the isolated laptop (no hardware):

```powershell
powershell -ExecutionPolicy Bypass -File offline\install_offline.ps1 -RunTests
```

Expect every step to pass: the file check, workflow validation, the offline
firmware compile, both test suites with zero failures, and the Level 1 and
Level 2 mocks. Offline readiness is claimed only after this passes on the
transferred copy.

## A. Arduino and needle

The active firmware is 1.2.0: **D3 STEP, D4 DIR only**. Keep the already-working
wiring and driver settings. There is no ENABLE connection or physical upper or
lower switch. Python HOME=0 and positions are software estimates, not encoder
or physical homing measurements. See `arduino/docs/FIRMWARE.md`.

- [ ] **A1 Wiring review, all power off.** Confirm the existing D3/D4 and
  signal-return connections, 24 V power disconnect, and safe travel space.
  Record `signal_interface.wiring_reviewed: true`; do not add wires.
- [ ] **A2 Compile offline:**
  `powershell -ExecutionPolicy Bypass -File offline\arduino_toolchain.ps1 -Compile`
- [ ] **A3 Upload with 24 V off and the actual Arduino COM port:**
  `powershell -ExecutionPolicy Bypass -File offline\arduino_toolchain.ps1 -Upload -Port COMx`.
- [ ] **A4 Test 1 connection:** run `arduino/scripts/test_01_arduino_connection.py
  --config arduino/configs/arduino.local.yaml --live`. READY must report 1.2.0.
- [ ] **A5 Test 2 decoupled motor:** run its preflight, then attended live
  forward/reverse test. Verify actual shaft direction and the physical 24 V
  disconnect. Firmware ENABLE/DISABLE only arm software; they do not switch
  the unwired driver ENA input.
- [ ] **A6 Calibrate logical movement:** physically verify the steps for one
  safe needle increment and whether legacy positive/forward is UP. Set
  `needle.steps_per_unit` and `needle.up_step_sign`; review min/max and named
  UP/DOWN positions against real clearance. Example bounds are not safety
  evidence.
- [ ] **A7 Establish software HOME:** physically inspect and place the needle
  at the chosen reference, then run `needle_control.py confirm-home --live`.
  Inspect any existing saved state after a restart; if the axis could have
  moved independently, reconfirm HOME.
- [ ] **A8 Test 3 axis:** run its no-motion `--preflight-only`, then an
  attended `--live` cycle. It returns to software HOME and visits configured
  UP/DOWN twice. Inspect travel and immediately stop on unexpected direction.
- [ ] **A9 Validate real flask UP/DOWN clearances:** the inlet must remain
  submerged during liquid withdrawal and clear of stir bar/bottom. At UP,
  confirm intended headspace/tip position. Record the reviewed settings and
  operator/date in the local YAML or run notes. Reinspect after any change.

This setup is supervised-only. Software bounds and STOP do not replace physical
limit switches or the existing driver-power disconnect for unattended use.

## B. Chemyx pump (independent)

- [ ] **B1 Port.** `scripts\diagnostics\01_list_serial_ports.py`; set
  `chemyx.serial_port` in `configs\machines\00_machine.local.yaml` (never the
  Intel AMT port). Baud must match `chemyx.baud_rate` (115200). If the port does
  not appear, install the driver exported in section 0.
- [ ] **B2 Syringe.** Identify the installed syringe and confirm its inner
  diameter and capacity against `pump.syringe_diameter_mm: 20.0` and
  `pump.syringe_capacity_ml: 20.0`. 20.0 mm is the value the rig ran with on
  2026-08-10 (`03_081626_phsi4.yaml`); the production config previously said
  28.6 mm. Diameter sets every delivered volume; capacity drives the fail-closed
  capacity check. The cycle peaks at 13 mL retained, plus a 1 mL margin.
- [ ] **B3 Direction** with solvent into a waste vessel:
  `scripts\diagnostics\02_verify_chemyx_movement.py --channel 1 --diameter 20.0 --rate 1 --volume 0.5`.
  Confirm that *withdraw* draws liquid in through the needle.
- [ ] **B4 Volume accuracy at the workflow rate** (5 mL/min). Weigh 5.0 mL and
  13.0 mL deliveries. Moves are time-based: the workflow waits
  `volume/rate + pump_extra_seconds` (2 s) and then sends STOP, and the pump
  reports no delivered volume. A slow or stalled move is truncated without an
  error, so confirm each volume completes inside that window under the real
  back-pressure, or increase `workflow.pump_extra_seconds`.
- [ ] **B5 STOP reply.** Confirm that the pump answers `stop` with a non-empty
  reply; an empty reply is treated as unconfirmed and halts the workflow. (The
  2026-08-10 run journaled six confirmed STOP replies.)

## C. NMR (independent)

- [ ] **C1** `scripts\diagnostics\03_check_nmr_connection.py` reports connected
  and RPC enabled (remote control on; `RPC_API_ENABLED = True`).
- [ ] **C2** Acquire the reaction solvent with the workflow settings:
  `scripts\diagnostics\04_run_nmr_1d_acquisition.py --save-dx results\raw\nmr\generated\commissioning_solvent.dx`.
- [ ] **C3** Confirm the file contains `##LONG DATE=`. Elapsed time uses only this
  header and fails closed without it. Note the NMR clock offset from the laptop
  (about 8-9 min on 2026-08-10).
- [ ] **C4** Process a real product-containing spectrum with the production
  pipeline (contacts no instrument):
  `scripts\01_three_instrument_system_test.py --live --process-only --input-dx <file>`.
  Expect `[PASS] NMR analysis: tracked peak 5.7x-5.9x ppm`. A solvent-only file
  correctly fails at *NMR analysis*: it has no QC-passing peak in 5.70-5.90 ppm.
- [ ] **C5 Tracked resonance on a real Si6 spectrum.** The window 5.8 +/- 0.10
  ppm comes from operator-confirmed PhSi2 and PhSi4 spectra (5.785-5.847 ppm);
  no spectrum labelled Si6 is in the repository. Confirm on the first real Si6
  reaction spectrum that the product resonance falls in the window and passes
  QC with 2 scans (SNR at least `analysis.min_peak_snr`, 5.0).

## D. Level 1 individual tests (`01 --live`)

Common arguments:
`--machine-config configs\machines\00_machine.local.yaml --arduino-config arduino\configs\arduino.local.yaml`.
Each live mode asks you to type `RUN THREE INSTRUMENT TEST`, except
`--process-only`, which asks nothing. Results go to
`results\runs\si6\<stamp>_diagnostic_<test>_live`. Optional first: Test 4A
connection-only preflight (`arduino\docs\TEST_04_GUIDE.md`).

| Mode | Instruments required | Physical action | Expected PASS lines |
|---|---|---|---|
| `--needle-only` | Arduino and Chemyx (needle motion is interlocked to a confirmed Chemyx STOP) | return to software HOME, UP, DOWN, UP | Arduino connection, software HOME return, Needle UP, Needle DOWN, Needle UP again |
| `--pump-only` | Arduino and Chemyx | return to software HOME and UP, then withdraw 0.5 mL and infuse 0.5 mL at UP (`three_instrument.test_*_ml`) | Chemyx connection, software HOME return, Chemyx withdraw, Chemyx infuse |
| `--nmr-only` | NMR only (no serial port opened) | one acquisition of the current flow-cell contents | NMR connection, NMR acquisition, NMR data retrieval, NMR processing, NMR analysis |
| `--process-only --input-dx <file>` | none | none | NMR processing, NMR analysis |

The analysis step needs a flow-cell sample with a QC-passing peak in
5.70-5.90 ppm. A failed live diagnostic marks the run for review, so the next
live run needs `--acknowledge-review <run id>`.

## E. Level 1 full integration (`01 --live --all`)

Same arguments, `--all`. Expect 13 `[PASS]` lines and
`THREE-INSTRUMENT SYSTEM TEST: PASS`. A failure names the first failing
subsystem. Because `01` and `02` share the production interfaces, a failure
here is an instrument, wiring, or configuration problem, not a scheduling
problem.

## F. Short attended Level 2 runs (`02 --live`)

Create `configs\experiments\02_si6_short_attended.local.yaml` (gitignored) from
`02_si6_automated_nmr.yaml`, changing only:

- `workflow.initial_stage`: `interval_minutes: 5`, `measure_immediately: true`,
  `max_hours: 0.05` (exactly one measurement), and an `operator_prompt` naming
  the test.
- `output.run_root_dir: results/runs/si6_commissioning`, which keeps
  commissioning runs out of the production folder.

```powershell
.venv\Scripts\python.exe scripts\02_si6_experiment.py --live --workflow-config configs\experiments\02_si6_short_attended.local.yaml --machine-config configs\machines\00_machine.local.yaml --arduino-config arduino\configs\arduino.local.yaml
```

Type `RUN SI6 THREE INSTRUMENTS`, then `yes` at the checkpoint.

- [ ] **F1 Solvent only in the flask.** The spectrum has no product, so the
  measurement fails. Expect the workflow to finish the whole physical cycle
  anyway (13 mL return while DOWN, UP, 5/5 mL exchange), then
  `Si6 EXPERIMENT: analysis_inconclusive` (exit 7). This proves the fluid path
  and the automatic recovery. `--inspect-run` must show
  `terminal_noncompletion`, 0 mL retained, operator review required.
- [ ] **F2 A sample that contains the product resonance**, after reconciling F1
  and adding `--acknowledge-review <F1 run id>`. Expect one valid measurement,
  then the stage-limit prompt (no plateau after one measurement): type `ABORT`.
  Expect `operator_aborted` (exit 3) and `terminal_noncompletion` with 0 mL.
- [ ] **F3 Repeatability:** `max_hours` covering three slots, with
  `interval_minutes` above the measured cycle time; answer `ABORT` at the
  prompt. Check that cycles never overlap and that `scheduling_delay_seconds`
  stays small.

Watch each step of the implemented order:

| Step | Needle | Observe |
|---|---|---|
| verify UP | UP | tip position and clearance |
| withdraw 8 mL | UP | what enters the line (headspace gas vs air); syringe travel |
| move DOWN | DOWN | immersion depth, stirring clearance |
| withdraw 5 mL | DOWN | liquid front position; bubbles; flask level drop; tip stays submerged |
| settle 300 s, NMR | DOWN | NMR fill (sample centered in the coil, no bubbles); acquisition time |
| infuse 13 mL | DOWN | sample returns to the flask; gas bubbling or splashing |
| move UP | UP | no drips or carry-out |
| withdraw 5 mL, infuse 5 mL | UP | needle and line clearing; what remains in the NMR cell (carryover) |
| cycle complete | UP | syringe back at its start position; pump idle; tubing dead volume |

Measure the real cycle duration from the journal (`cycle_status` STARTED to
`cycle_completed`). Software passing is not enough: the fluidics must be
confirmed by eye.

## G. Chemistry-specific review (after F succeeds)

All values live in YAML; no code changes are needed.

| Setting | Where | Now | Note |
|---|---|---|---|
| Initial interval | `initial_stage.interval_minutes` | 60 | Set 30 for 30-minute sampling; nothing else changes (51 measurements) |
| Stage durations | `max_hours` | 26 h initial, 2 h later | The duration governs: 25 hourly and 7 fifteen-minute measurements |
| Initial plateau stop | `three_instrument.initial_plateau_stopping_enabled` | true | Overrides `initial_stage.plateau_stopping_enabled: false`, which only the legacy script uses |
| Later intervals | `first_addition_stage`, `repeating_stages` | 15 min | Estimated cycle is about 14 min (below), leaving little slack; late cycles are logged and never overlap |
| Stage end without plateau | built in | - | Operator chooses CONTINUE, ADVANCE, or ABORT; never automatic |
| First measurement | `measure_immediately` | false (all stages) | A spectrum without product stops the run after a safe cleanup |
| Settle | `workflow.cycle[3].seconds` | 300 | |
| Scans | `nmr.scans` | 2 | 2026-08-10 used 8; raise machine `nmr.max_wait_seconds` (300) if acquisitions lengthen |
| Tracked window | `nmr.target_ppm`, `analysis.detection_window_ppm` | 5.8 / 0.10 | See C5 |
| Measurement QC | `analysis.min_peak_snr` | 5.0 | Separates all local spectra, but the loudest non-product feature scored 4.8; confirm with 2-scan data |
| Plateau rule | `analysis.plateau_*` | +5 % / -2 %, 3 intervals | Needs four valid measurements in the same stage |
| Pump rate and volumes | `pump.rate_ml_min`, `workflow.cycle` | 5 mL/min; 8/5/13/5/5 mL | Cycle must return to 0 mL net (enforced) |
| Repeat rounds | `workflow.repeat_addition_rounds` | 1 | |
| Sample DOWN | `arduino.local.yaml` `needle.down_position` | -1 example only | A9 |

Cycle-time estimate at the current settings: withdraw 8 mL (98 s), withdraw
5 mL (62 s), settle (300 s), infuse 13 mL (158 s), withdraw 5 mL (62 s), and
infuse 5 mL (62 s) total 742 s, or 12.4 min. The NMR acquisition (not yet
measured live), `process_fid` (about 20-40 s in mock runs), and two needle moves
come on top, so expect roughly 14-15 min. F replaces this with a measured value.

## H. Long Si6 run gate

- [ ] A-G signed off; C5 confirmed on a real Si6 spectrum.
- [ ] A product-containing F-style cycle passed analysis at the configured window.
- [ ] Laptop on AC power; sleep, hibernate, USB selective suspend, and automatic
  update restarts disabled for the run. Enough free disk space (each full
  `process_fid` pass writes about 6 MB).
- [ ] Operator available at every reagent checkpoint and stage-limit prompt.
  Both need an interactive terminal.
- [ ] First long run attended through several cycles; never unattended until a
  complete long run has been reviewed.

## Reconciliation after any stop

1. Move nothing. Make the rig safe per the A7/A1 records: switch off 24 V only
   if the axis is verified safe when disabled.
2. Inspect the journal:
   `scripts\02_si6_automated_nmr.py --inspect-run <run folder>`. Note the
   classification, the last durable operation, the estimated retained volume,
   the operator-review flag, and every diagnostic line.
   `manual_inspection_required` or `physical_state_uncertain` means the rig is
   not at its cycle rest state.
3. Reconcile each item physically and record it: needle position (tip in the
   liquid? height?), syringe plunger versus the estimated retained volume, pump
   state, tubing contents, NMR cell contents, reaction flask (volume lost, N2
   blanket), and the journal's failed step.
4. Return the rig to rest by an agreed manual procedure. After an interrupted
   needle move, never command UP blindly; inspect first. If the syringe still
   holds volume, empty it as agreed, or set `pump.initial_retained_volume_ml` to
   the true value before the next run, because the capacity check trusts it.
5. Start a new run with `--acknowledge-review <run id>`. The workflow never
   resumes a stopped run.

## Known limits of the software checks

- Needle position is a persisted, commanded software estimate. There are no
  physical limit inputs or encoder; missed steps and manual drift are invisible.
- Pump "idle" is the Chemyx STOP acknowledgement after a timed move. There is
  no delivered-volume or status feedback.
- The plateau decision uses only the tracked resonance in 5.70-5.90 ppm.
- Automatic recovery after a failed measurement runs only when the pump STOP is
  confirmed, the needle is verified at DOWN, the retained volume matches, and
  the journal is healthy. An operator Ctrl+C never triggers automatic motion.
