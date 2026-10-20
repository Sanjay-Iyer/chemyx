> Historical audit. Current policy: prior-dose history does not block a new fixed-time run; pump loading is refreshed, while startup MOVES to the official Arduino-only HOME without redefining it. See [current restart behavior](../03_fixed_time_experiment/05_restart_behavior.md). Other findings retain their historical scope.

> [Documentation home](../00_START_HERE.md) | [Technical reviews and audit evidence](00_START_HERE.md)

# SI6 fixed-time experiment: new independent audit

Audit date: 2026-10-16, on the HOME laptop. The audit read and traced the actual
working-tree code from `scripts/04_si6_fixed_time_run.py` through every imported
physical, NMR, ledger, dose and journal module. It did not rely on the earlier
implementation report or on [02_fixed_time_earlier_review.md](02_fixed_time_earlier_review.md).
All execution used mock transports, fake RPC clients and simulated clocks. No
instrument, serial port or NMR endpoint was contacted, and nothing was committed
or pushed. The physical-state records under `runtime/` and `runs/arduino/`, and
existing results, were not modified; every audit run wrote to temporary or
scratch folders.

## 1. Executive summary

| Question | Answer |
|---|---|
| Ready for WORK deployment? | **The handed-off package `si6_review_de5b4b9536f5_offline.zip` is not.** Its NMR worker could discard every real acquisition (finding 1). The corrected working tree, regenerated as `dist/si6_fixed_time_handoff_audit`, is **ready for WORK checkout**. The long run should follow a passing `--nmr-check` on WORK. |
| Completes a 48 h / 6 h simulation? | Yes. Four full accelerated runs with the real mock pump and Arduino drivers (all NMR saved, all failed, intermittent, and all 36 observations through the real acquisition logic) each ran both full stage windows (172,800 s and 21,600 s), 24 + 12 observations at the correct offsets, exactly one 1.86 mL Channel 2 infusion at 1.0 mL/min and complete cleanup (section 8). |
| Missing NMR spectra nonfatal? | Yes. Exceptions, `NMR_MISSING`, `NMR_FAILED`, malformed worker replies, an unwritable raw folder, worker timeouts and an unavailable NMR lock all leave the timed schedule running. No code path from the new runner reaches the legacy analysis or "missing file" gates. |
| Can the acquisition path save valid raw NMR data? | In simulation, yes, after this audit's fixes. This includes realistic NMReady status sequences and the real child-process boundary, saving the instrument text byte-for-byte. **Before the fixes, the vendor-documented in-progress status (`ResultCode: 2`, 0 scans run) made the worker abandon the acquisition after one poll.** Real-instrument behavior is still unverified; `--nmr-check` exists to check it on WORK. |
| Major bugs remaining? | None known in the reviewed paths. Residual risks, all named in sections 2 and 3: real NMR reply shapes have never been captured; any single transient serial failure on a physical step ends the run, with no resume; and a pre-dispatch failure inside the dose window strands the dose as `DISPATCH_INTENT`. Two physical-behavior changes that would reduce these are **proposed, not implemented** (findings 13 and 14). |

## 2. Bugs discovered

**The previous review's claimed fixes (A–H), checked independently:**

| Claim | Verdict |
|---|---|
| A. Worker `Path` serialization | Correct. `save_dir` crosses as a string and `main` restores it. The successful path is proven through a real child process: the request reaches the child, exactly 8 scans are sent, the raw TD is saved, and the result returns. No non-JSON value remains in the request. |
| B. No re-entry into the old NMR/analysis path | Correct. The runner never calls `run_nmr_acquisition`, `process_fid` or the legacy `VerificationError` gates (traced call graph; existing tests replace them with failures). |
| C. Optional outputs cannot stop the run | Correct, and required movement/dose evidence still stops it. Teardown after COMPLETE was a remaining gap (finding 9). |
| D. NMR ownership, one START, bounded polling | One START, positive idle before START, the 430 s outer kill and nonfatal lock failure are correct. **But the polling loop failed fast on any unrecognized, lagging or failed status read, and the receipt match was over-strict** (findings 1 to 3). |
| E. Raw data handling | The inline TD, direct export, `.rpc.dx` and FD-export rejection paths are correct. FID text in the FD field was missed (finding 4); writes were not byte-faithful (finding 5). |
| F. Dose-guard durability | The ordering fix is correct. No window repeats a dose or reports an undurable CONFIRMED, and Stage 2 requires CONFIRMED. Recovery instructions were wrong for most statuses (finding 7), and the dose-window stranding is proposed in finding 14. |
| G. Manual jog consistency | Correct. Before HOME, the DONE count, final counter, idle and fault are all checked. Calibrated jogs use the automation's DONE check. |
| H. No numerical dependencies at startup | Correct. NumPy, SciPy, matplotlib and nmrglue are imported lazily, proven by an import-blocking subprocess test. |

Status key: **Fixed** = reproduced by a failing test, corrected, and regression
tested. **Proposed** = needs your decision because it changes physical motion or
dose semantics. **Doc** = procedure or guide correction.

### Finding 1: the NMR worker abandons any acquisition whose status code it does not recognize

