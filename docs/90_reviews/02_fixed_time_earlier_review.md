> Historical audit. Current policy: prior-dose history does not block a new fixed-time run; pump loading is refreshed, while startup MOVES to the official Arduino-only HOME without redefining it. See [current restart behavior](../03_fixed_time_experiment/05_restart_behavior.md). Other findings retain their historical scope.

> [Documentation home](../00_START_HERE.md) | [Technical reviews and audit evidence](00_START_HERE.md)

# Independent review: SI6 fixed 48-hour / 6-hour acquisition

Review date: 2026-10-16. Performed by a separately delegated senior Python
automation/instrument-control agent after the initial implementation. The review
read the actual working-tree Python, configuration, drivers, durable state
implementation, CLI changes and tests; it did not rely on the implementing
agent's description. All execution used HOME mock transports or injected fake
clients. No physical instrument, live serial connection, NMR network endpoint,
commit or push was used.

## Findings and corrections

The earlier failure path is identifiable in the retained production workflow:
`three_instrument_si6.py:Services.nmr_measurement:621` raises
`VerificationError` at line 658 for a missing/empty retrieved file, and its
processing branch raises for a missing processing directory at line 678.
`sample_cycle:833` finishes verified cleanup but raises
`MeasurementFailedAfterCleanup` at line 918 for an ordinary acquisition/retrieval
failure. Separately, the legacy `instrument_operations.py:run_nmr_acquisition:99`
propagates RPC/wait/export errors, uses generic text extraction and invokes peak
analysis. The new runner bypasses these NMR/measurement paths completely while
reusing their established physical transfer driver.

