# Three-instrument automation architecture

## Scope

Two entry points share `chemyx_lab.workflows.three_instrument_si6`:

- `scripts/01_three_instrument_system_test.py` (Level 1) tests individual
  subsystems or the full chain: `--needle-only`, `--pump-only`, `--nmr-only`,
  `--process-only`, `--all`.
- `scripts/02_si6_experiment.py` (Level 2) runs the configured multi-stage Si6
  sequence.

Both default to validation only. `--mock` uses the simulated Arduino and Chemyx
transports and a real JCAMP spectrum
(`chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx`) that
contains the tracked resonance, so mocks exercise the configured target window.
Level 1 mock runs production `process_fid` for its measurement. Level 2 mock
runs it for the first cycle and reuses those tables for the byte-identical
fixture copies that follow. `--live` requires matching staged Arduino
commissioning records and an exact interactive confirmation. Neither script has
been validated on physical instruments. Do not use them for an unattended run.

## Production modules (shared by 01 and 02)

| Function | Module |
|---|---|
| Needle | `arduino.python.needle_state.TrackedNeedle` over the serial controller; firmware `arduino/firmware/needle_controller/needle_controller.ino` 1.2.1 |
| Chemyx pump | `chemyx_lab.instruments.chemyx.Pump`; timed, STOP-confirmed moves in `si6_automated_nmr.run_safe_metered_move` |
| NMR acquisition and retrieval | `chemyx_lab.workflows.instrument_operations.run_nmr_acquisition` (NMReady iFlow RPC) |
| NMR processing | `scripts/nmr/process_fid.py` via `si6_automated_nmr.run_process_fid_postprocessing`, restricted to the tracked window |
| Tracked-resonance measurement | `three_instrument_si6.analyze_tracked_resonance`: reads process_fid's QC'd `*peaks_simple.csv` and `*peak_qc_log_window.csv` |
| Plateau | `si6_automated_nmr.plateau_reached` |
| Scheduling | `si6_automated_nmr.run_monitoring_stage` and `run_stage_sequence` |
| Journaling | `chemyx_lab.runtime_journal.RunRecorder` and `runtime_state.replay_journal` |
| Recovery inspection | `chemyx_lab.recovery.inspect_run` (`scripts/02_si6_automated_nmr.py --inspect-run <run>`) |

## Cycle order

One cycle is strictly sequential:

```text
verify needle UP
  -> withdraw 8 mL with needle UP; confirmed pump STOP
  -> move needle DOWN (pump STOP confirmed first); verify commanded DOWN
  -> withdraw 5 mL with needle DOWN; confirmed pump STOP
  -> settle 300 s
  -> acquire NMR -> retrieve .dx -> process_fid -> LONG DATE -> analyze -> plateau
  -> infuse 13 mL with needle still DOWN; confirmed pump STOP
  -> move needle UP; verify commanded UP
  -> withdraw 5 mL with needle UP; confirmed pump STOP
  -> infuse 5 mL with needle UP; confirmed pump STOP
  -> verify pump idle -> CLEANUP_COMPLETE -> cycle_completed
```

A plateau result never skips cleanup. Every pump move first verifies the
needle is at the position the SOP requires, and every needle move first
verifies a confirmed Chemyx STOP; the Arduino motion guard refuses motion while
the pump is moving or uncertain.

## NMR measurement

The tracked resonance is 5.8 +/- 0.10 ppm (5.70-5.90 ppm), the window in
`configs/nmr/analysis.yaml` `target_peak`. Every operator-confirmed spectrum
places it at 5.785-5.847 ppm on the metadata-derived ppm axis that production
uses; the earlier 6.1 ppm is the same resonance on a vendor display with a
shifted reference (`results/README.md`).

The measurement is production `process_fid`'s result for that window: the
phase-corrected, ALS-baselined real spectrum with the peak-QC gates calibrated
against confirmed spectra, plus the workflow's `analysis.min_peak_snr`,
`min_prominence_snr`, and `min_peak_area`. The magnitude-spectrum detector
(`analyze_dx_peak`) is no longer used for decisions: on the 21 local real
spectra it found the resonance in 5 of 15 spectra that contain it, while this
metric found 14 of 15 with no false positives. Elapsed time uses only the JCAMP
`LONG DATE` header and fails closed without it. A failed measurement never
enters the time series or the plateau window.

## Failure and recovery