| Field | Information |
|---|---|
| Severity | **Critical** (all NMR data of the run) — Fixed |
| File | `chemyx_lab/workflows/si6_fixed_time_nmr.py` |
| Function | `acquire` (completion polling loop), `running_state` |
| Root cause | After START, any status for which `running_state` returned `None` raised `RuntimeError("NMR completion state unknown")` on the first poll. `running_state` treats only `ResultCode` 0 as idle and 1 as running; every other code is "unknown". The legacy parser `status_indicates_running`, which the hardware proved on 2026-08-10, treats every nonzero code as running and keeps waiting. |
| Trigger | The real instrument reports a code other than 0/1 while acquiring. The vendor's only documented `iFlow/ExperimentStatus` example (`docs/08_reference/vendor/nmr_rpc_api/html/rpc-api.md`, section `*/interfaces/iFlow/ExperimentStatus*`) is exactly that: `ResultCode: 2`, `NumberOfScansRun: 0`, empty `JDX_FileContents_TD/FD`. |
| Experiment impact | Every observation would be `NMR_FAILED` within seconds of START, for all 36. The instrument keeps acquiring, but nobody retrieves the data. The run completes "successfully" with no spectra. |
| Evidence | `test_documented_in_progress_status_is_waited_through_and_raw_saved` and the real-child `test_real_worker_child_saves_documented_sequence_byte_for_byte` failed before the fix with `NMR completion state unknown: {... 'ResultCode': 2}`. The 2026-08-10 journal (`results/runs/automated/chemyx_demo_081026_v3/20260810_171441_si6`, sequences 21→22) shows the legacy loop waited 81 s through the real acquisition before saving `##DATA TYPE=NMR FID`. |
| Fix | Only positive completion ends polling. Running, lagging, unrecognized, empty or failed status reads are polled again until the existing bounded `max_wait_seconds` deadline (300 s). Any nonzero `ResultCode` counts as busy evidence (`busy_evidence`). START is still sent at most once, positive idle is still required before START, and the receipt checks are unchanged in meaning. |

### Finding 2: a strict receipt match on a START reply shape that was never captured

| Field | Information |
|---|---|
| Severity | **High** (all observations) — Fixed |
| File / function | `si6_fixed_time_nmr.py` / `acquire`, new `receipts_conflict` |
| Root cause | `completed_receipt != expected_receipt` compared the `(ExperimentNumber, TimeStamp)` tuple from the PUT `RunExperiment` reply with the status `OriginalReceipt`. The PUT reply is undocumented (the reference documents only the GET reply), and no real reply has been captured; the legacy code ignored it. A reply that carries `ExperimentNumber` but no `TimeStamp` produces `(1, None)`, which never equals `(1, "<time>")`. |
| Trigger | A PUT reply with fewer receipt fields than `OriginalReceipt`. |
| Experiment impact | "NMR completion receipt does not match this START" for every observation, so no spectra. |
| Evidence | `test_start_reply_with_fewer_receipt_fields_still_matches_completion` failed before the fix. |
| Fix | Only fields present in both replies can conflict. A real conflict (`test_completion_of_a_different_start_receipt_is_never_saved`) and a stale previous receipt (`test_stale_previous_spectrum_is_never_saved_as_this_observation`) still never save. |
| Residual risk | A reply carrying a `TimeStamp` formatted differently from `OriginalReceipt` (the two documented examples differ in format, from 2015 versus 2017 software) would still time out every observation. `--nmr-check` exposes this before the long run. |

### Finding 3: transient or lagging status reads cost the observation

| Field | Information |
|---|---|
| Severity | **Medium** (per observation) — Fixed |
| File / function | `si6_fixed_time_nmr.py` / `acquire`, new `confirm_idle` |
| Root cause | One exception from any status read propagated and ended the observation, including one read timeout mid-acquisition. An idle status still showing the previous run's receipt right after START raised at once, instead of waiting for the instrument to register the START. One failed or empty pre-START read skipped START. |
| Trigger | A transient Ethernet or HTTP read failure; a status update that lags START; an empty reply. The legacy loop also returned at once on a lagging idle status. That race lets the old path save stale or empty data, which matches the original "missing NMR data" stop. |
| Experiment impact | That observation's spectrum is lost even though the instrument acquired it. |
| Evidence | `test_transient_status_errors_and_empty_replies_do_not_discard_acquisition`, `test_status_that_still_shows_previous_run_after_start_is_waited_out` and `test_transient_pre_start_reply_is_rechecked_before_skipping` failed before the fix. |
| Fix | After START, failed reads and lagging statuses are re-polled until the deadline. Before START, a failed or unrecognized read is re-read at most twice (2 × `poll_seconds`); a positively busy instrument is never started and never waited on. A retrieval phase that ends on failed reads is still `NMR_FAILED`, and one that ends without data is `NMR_MISSING`. |

### Finding 4: raw FID text in the field the proven legacy saver read first was ignored