| Finding and classification | Correction and exact source location |
|---|---|
| REMOVE: numerical endpoint dependencies could prevent acquisition-only startup even though analysis was never called. An independent import-blocking test reproduced this through `stage_completion` importing NumPy. | `chemyx_lab/workflows/si6_automated_nmr.py:_parse_stage:546` imports `validate_completion` only inside the existing completion branch at line 586. Numerical algorithms and production recipes are unchanged. |
| REMOVE: unused `nmr.target_ppm` was mandatory through a shared settings builder; an invalid analysis-only environment target also blocked startup. | `chemyx_lab/workflows/si6_fixed_time.py:load_settings:149` calls the backward-compatible acquisition-only settings option; `si6_automated_nmr.py:build_instrument_settings:744` and `chemyx_lab/config.py:_explicit_env_nmr_overrides:446` ignore this analysis field only for acquisition-only callers. The new preset no longer contains it. |
| REMOVE: a sticky two-scan checkout environment could override the production eight-scan preset. | The acquisition-only settings option ignores `NMR_DEFAULT_SCANS`; the explicit new YAML's `nmr.scans` is authoritative. Production and standalone checkout callers retain their existing behavior. |
| RECOVER: NMR ownership contention, including an unwritable lock directory, rejected the entire chemistry run. Mock runs also acquired the global live NMR lock. | `si6_fixed_time.py:run:404` attempts that lock only in live mode; failure disables NMR STARTs for this run and records failed observations while timed fluidics continue. |
| REMOVE / RECOVER: optional NMR folder creation, command-log opening/writing, configuration/report export, stage metadata and the derived `run_state.json` projection could terminate otherwise safe operation. | `si6_fixed_time.py:optional_event:66`, `FixedTimeRecorder.record:76`, `OptionalCommandLog:98`, `run_stage:365`, `run.optional_write:428`. Snapshot-only recovery independently replays a valid journal and matches the just-written sequence/event; missing/corrupt required physical evidence still stops. Raw-folder errors are isolated inside acquisition. |
| RECOVER: contradictory NMR status fields could authorize START despite a busy field. Late retrieval could silently save a different acquisition's spectrum. | `si6_fixed_time_nmr.py:running_state:22`, `receipt_identity:44`, `acquire:85`: busy evidence takes priority, unknown/error codes never prove idle, START receipts are checked when supplied, previous receipts are rejected and every retrieval poll must retain the completed receipt and idle state. |
| RECOVER: valid direct server export plus inline TD raised `FileExistsError`; an incomplete export could prevent saving available TD. A local FD JCAMP could be mislabeled as raw. | `si6_fixed_time_nmr.py:local_fid:74`, `acquire:85`: accept an identifiable FID export, preserve all existing exports, save returned TD to `.rpc.dx` when necessary, and never claim a local frequency-domain export as raw. A pre-existing observation file skips START and is preserved. |
| RECOVER: a malformed/false worker `NMR_SAVED` reply could be accepted without a saved raw file. | `si6_fixed_time_nmr.py:acquire_bounded:171` validates the result mapping and status; SAVED must point to this observation's nonempty expected file. Status evidence excludes duplicate raw/processed spectra and peak lists. |
| RECOVER, critical acquisition defect: `asdict(NmrSettings)` contains a `Path`; request JSON serialization failed before the NMR child could ever launch. Earlier failure-only worker tests accidentally passed on that unrelated error. | `si6_fixed_time_nmr.py:acquire_bounded:171` serializes paths as strings; `main:198` restores `save_dir` to `Path`. New successful tests cross the real JSON/subprocess/main/result boundary with only the child RPC transport replaced by a fake, prove exactly one START and eight scans, and validate both inline and direct-export raw saves. Timeout/malformed-reply tests now explicitly assert child dispatch was reached. |
| RECOVER: the precise NMR failure disappeared if later physical cleanup failed. | `si6_fixed_time.py:Rig.sample:288` writes an optional `nmr_observation` event immediately after acquisition and before cleanup; completed-cycle exports still require successful physical cleanup. |
| RETAIN, corrected reporting: `DoseGuard.mark` updated memory before its durable write; failed CONFIRMED persistence could falsely print a confirmed dose. | `chemyx_lab/workflows/dose_guard.py:DoseGuard.mark:47` publishes the new in-memory status only after durable replacement. Failed persistence never authorizes replay. |
| RECOVER: a RESERVED admission followed by a crash before the first journal event had no recovery path. Retirement was also impossible if old journal evidence was unavailable or corrupt. | `chemyx_lab/workflows/dose_recovery.py:reconcile:20`: intact RESERVED plus verified no-delivery facts permits `unused`, including the missing/empty initial-journal gap. A provided nonempty corrupt/wrong-run journal refuses `unused`. Explicit `retired` requires isolating/retiring the old reactor and servicing the syringe for a new reaction; it makes no delivery-completion claim. |
| RECOVER: malformed canonical dose JSON needed an explicit path that preserves the original evidence. | `dose_recovery.py:reconcile:20` permits only retirement of unreadable evidence, first saving exact original bytes as `.json.unreadable-<uuid>.bak`; the canonical record retains status UNREADABLE, preserved fields and retirement history. `chemyx_manual.py:dose_records:194` handles non-mapping JSON conservatively. |
| REMOVE: custom output directories sharing a basename could reuse a canonical experiment identity. | `si6_fixed_time.py:new_identity:400`, `run:404`, `main:551` allocate a new timestamp/random identity independently of a custom output basename. Existing results are still never overwritten. |
| RETAIN, corrected interruption reporting: a needle STOP/persistence exception could replace the original error or prevent other STOP attempts. | `si6_fixed_time.py:Rig.stop:345` independently attempts both pump STOPs and needle STOP; it preserves the original failure. `FixedTimeRecorder.record:76` also preserves interruptions wrapped by the shared journal implementation. |
| RETAIN, corrected manual confirmation: an unhomed relative jog's DONE count could disagree with its final status counter. | `arduino/python/manual_jog.py:tracked_relative_jog:16` checks both the DONE count and final stopped, fault-free counter before persisting success. Pending movement remains uncertain across interruption; failed STOP reporting cannot mask the original failure. |

Paths abbreviated in the table are under `chemyx_lab/workflows/` unless otherwise
qualified. Line numbers refer to the reviewed working-tree snapshot.

## Exit, startup, deadline and wait audit

The following covers the significant exits and waits reachable from the new
entry point, including its shared physical driver calls.