| Failure | Physical state | Behaviour | Journal and inspection |
|---|---|---|---|
| NMR acquisition, retrieval, `process_fid`, `LONG DATE`, analysis (including no QC-passing peak), or plateau evaluation | Pump STOP confirmed, needle verified DOWN, retained volume as expected | Same cleanup as a normal cycle (13 mL return while DOWN, UP, 5/5 mL exchange, idle check), then the experiment stops (exit 7) | `measurement_failed`, `recovery_cleanup` started/completed, `CLEANUP_COMPLETE`, no `cycle_completed`; terminal `operator_review_required`; next live run needs `--acknowledge-review` |
| Same, but pump or needle state cannot be proven before cleanup | Uncertain | No automatic motion; pump STOP and needle STOP attempted | `recovery_cleanup` not_attempted, `manual_inspection_required` (uncertain): `physical_state_uncertain` |
| Cleanup itself fails | Uncertain | Stop; no further motion | `recovery_cleanup` failed: `physical_state_uncertain` |
| Pump STOP unconfirmed or pump error | Uncertain | No needle motion | `physical_state_uncertain` |
| Needle fault, STOP during movement, lost Arduino link | Needle software position uncertain | No pump motion | `physical_state_uncertain` |
| Operator Ctrl+C during a cycle, or journal failure | Mid-cycle | No automatic motion | `manual_inspection_required` |
| Operator declines a reagent checkpoint | At rest | Run ends (exit 3) | `terminal_noncompletion` |

Before any new live run, `prepare()` inspects the latest live run folder and
refuses to start while it is `manual_inspection_required`,
`physical_state_uncertain`, `journal_corrupt`, or marked for operator review,
until the operator reconciles the rig and passes
`--acknowledge-review <run id>`. Mock runs never gate live runs. There is no
automatic resume.

## Stage policy

`interval_minutes` is start-to-start; late cycles are logged and never overlap.
A stage runs until a plateau, until `max_hours`, or until an operator decision.
Measurements are scheduled every interval while the stage is younger than
`max_hours` (60 min / 26 h gives 25; 15 min / 2 h gives 7). `max_measurements`
is optional; an explicit value is only a safety cap, and `prepare()` rejects one
that would end a stage before `max_hours`. A measurement that shows plateau
counts even if it finishes after the ceiling.

A stage that reaches its limit without a plateau is never taken as complete:
the journal records `stage_limit_reached` (`plateau_not_reached`) and the
operator types `CONTINUE` (another stage duration, same plateau window),
`ADVANCE` (next reagent checkpoint), or `ABORT`. A non-interactive terminal,
end of input, or a Level 2 mock chooses ABORT. Three stable intervals require
four valid measurements in the same stage.

## Run identity

Run folders are named `<stamp>_si6_<mode>`, `<stamp>_diagnostic_<test>_<mode>`,
or `<stamp>_processing_only[_mock]`. Live runs go to `output.run_root_dir`
(`results/runs/si6`) and mock runs to its `_mock` sibling. The folder name is
the dataset display name in every figure title and in `manifest.json`, which
also records `run_kind`, `mode`, and `diagnostic_selection`. The journal's first
event records the same identity.

## Configuration model

- `configs/machines/00_machine.local.yaml`: Chemyx COM port and NMR endpoint.
- `arduino/configs/arduino.local.yaml`: Arduino COM identity, validated D3/D4
  wiring review, steps per logical unit, UP direction sign, software bounds,
  and named UP/DOWN positions. Pass it with `--arduino-config` on every live
  run; both scripts default to the uncommissioned `arduino.example.yaml`.
  `runs/arduino/needle_state.json` holds the durable software position estimate.
- `configs/experiments/02_si6_automated_nmr.yaml`: cycle volumes and settle
  time, stage intervals and durations, repeat count, pump rate and syringe, NMR
  settings, tracked window, plateau thresholds, and output folder. Its operator
  entries are read as needle DOWN and UP by this workflow.
  `three_instrument.initial_plateau_stopping_enabled` enables early stopping
  for the initial stage without changing the legacy script's policy. Mock or
  live is chosen only on the command line.

## Offline requirements

Python: `offline/requirements-lock.txt` (27 pinned packages) covers everything;
no package was added. Arduino: `offline/arduino_toolchain.ps1` bundles a
portable arduino-cli with the UNO R4 core (`arduino:renesas_uno`) and compiles
the sketch offline; the sketch needs no third-party Arduino library. Serial
drivers for the Chemyx USB-serial adapter must be exported from a laptop where
the pump already works (`offline/serial_drivers.ps1 -Export`). See
[OFFLINE_SETUP.md](OFFLINE_SETUP.md).

## Implementation gates

The ordered operator procedure is
[LIVE_COMMISSIONING_CHECKLIST.md](LIVE_COMMISSIONING_CHECKLIST.md). Before
enabling a live integrated workflow:

1. Review the already-validated D3 STEP / D4 DIR wiring. Do not add ENABLE or
   limit-switch wires or change driver settings for this supervised demo.
2. Upload firmware 1.2.1 and verify identity with connection-only Test 1.
3. Complete staged Arduino motion tests; calibrate logical UP/DOWN and
   explicitly confirm software HOME after physical inspection.
4. Verify the Chemyx and NMR independently.
5. Run Level 1 live, then one short attended Level 2 cycle, before any
   multi-hour run.

Automated mock tests already cover the cycle order, plateau paths, stage
limits and decisions, Ctrl+C, persistence failure, NMR failures with and
without a provable physical state, cleanup failure, run identity, and recovery
classification (`tests/test_three_instrument_si6.py`).