| Field | Information |
|---|---|
| Severity | **Medium** — Fixed |
| File / function | `si6_fixed_time_nmr.py` / `raw_text`, `local_fid` |
| Root cause | `raw_text` accepted inline data only from `JDX_FileContents_TD` and generic keys. The legacy saver (`extract_text_payload`) read `JDX_FileContents_FD` before `JDX_FileContents_TD`, and on 2026-08-10 it saved an `NMR FID`. So the real reply either had an empty FD field or carried the FID in that FD-named field; HOME evidence cannot tell which. |
| Trigger | NMReady returns the FID in `JDX_FileContents_FD` (or `JDX_FileContents`). |
| Experiment impact | Every observation `NMR_MISSING` although the raw FID was in the reply. |
| Evidence | `test_raw_fid_under_the_fd_field_is_saved_but_a_spectrum_never_is` failed before the fix. |
| Fix | The TD field is used first, unless its own header declares a non-FID type. Any other text field counts only when its JCAMP `##DATA TYPE` declares an FID. A frequency-domain spectrum is still never saved as raw (same test). Generic keys now also require the FID declaration. |

### Finding 5: inline raw JCAMP was not saved byte-for-byte

| Field | Information |
|---|---|
| Severity | **Low** (data fidelity) — Fixed |
| File / function | `si6_fixed_time_nmr.py` / `acquire` (retrieval write) |
| Root cause | The text was written in Windows text mode, so each `\r\n` from the instrument became `\r\r\n`. |
| Evidence | 4,220 of 4,220 lines of the real 2026-08-10 API file end in `\r\r\n`; the NMR's own exports (`results/raw/nmr/06-08-26`) use CRLF. `read_jcamp_fid` parses both identically (8,192 points, identical metadata). `test_inline_raw_is_saved_byte_for_byte_with_instrument_line_endings` failed before the fix. |
| Fix | Open with `newline=""`. |

### Finding 6: the new NMR worker had no real-hardware check before the long run

| Field | Information |
|---|---|
| Severity | **High** (verification gap) — Fixed with a new mode |
| File / function | `chemyx_lab/workflows/simple_instrument_checkout.py` / `acquire_nmr` uses the legacy `base.run_nmr_acquisition` |
| Root cause | The documented WORK checkout never exercises `si6_fixed_time_nmr.acquire`. The first real use of the fixed-time worker would be observation 1 of the 54-hour run, after the reaction is committed. |
| Fix | `python scripts\04_si6_fixed_time_run.py --nmr-check`: one 8-scan acquisition through `acquire_bounded` under the same NMR lock. It opens no pump, needle, position ledger or dose record and processes nothing. Its `nmr_check.json` records the instrument's real status replies before START and at the end. Tests: `test_nmr_check_*`. |

### Finding 7: dose-blocker recovery printed a command its own status refuses

| Field | Information |
|---|---|
| Severity | **Medium** (startup barrier without a working instruction) — Fixed |
| File / function | `chemyx_lab/workflows/dose_recovery.py` / `reconciliation_command`; `si6_fixed_time.py` / `check_previous_doses`; `chemyx_manual.py` / `channel2_refusal` |
| Root cause | The printed command always said `--resolution unused`, which `reconcile` refuses for DISPATCH_INTENT, CONFIRMED and UNREADABLE records. It used a `<old-run>/operation_journal.jsonl` placeholder: PowerShell rejects `<`, and production two-stage runs keep their journal at `logs/experiment_journal.jsonl`. It also omitted `--machine-config`, which points a COM6 operator at COM4. `channel2_refusal` printed `records[0]` even when another record was blocking. |
| Trigger | A production run reserves `runtime/si6_doses/SI6_REAL_TWO_STAGE_001.json` before opening any instrument, so the failed WORK run most likely left one: the most likely first blocker. Every completed fixed-time run also leaves a CONFIRMED record that must be retired before the next reaction. |
| Experiment impact | Launch blocked, with a recovery command that fails or targets the wrong port. |
| Fix | The command now uses the resolution that status accepts (`unused` only for RESERVED) and the run's real `--config` and `--machine-config`. It includes the old run's journal when one is found under `results/fixed_time/live/<run>` or `results/experiments/<run>/logs/`, and otherwise omits `--journal` so the command runs as printed. The message states the status and run and why. Test: `test_previous_dose_blocks_with_the_one_recovery_its_status_accepts` (each printed resolution is then accepted by `reconcile`, and a new run completes), plus `test_unreadable_previous_dose_points_to_retirement`. |

### Finding 8: startup displacement message lacked port, direction and reagent hazard

| Field | Information |
|---|---|
| Severity | **Low** — Fixed |
| File / function | `si6_fixed_time.py` / `run` (startup HOME check) |
| Root cause | The message gave `chemyx_control.py home` without `--machine-config` and without saying what that HOME return would do. After every completed run, Channel 2 sits at -1.86 mL, and its HOME return *withdraws* 1.86 mL through the reagent line. |
| Fix | The message now gives the signed displacement, the exact `home` and `set-home` commands with config and machine config, and the motion `home` would perform. For Channel 2 it adds: "withdraw only from the reagent source, never from a reactor". Tests: `test_run_that_stopped_mid_cycle_*`, `test_completed_previous_dose_leaves_channel2_*`. |

### Finding 9: a transport close error after completion reported the whole run FAILED

| Field | Information |
|---|---|
| Severity | **Low** — Fixed |
| File / function | `si6_fixed_time.py` / `run` (outer handler) |
| Root cause | `status = "COMPLETE"` is set inside the `ExitStack`; an exception while closing a serial port or lock overwrote it with FAILED and printed recovery commands. |
| Experiment impact | After 54 completed hours, it could prompt unnecessary recovery actions and a nonzero exit. |
| Fix | Teardown errors after COMPLETE are recorded as `teardown_warning`. Test: `test_closing_a_transport_after_completion_is_a_warning_not_a_failed_run`. |