| Condition | Classification | Location and disposition |
|---|---|---|
| Missing required config, invalid acquisition settings, incompatible syringe/loading/calibration, nonpositive/nonfinite timing or overflow when converting to seconds | RETAIN | `si6_fixed_time.py:load_settings:149`; reject before physical movement. Numeric YAML strings are normalized before use. These are configuration errors, not experimental-data gates. |
| Existing output directory or inability to create a new required journal directory | RETAIN | `si6_fixed_time.py:run:404`; fail before hardware, preserving prior results. A run cannot dispatch accountable movement without durable physical evidence. |
| Startup confirmation declined, EOF or operator interruption | RETAIN | `si6_fixed_time.py:run:404`; no normal-operation reprompt; deliberate operator cancellation is honored. |
| A previous failed report, stale experiment marker, old process ID or owner file by itself | REMOVE as blocker | The new runner does not use previous report status to admit a run. `chemyx_position.py:PositionLedger.acquire:481` uses the OS lock; a stale owner file alone does not own it. |
| Genuine active pump ledger owner or Arduino port owner | RETAIN | `chemyx_position.py:PositionLedger.acquire:481`, `arduino/python/run_lock.py:PortProcessLock.acquire:19`; another controller cannot be cleared by recovery. |
| NMR connection/template/START/status error; missing/delayed data; missing/unwritable output folder; acquisition/retrieval timeout; unknown/busy state; worker error | RECOVER | `si6_fixed_time_nmr.py:acquire:85`, `acquire_bounded:171`; return `NMR_FAILED` or `NMR_MISSING`. `Rig.sample:288` adds a final ordinary-exception boundary around acquisition only, then verifies fluidic state and performs cleanup. |
| NMR lost START reply, ongoing acquisition or changed receipt | RECOVER without retry | At most one START per observation. Later scheduled observations must independently prove idle; unknown/busy status skips START. The timed controller has no background acquisition thread and calls its one killable worker synchronously. |
| NMR retry/poll hang | REMOVE | Completion and late retrieval have monotonic deadlines and bounded sleeps in `acquire:85`. `acquire_bounded:171` imposes a hard child-process timeout, kills/reaps a timed-out worker and records failure. Default outer limit is 430 seconds; it does not reset either stage clock. |
| FFT, ACME, peak/SNR/integration/solvent/plateau/rolling-slope/completion results, missing acquisition timestamp | REMOVE | None is called by `run_stage:365` or `Rig.sample:288`. Acquisition receipt identity is protocol evidence, not a chemical or timestamp-quality gate. Old processing remains available offline. |
| Bench session-age timeout after 59/120 seconds | REMOVE | `si6_fixed_time.py:run:404` removes only the long-lived controller session-age cap at line 497. Per-command serial deadlines, bounded JOG completion, travel checks and firmware watchdog remain in `arduino/python/controller.py` and `needle_state.py:TrackedNeedle.move_to:217`. |
| Stage deadline reached | RETAIN, normal transition | `si6_fixed_time.py:run_stage:365` admits no new cycle at/after the monotonic deadline. An already-admitted physical cycle/acquisition/cleanup finishes. Missed slots are skipped without resetting the stage clock or issuing catch-up bursts. |
| Pump STOP acknowledgment missing, transport/configuration command failure, uncertain or mismatched displacement, capacity/retained-volume violation | RETAIN | `Rig.idle:251`, `Rig.move:262`, `chemyx_manual.py:confirm_idle:299`, `chemyx_position.py:plan_transfer:376`, `si6_automated_nmr.py:run_safe_metered_move:1167`; stop, retain unknown/pending evidence and require physical reconciliation. |
| Needle moving/faulted, unknown/pending HOME, changed calibration/state, command completion mismatch or travel limit | RETAIN | `verify_needle:235`, `Rig.needle_to:255`, `needle_state.py:TrackedNeedle.move_to:217`, `manual_jog.py:tracked_relative_jog:16`; no HOME inference or automatic unknown-position resume. |
| Shared physical journal or pump/needle movement-state persistence fails | RETAIN | `Rig.move:262`, `Rig.needle_to:255`, shared `run_safe_metered_move`, `NeedleStateStore.save`, `PositionLedger._commit`; durable motion/accounting evidence remains required. Only a independently verified rebuildable run projection is optional. |
| Prior unreconciled RESERVED/DISPATCH_INTENT/CONFIRMED/UNREADABLE dose; same identity reservation; uncertain dose completion | RETAIN pending explicit recovery | `check_previous_doses:226`, `DoseGuard.reserve:37`, `Rig.dose:322`. A new random ID does not authorize dosing an old reactor. No automatic dose retry or partial resume exists. Stage 2 begins only after a single matched durable Channel 2 completion and canonical confirmation. |
| Physical cleanup fails after an NMR failure, or after the final observation | RETAIN | `Rig.sample:288` and `run:404` report FAILED/INTERRUPTED, retain state and attempt independent STOPs. Overall COMPLETE is not claimed. The NMR failure event is preserved before cleanup. |
| Optional stage/result/console/command-log export fails | RECOVER | `progress:39`, `optional_event:66`, `OptionalCommandLog:98`, `run.optional_write:428`, `run_stage:365`. Required physical records remain a separate gate. |
| KeyboardInterrupt, SystemExit, unforeseen physical/controller exception, transport close failure | RETAIN | Inner `run` handlers stop while transports are still open; the outer handler preserves a failed/interrupted report. There is no suppression around physical movement to continue an unknown state. |