### Finding 10: configuration mismatch message had no values

| Field | Information |
|---|---|
| Severity | **Low** (likely first WORK blocker) — Fixed |
| File / function | `si6_fixed_time.py` / `load_settings` |
| Root cause | "manual HOME geometry/loading must match the experiment" did not say which file, channel or values. WORK's preserved `manual_control.yaml` may predate the 2026-10-16 convention: the first committed version had Channel 2 `syringe_volume_at_home_ml: null` and geometry from a recipe that then said 12.06 mm / 5 mL. |
| Fix | The message names both files and the differing diameter, capacity and HOME loading, and gives the correction path. Test: `test_geometry_mismatch_names_both_files_and_values`. |

### Finding 11: the deployment verifier fails if WORK lacks any packaged config

| Field | Information |
|---|---|
| Severity | **Medium** (deployment abort) — Doc |
| File / function | `docs/03_fixed_time_experiment/04_offline_deployment.md`; `scripts/verify_si6_environment.py` / `verify_release` |
| Root cause | `verify_release` requires all 27 packaged YAML files under `configs/` and `arduino/configs/` to exist, and raises "Missing/unsafe release file". The documented `robocopy` excluded both directories entirely and added back only the new preset. |
| Trigger | WORK lacks any packaged config, for example `configs/checkouts/si6_simple_instrument_check.yaml` (created 2026-10-16 14:42) or `configs/chemyx/manual_control.yaml` (created 2026-10-16 00:04). |
| Fix | Add-only copies: `robocopy "$Si6Stage\configs" "$Si6Root\configs" /E /XC /XN /XO` (and the same for `arduino\configs`). These copy only files absent on WORK and never replace an existing WORK YAML. The manual-defaults step now leaves an already-correct file untouched, and uses a fresh staging folder name. |

### Finding 12: the handoff ZIP is stale

| Field | Information |
|---|---|
| Severity | **High** for deployment — Fixed (regenerated) |
| Evidence | Before this audit, `si6_review_de5b4b9536f5_offline.zip` matched the working tree exactly: 7,930 of 7,930 entries byte-identical, a flat layout, no entries under robocopy-excluded names, and a longest staged path of 226 characters. It therefore contains the unfixed code and guides for findings 1 to 11. |
| Fix | A new package in `dist/si6_fixed_time_handoff_audit` (section 7). Do not deploy the old one. |

### Finding 13 (Proposed): one transient serial error on any physical step ends the run

| Field | Information |
|---|---|
| Severity | **High** (operational) — **Proposed, not implemented** |
| File / function | `si6_fixed_time.py` / `Rig.idle`, `Rig.move`, `verify_needle`; `chemyx_manual.py` / `confirm_idle`; `instrument_operations.py` / `configure_pump`; `chemyx.py` / `_verify_numeric` |
| Root cause | Each physical step requires every STOP acknowledgement, every echo-verified Chemyx setting and every Arduino STATUS read to succeed the first time. One full mock run needed 841 acknowledged STOPs, 724 echo-checked configuration commands and 75 needle moves, each followed by STATUS checks. Any single empty reply, garbled echo or STATUS read error stops the run, and there is no resume: a fresh launch restarts the 48-hour clock. |
| Proposed fix | One bounded retry (about 1 to 2 s) of **non-motion** commands only: STOP, Arduino STATUS, and `set units`/`set diameter`/`set rate`/`set volume` echoes, before declaring failure. `start` and JOG are never retried. Consequence: a genuinely failing device is detected 2 to 4 s later; no motion is added. |

### Finding 14 (Proposed): a pre-dispatch failure in the dose window strands the dose

| Field | Information |
|---|---|
| Severity | **High** (operational, at hour 48) — **Proposed, not implemented** |
| File / function | `si6_fixed_time.py` / `Rig.dose`; `dose_recovery.py` / `reconcile` |
| Root cause | `DISPATCH_INTENT` is made durable before `Rig.move`, which then re-verifies both pumps' STOP, the needle STATUS and the Channel 2 configuration echoes. A transient failure there leaves `DISPATCH_INTENT` with no infusion command sent. That state can only be `retired`, which retires a 48-hour reaction. |
| Proposed fix | Perform Channel 2's idle, needle and configuration checks before marking `DISPATCH_INTENT`. Optionally, add a "not dispatched" reconciliation that is allowed only when the dose record's own valid journal has no Channel 2 infuse `dispatch_started` after the intent. Consequence: the stranding window shrinks to the journal and ledger writes plus `set volume`/`start`. Neither change repeats a dose automatically. |

### Finding 15: other observations

* **Low, Proposed:** the Chemyx serial port opens with no `write_timeout`
  (`chemyx.py:Pump.connect`), so a wedged USB adapter could block a write
  indefinitely. Suggested fix: `write_timeout=self.timeout`.
* **Info:** if the NMR lock cannot be taken, NMR is disabled for the whole run.
  The warning prints *after* the start-up `y`; acquiring the lock before the
  prompt would show it first.
* **Info:** Chemyx positions are software estimates from timed moves plus STOP
  acknowledgement. A stalled pump or front-panel motion is invisible to them.
* **Info:** `RunRecorder` replays the whole journal after each record. A full
  run writes about 3,260 records (1.9 MB), so a STOP is sent about 0.1 to 0.4 s
  late near the end, inside the existing +2 s timing margin.
* **Doc:** each Chemyx HOME belongs to its serial port (`serial:COM4` or
  `serial:COM6`). Switching ports requires `set-home` on both channels; the run
  guide now says so.

## 3. Experiment stop-condition audit

**Execution sequence.** Functions that can raise into the controller are named
on each step. All of them are caught by `run`'s outer handler, which attempts
STOPs while the transports are open and writes `report.json`; `main` returns 1.

1. `scripts/04_si6_fixed_time_run.py` → `si6_fixed_time.main` → `load_settings`:
   preset, machine YAML (COM4/COM6), Arduino YAML, manual YAML. Raises on any
   invalid or mismatched value; no hardware is touched.
2. `run`: new output folder, journal, `DoseGuard` → `check_previous_doses`
   (raises on an unreconciled dose) → one operator `y`.
3. `PositionLedger` (raises if owned; makes pending motion UNKNOWN) → NMR lock
   (a failure disables NMR only) → `OptionalCommandLog`.
4. HOME checks: `plan_home` for both channels (raises if not at HOME or UNKNOWN).
5. Arduino: `controller_session` (port, lock, READY, identity, runtime config) →
   `TrackedNeedle` (raises on an invalid or pending record) → `verify_needle`.
6. Chemyx: `pump_session` (port, HELP) → `Rig.idle` (STOP acknowledgement on
   both channels).
7. `DoseGuard.reserve` → journal `experiment_reserved` → `Rig.needle_to(1)`.
8. Stage 1, `run_stage`: monotonic 48 h deadline; slots every 120 min; no new
   cycle at or after the deadline.
9. Each slot, `Rig.sample`:
   1. withdraw 8 mL (`Rig.move` → `confirm_idle`, `verify_needle`,
      `plan_transfer`, `configure_pump`, `run_safe_metered_move`);
   2. needle HOME;
   3. withdraw 5 mL;
   4. settle 10 s;
   5. NMR (`acquire_bounded` → child `acquire`; every exception becomes an
      `NMR_*` status);
   6. `nmr_observation` event;
   7. `Rig.idle` and `verify_needle(0)`;
   8. needle UP;
   9. infuse 13 mL, withdraw 5 mL, infuse 5 mL;
   10. Channel 1 displacement check.
10. Then `observe`: `observations.jsonl` and `report.json`, both optional.
11. At the deadline, `Rig.dose`: `check_previous_doses` (own record excluded) →
    needle HOME → `DISPATCH_INTENT` → journal intent → `Rig.move` (Channel 2
    infuse 1.86 mL at 1.0 mL/min) → exactly one matched durable completion →
    `dose_confirmed` → `CONFIRMED` → needle UP.
12. Stage 2, `run_stage`: new 6 h clock, slots every 30 min.
13. Final `Rig.idle` and `verify_needle(1)` → `fixed_time_complete` → COMPLETE.
14. `ExitStack` closes the pump, Arduino, log, NMR lock and ledger. A close error
    is now a `teardown_warning`.
15. `finally`: `channel2_dose.json` and `report.json`.
16. Exit code 0 only for COMPLETE.

Classification: **A** = correct stop for physical/reagent uncertainty, **B** =
recorded without ending the run, **C** = bug (unnecessary termination).

**Startup** (all before motion unless noted):

| Condition | Location | Class |
|---|---|---|
| Invalid or incompatible YAML, geometry, calibration, loading; non-finite timing | `load_settings` | A (config; message now specific) |
| Existing output folder | `run` | A |
| Unreconciled prior dose (RESERVED/DISPATCH_INTENT/CONFIRMED/UNREADABLE) | `check_previous_doses` | A (actionable after finding 7) |
| Active pump ledger owner; Arduino port owner | `PositionLedger.acquire`, `PortProcessLock` | A |
| Stale `owner.json`, old failed report, old experiment ID | none | not a blocker (`test_stale_owner_record_and_old_failed_report_*`) |
| Pending or corrupt Chemyx record, displacement not at HOME, identity/port change | `PositionLedger._reconcile`, `plan_home` | A |
| Needle record invalid, pending HOME, calibration change; firmware moving or faulted | `TrackedNeedle.__init__`, `verify_needle` | A |
| Arduino port/READY/identity/runtime-config failure; pump HELP empty; STOP not acknowledged | `controller_session`, `pump_session`, `Rig.idle` | A |
| NMR lock unavailable | `run` | B (NMR disabled for the run) |
| Command log, configuration export unavailable | `OptionalCommandLog`, `optional_write` | B |

**Every cycle and the dose**:

| Condition | Class |
|---|---|
| Any NMR exception, invalid result, timeout, missing data, unwritable raw folder | B |
| Stage/observation exports, `run_state.json` projection | B |
| Needle STATUS error, moving/fault, logical mismatch; pump STOP not acknowledged; echo mismatch | A by design (finding 13 proposes a bounded retry of these non-motion commands) |
| Motion interrupted, completion not durable, ledger displacement mismatch | A |
| Required journal or ledger write failure | A |
| Dose: intent, single matched completion, CONFIRMED write | A; a failure before the intent leaves RESERVED (`unused` possible), after it `retired` only (finding 14) |
| Ctrl+C | INTERRUPTED; STOPs attempted; no reversal |
| Teardown error after COMPLETE | C before the fix (finding 9); now B |

Unexpected termination paths found: findings 1 to 3 (NMR observation loss, never
run termination) and finding 9. No hidden chemical endpoint, analysis import,
filename-time or acquisition-time gate exists in the new runner. The runner
imports `si6_automated_nmr` only for its physical drivers. That module imports
`chemyx_lab.analysis.nmr`, whose NumPy/SciPy/matplotlib/nmrglue imports are lazy
(proven by `test_new_entry_point_loads_without_numerical_processing_dependencies`).

**Potential hangs:** inter-observation `time.sleep`, which is intended and
interruptible; the NMR child, bounded at 430 s (`max_wait + retrieval_wait +
10 × timeout + 10`); Arduino commands, with per-command deadlines and a firmware
watchdog (the session-age cap is removed for the 54-hour session); Chemyx reads,
with a 2 s timeout. **Unbounded:** Chemyx writes (no `write_timeout`) and journal
`fsync` on a failing disk. Machine sleep, USB selective suspend and Windows Update
restarts are outside software control; the guide now gives the power settings.

## 4. NMR acquisition audit

**Original failure mechanism.** The previous production path is
`three_instrument_si6.Services.nmr_measurement` →
`instrument_operations.run_nmr_acquisition`. Its `wait_for_idle` returns on the
first status that does not look running, then writes whatever
`extract_text_payload` finds. That is a JSON dump when there is no text, or an
empty file for an empty reply. Downstream gates then stop the run:
`VerificationError("NMR acquisition returned no nonempty retrieved data file")`,
missing processing output, or LONG DATE ordering. The new runner
(`Rig.sample` → `acquisition` → `acquire_bounded`) never calls any of these.
Existing tests replace `run_nmr_acquisition` and `run_process_fid_postprocessing`
with failures.