Physical serial reads/writes and individual Arduino commands retain their driver
timeouts. There is no infinite application retry loop. General operating-system
or filesystem stalls, machine suspend/power failure and real hardware faults
cannot be ruled out by Python mocks; this review does not claim otherwise.

## Manual operation and failed-run recovery

`arduino/scripts/needle_control.py:main:24` routes only manual UP/DOWN to
`tracked_relative_jog`. The existing 200-step calibrated increment and polarity
produce +1, +2, +1, 0, -1 for UP/UP/DOWN/DOWN/DOWN. Before HOME this persisted
relative count remains `position_valid=False` and cannot authorize automation.
Calibrated manual movement uses the same `TrackedNeedle` reference as automation.
The configured -3 through +5 range, motion-time checks, STOP and fault handling
remain. `arduino/python/home_recovery.py:restore_home:55` records set-home at the
current physically verified stopped location without JOG; automatic targets
remain absolute HOME/DOWN=0 and UP=1. No position is independently measured.

`chemyx_manual.py:cmd_recover_stale:693` acquires the same pump OS lock, changes no
HOME or plunger position and cannot clear an active owner. Acquiring the ledger
preserves known completed movement and changes unfinished motion to UNKNOWN.
`cmd_reconcile_dose:706` holds that lock and verifies pump STOP before one factual
operator confirmation. Mock reconciliation defaults to separate mock dose state.
Manual Channel 2 motion retains dose-integrity restrictions; waste-routing
authorization never overrides an uncertain/reserved dose.

Exact WORK launch, COM6 selection, manual and recovery commands are in
[01_run_guide.md](../03_fixed_time_experiment/01_run_guide.md). The key recovery forms
are `recover-stale --channel both`, `set-home --channel <affected channel>`, and
`reconcile-dose --channel 2 --experiment-id <old ID> --resolution unused|retired`,
with `--live --config configs/chemyx/manual_control.yaml` and the selected
`--machine-config`. Supply the actual old `--journal` when available. Neither
recovery operation repeats a dose or moves the physical plunger.

## Independent fault injection and execution evidence

The review added `tests/test_si6_fixed_time_independent_review.py`. It covers
contradictory/unknown NMR status, pre-existing raw files, simultaneous inline and
server exports, partial/FD exports, stale or changed receipts, invalid worker
results, no numerical dependencies, optional output/snapshot failures, the
required physical-journal failure stop, stale RESERVED admission gaps, corrupt
canonical/journal retirement, failed CONFIRMED persistence, explicit-output
identity collisions, finite-to-infinite clock conversion, analysis-only target
omission, NMR lock failure with both transports replaced by mocks, interrupted
NMR user abort, failed physical cleanup after NMR failure and mismatched manual
movement counters. Initial testing reproduced and then corrected the NumPy
startup dependency. Final cross-review also reproduced and corrected the worker
Path serialization bug. A real child that sleeps for 60 seconds is terminated
with a 0.3-second test timeout; its failure returns without waiting for that sleep.
Successful worker tests use a child-local fake RPC client, with no socket or
instrument connection. A preliminary loopback HTTP fixture was rejected by the
Windows network sandbox and was replaced with this no-network test mechanism.