**Commands sent.** The configuration and START requests are the ones the legacy
path used on 2026-08-10; the status reads around them are new. GET `iFlow/ExperimentStatus`
(positive idle required), GET `iFlow/Settings/1D` and `iFlow/ExperimentSettings`
(the instrument's own 1D templates, experiment not overridden), PUT `Settings/1D`
(`ReceiverGain` 12, `AutoGain` false, `ExportFilename`), status recheck, then one
PUT `iFlow/RunExperiment` (`NumberOfScans` **8**, `ReceiverGain` 12,
`SpectralCentreInPpm` 5.0, `SpectralWidthInPpm` 20.0, `ExportFilename`). After that
it polls every 2 s up to 300 s, then retrieves for up to 20 s. Asserted in
`assert_production_request` and in the real-child test.

**Retrieval.** Inline TD from the completion reply is the primary path.
`ExportFilename` is interpreted on the NMR computer (169.254.30.54), not on WORK,
so a local export exists only if that path is shared; it is accepted only when its
header declares an FID. Raw files are named `nmr_raw/<stage>_<NNNN>.dx` with
`.request.json` and `.result.json` beside them. An existing observation file
blocks START and is preserved. A partial or foreign export is preserved and the
returned TD is saved beside it as `.rpc.dx`. The before and last status evidence
is kept without the spectra.

**Remaining ways a real acquisition could leave no saved file** (only real hardware can tell):

1. The instrument's idle reply has neither `ResultCode` 0 nor boolean running
   flags, so START is skipped every time (kept fail-closed by design).
2. The START reply's `TimeStamp` text differs in format from `OriginalReceipt`'s.
3. The instrument writes the data only to its own disk and returns no inline
   JCAMP (`JDX_Filename` is recorded).
4. An 8-scan acquisition longer than 300 s. On 2026-08-10, 8 scans took about
   81 s end to end.

`--nmr-check` exercises all four on WORK in about 2 minutes, without reagent.

**Simulated versus real.** Verified here: request contents, single START, polling
and receipt logic over documented status shapes, inline/FD/export retrieval,
byte-exact saving, child IPC (no `Path` or other non-JSON value crosses it), the
outer timeout and kill, and failure isolation. **Not verified anywhere yet:** the
real NMReady status codes, receipt formats, where the data appears in the reply,
and acquisition duration under the production settings.

## 5. Manual instrument control audit

**Arduino** (`arduino/scripts/needle_control.py`, `arduino/python/manual_jog.py`):

* `up` and `down` jog ±1 × 200 STEP pulses (`up_step_sign` +1).
* Before HOME, the count is persisted with `position_valid=false`, so it can
  never authorize automation. Proven across five separate processes:
  `+1, +2, +1, 0, -1`.
* `set-home` sends no JOG and saves logical 0 as `operator_confirmed_home`, with
  a pending-marker readback.
* A calibrated `up` and the automated HOME/DOWN=0, UP=1 share one record
  (`runs/arduino/needle_state.json`, resolved to the repository).
* Bounds -3 to +5, the 200,000-step and 120 s limits, and STOP are unchanged.
* An interrupted jog stays `manual_relative_pending` or
  `motion_failed_or_interrupted`, never success. The unhomed branch checks the
  DONE count, the final counter, idle and fault.
* After an uncertain record, the documented `stop` → `set-home` → jog sequence
  works (`test_uncertain_needle_record_recovers_*`). If the needle is not yet at
  the true HOME, `set-home` at its current stopped position restores jogging as
  a temporary reference. Watch physical travel, because the -3 to +5 bounds are
  then relative to that point. Run a final `set-home` at the verified sampling
  HOME; automation uses only the latest reference.

**Chemyx** (`scripts/chemyx_control.py`):

* Manual defaults are 1.0 mL at 10.0 mL/min per channel and do not touch the
  fixed preset (dose 1.86 mL at 1.0 mL/min; sampling 5 mL/min).
* Every command is channel-addressed (`<ch> set …`, `<ch> stop`), and the default
  is restored after each move.
* Channel 2 infusion requires `--allow-channel2-infusion`, is refused by any
  RESERVED or uncertain dose record, and prints a waste-routing note.
* Intent is recorded before dispatch. Position advances only after the timed
  move, a positively acknowledged STOP and a durable completion; anything else
  is UNKNOWN until `set-home`.
* HOME identity includes the port, so a COM change never silently reuses a HOME.
* Startup requires Channel 1 at 0 mL and Channel 2 at its 5.00 mL HOME. After a
  run, Channel 2 reads -1.86 mL and its HOME return withdraws through the
  reagent line, now stated in the blocker message.

## 6. Recovery audit

| Previous state | New run starts? | Should it? | Blocker | Physical action | Command |
|---|---|---|---|---|---|
| Failed before the first NMR acquisition | No | No (the reaction may continue) | RESERVED dose | Confirm the old process stopped; no reagent delivered | Printed `reconcile-dose … --resolution unused` (with the journal when found) |
| Failed after sampling, before cleanup | No | No | Channel 1 displacement ≠ 0, or UNKNOWN | Inspect lines; restore the plunger to HOME | Printed `home` or `set-home` (with `--machine-config`) |
| Failed before Channel 2 dosing | No | No | RESERVED | As row 1 | `unused` |
| Reserved, never dispatched | No | No | RESERVED | As row 1 | `unused` (journal optional) |
| Dose issued, confirmation lost | No | No | DISPATCH_INTENT | Retire the old reactor; service Channel 2 | `--resolution retired`; `unused` refused |
| Dose completed, then crash | No | No | CONFIRMED; Channel 2 at -1.86 mL | Retire the old reactor; reload 5.00 mL | `retired`, then `set-home --channel 2` |
| Crash left an owner record | Yes | Yes | none (only a live OS lock blocks) | none | none |
| Arduino move interrupted | No | No | needle record invalid | Inspect; `stop` if a STOP was unresolved | `needle_control.py set-home` |
| Chemyx move interrupted | No | No | UNKNOWN on next ledger open | Place the plunger at HOME | `chemyx_control.py set-home` |
| Old results and dose records | Yes, for results | Yes | unreconciled dose records only | per status | per status |

All rows were exercised through the real `--live` startup branch with only the
transports faked (`test_previous_dose_*`, `test_unreadable_*`, `test_stale_owner_*`,
`test_run_that_stopped_mid_cycle_*`, `test_completed_previous_dose_*`,
`test_interrupted_pump_move_*`, `test_interrupted_needle_move_*`). Every blocked
start sent no pump transfer and no needle move. No recovery command moves a
plunger or invents completion, and original dose evidence is preserved. A fresh
run always restarts both stage clocks; there is no resume of a partly finished
reaction (findings 13 and 14).

## 7. Offline package assessment

The previous package is internally sound but stale (finding 12). The procedure
preserved WORK's `runtime`, `runs` and `results` and did not use `/MIR`, but its
verification could abort on configs missing from WORK (finding 11, fixed in the
guide). WORK's existing YAML is preserved and never overwritten. Incompatible
older YAML fails closed at `--validate-config`, with the values that differ.
PowerShell syntax was reviewed:

* The checksum comparison is case-insensitive.
* The single-quoted here-string has `'@` at column 0.
* `-Filter '*.zip'` does not match the `.sha256` sidecar.
* `Expand-Archive` is safe because the archive has no wrapper folder.

The regenerated package and its independent verification are in section 8. Once
on WORK, the verified order is:

1. `--validate-config`
2. the 2-scan checkout
3. `--nmr-check` (must report `NMR_SAVED`)
4. `--live`

## 8. Tests and evidence

Environment: `C:\Users\iyer95\miniconda3\envs\ai\python.exe` (Python 3.11), `-p no:cacheprovider`,
basetemp in the session scratch directory.

| Command (repository root) | Result |
|---|---|
| Baseline, original code: `pytest tests/test_si6_fixed_time.py tests/test_si6_fixed_time_independent_review.py` | 69 passed |
| Baseline, original code: `pytest tests/test_chemyx_manual_control.py tests/test_simple_instrument_checkout.py` | 36 passed |
| Baseline, original code: `pytest arduino/tests/test_tracked_manual_relative.py arduino/tests/test_manual_jog.py` | 12 passed |
| New audit tests against the original code: `pytest tests/test_si6_fixed_time_new_audit.py` (NMR part) | **8 failed, 13 passed**: findings 1 to 5 reproduced; retained protections passed |
| Final code: `pytest tests/test_si6_fixed_time.py tests/test_si6_fixed_time_independent_review.py tests/test_si6_fixed_time_new_audit.py arduino/tests/test_tracked_manual_relative.py tests/test_si6_automated_nmr.py tests/test_release_inventory_hardening.py arduino/tests/test_manual_jog.py tests/test_config.py tests/test_chemyx_manual_control.py tests/test_simple_instrument_checkout.py` | **172 passed** (the previous 99 + 36, plus 37 new) |
| After fixes: `pytest arduino/tests` (full Arduino suite) | 128 passed |

Accelerated full 48 h / 6 h runs with the real mock pump and Arduino drivers
(scratch evidence, `full_schedule_runs.py` and the CLI):

| Run | NMR outcomes | Longest cycle | Raw files |
|---|---|---|---|
| CLI `--mock`, final code (fixture acquisition) | 36 saved | 482 s | 36 |
| A: all NMR failed (alternating exceptions and failed results) | 36 failed | 452 s | 0 |
| B: intermittent (saved, missing, failed, raised, malformed) | 12 saved, 6 missing, 18 failed | 452 s | 12 |
| C: every observation through the real `acquire()`, scripted NMReady replies (ResultCode 2 at 0 scans, START lag, read timeout and empty reply, late raw, never-finishing) | 29 saved, byte-exact; 7 failed = the scripted never-finishing acquisition, bounded at 300 s | 752 s | 29 |

Every run had:

* status COMPLETE;
* stage windows of 172,800 s and 21,600 s;
* 24 + 12 observations, scheduled at 0 to 46 h every 2 h and 0 to 5.5 h every 0.5 h;
* exactly one completed Channel 2 infusion (1.86 mL at 1.0 mL/min), with the dose
  record CONFIRMED and 3.14 mL retained;
* Channel 1 KNOWN at 0 mL and Channel 2 KNOWN at -1.86 mL;
* the needle valid at UP=1;
* zero uncertain lifecycle records.

Run C also asserted exactly one START per acquisition. Its longest cycle,
including the 300 s bounded wait, stays inside the 30-minute Stage 2 interval,
so no slot was skipped. `--validate-config` passes on the final code with 8 scans.

The previous review's cited runs were re-analyzed and match their claims:
`results/fixed_time/mock/SI6_FIXED_20261016T220711Z_b74dc4b73af2` (36 saved) and
`…/SI6_FIXED_ALL_NMR_FAILED_REVIEW_fed7e60d3d2e` (36 failed). Both have stage
windows of 172,800 s and 21,600 s, one 1.86 mL dose, Channel 1 back to 0 and the
needle at UP. Neither run exercised `acquire()`: one copied a fixture and the
other injected failures.

**Regenerated WORK package.** After every change above, including this report:
`python scripts/prepare_si6_release.py --working-tree --with-offline-payload --output-dir dist/si6_fixed_time_handoff_audit`.
That folder holds the ZIP, its `.sha256` sidecar, its manifest, and
`audit_package_verification.json`. The verification checks:

* every archived file is byte-identical to the audited working tree;
* the archive has a flat layout, no entries under robocopy-excluded names, and
  paths under 260 characters at the documented staging folder;
* the new files and changed modules are present;
* `--validate-config` passes from the extracted copy;
* `offline/verify_bundle.py` passes from the extracted copy.

**Files changed by this audit:**

* `chemyx_lab/workflows/si6_fixed_time_nmr.py`: findings 1 to 5.
* `chemyx_lab/workflows/si6_fixed_time.py`: findings 6 to 10.
* `chemyx_lab/workflows/dose_recovery.py`: finding 7.
* `chemyx_lab/workflows/chemyx_manual.py`: finding 7, blocking record.
* `docs/03_fixed_time_experiment/01_run_guide.md`
* `docs/03_fixed_time_experiment/04_offline_deployment.md`
* `offline/release_manifest.yaml`: required resources.
* New: `tests/test_si6_fixed_time_new_audit.py` (37 tests) and this report.

No change touches stage durations, intervals, scan count, the dose volume or
rate, the 5 mL loading, syringe geometry, sampling volumes or rates, motion
commands, travel limits, or dose-guard transitions.

## 9. Final recommendation

**READY FOR WORK CHECKOUT**, with the regenerated `dist/si6_fixed_time_handoff_audit`
package, not the earlier one. No critical software defect remains in the
reviewed paths after the fixes above. Real-instrument verification is still
required, in this order:

1. Deploy with the corrected procedure.
2. Run `--validate-config`, and resolve any reported YAML mismatch physically.
3. Run the 2-scan checkout.
4. Run `--nmr-check`; it must report `NMR_SAVED`. If not, keep `nmr_check.json`
   and do not start the run.
5. Clear the old `SI6_REAL_TWO_STAGE_001` dose record using the exact command the
   blocker prints.
6. Set the WORK power plan as the guide says.
7. Launch `--live`.

Before an unattended 54-hour run, decide on proposals 13 (non-motion retry) and
14 (dose-window ordering). Both lower the chance that one transient serial
glitch ends the reaction.