The existing focused tests additionally cover all 48/6-hour windows and 120/30
minute intervals under missing and repeated failed NMR observations, delayed
acquisition crossing the stage boundary, no repeated START, killed-worker
timeout handling, actual shared mock-driver cleanup, single dose, interrupted
dose replay refusal, stale/active ownership and persistent manual relative
movement versus automated absolute positioning.

Accelerated full-schedule evidence:

- Default CLI mock: `results/fixed_time/mock/SI6_FIXED_20261016T220711Z_b74dc4b73af2`;
  log `results/fixed_time/full_schedule_validation.log`. Both physical drivers
  mocked, 36 saved fixture observations, 172800/21600-second stage windows,
  exactly one 1.86 mL dose and confirmed cleanup.
- Independent actual-driver mock with **every NMR connection failing**:
  `results/fixed_time/mock/SI6_FIXED_ALL_NMR_FAILED_REVIEW_fed7e60d3d2e`;
  log `results/fixed_time/independent_all_nmr_failed_validation.log`. Numerical
  analysis entry points were replaced with assertions. All 36 observations
  failed and were preserved; both stages completed at 172800/21600 simulated
  seconds, exactly one 1.86 mL Channel 2 infusion was confirmed, and final physical
  cleanup completed. No 54-hour wall-clock wait occurred.

Final test execution used `C:/Users/iyer95/miniconda3/envs/ai/python.exe`:

```powershell
python -m pytest -q tests/test_si6_fixed_time.py tests/test_si6_fixed_time_independent_review.py arduino/tests/test_tracked_manual_relative.py tests/test_si6_automated_nmr.py tests/test_release_inventory_hardening.py arduino/tests/test_manual_jog.py tests/test_config.py --basetemp .codex_pytest_temp_independent_release_final
```

Result after the worker-boundary correction: **99 passed in 46.04 seconds**.
This includes both real-child successful raw saves, actual hung-child termination
and sticky-checkout scan precedence. The 26 warnings are existing nmrglue/NumPy
dtype deprecations in archived numerical-helper integrity checks. The implementing agent's final manual/checkout
regression command, independently reported with actual output, was
`pytest tests/test_chemyx_manual_control.py tests/test_simple_instrument_checkout.py -q --basetemp .codex_pytest_temp_fixed_final_manual`:
**36 passed in 63.57 seconds**. These are 135 distinct final targeted cases.
No blanket claim is made about the repository's unrelated tests.

`python offline/verify_bundle.py` passed: **28 offline assets verified, 27 wheels,
no network used**. The release inventory includes the new runner, modules,
preset, tests and operating/review/offline deployment guides, and excludes
physical state and results. The handoff is exported with:

```powershell
python scripts/prepare_si6_release.py --working-tree --with-offline-payload --output-dir dist/si6_fixed_time_handoff
```

The generated immutable ZIP, release manifest and `.sha256` sidecar identify the
exact uncommitted review snapshot. Preserve WORK's runtime state, HOME references,
dose evidence, existing operator YAML and experimental results using
[04_offline_deployment.md](../03_fixed_time_experiment/04_offline_deployment.md).

## Assessment

No chemical result, missing NMR data, numerical processing dependency, ordinary
NMR exception, NMR ownership failure, old failed-report marker or optional output
failure remains an independent reason to shorten a fixed-time stage in the
reviewed paths. Admission deadlines are monotonic; stage transitions depend on
elapsed time and the one protected dose. The remaining deliberate stops concern
invalid physical configuration, active ownership, uncertain motion/reference,
required durable physical evidence, user interruption or reagent-dose integrity.
The mock results support the software behavior; they do not validate real
instrument communication, mechanical clearance, actual delivered volume or all
possible physical failures. WORK checkout and physical verification remain
necessary before a real experiment.
