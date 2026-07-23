# Si6 Automated Chemyx/NMR Workflow

The Si6 workflow is configured in:

```text
configs/experiments/02_si6_automated_nmr.yaml
```

It automates pump motion, timed NMR acquisition, JCAMP-DX processing, peak
tracking, stopping decisions, CSV output, and plots. The current rig does not
contain a needle-position or reagent-addition actuator, so lowering/raising the
needle and adding reagents are interactive operator checkpoints.

> **Experimental, attended-only workflow:** the operation journal provides
> durable evidence and conservative recovery classification. Automatic or
> operator-approved physical resume is not implemented. Fixed-timeline
> scheduling and independent physical-state verification are also absent. This
> workflow is not qualified for unattended operation.

## Safe validation

On the development/simulation laptop:

```powershell
conda run -n ai python scripts\02_si6_automated_nmr.py --validate-only
```

This parses and validates the plan without creating a result directory or
contacting either instrument. `--dry-run` also contacts no hardware, but it now
creates a timestamped journal-backed run directory so persistence and recovery
inspection can be exercised offline. Run real hardware only from the instrument
laptop after reviewing the plan and local machine config.

## Adjustable settings

Edit `02_si6_automated_nmr.yaml`:

- `interval_minutes`: fixed start-to-start measurement spacing.
- `measure_immediately`: whether measurement 1 is scheduled at stage start.
- `plateau_stopping_enabled`: selects fixed-count or plateau-or-limit mode.
- `max_measurements`: number of scheduled measurement slots.
- `max_hours`: independent hard runtime ceiling.
- `repeat_addition_rounds`: number of acetone/diphenyl-silane pairs.
- `pump.syringe_capacity_ml`: physical syringe capacity; required.
- `pump.initial_retained_volume_ml`: volume already retained before the cycle.
- `pump.syringe_safety_margin_ml`: capacity that automation may not use.
- `nmr.scans`: NMR scans per timepoint. Increase the cycle pause if needed.
- `nmr.target_ppm`: tracked peak, currently 6.1 ppm.
- `analysis.min_peak_snr`, `min_prominence_snr`, and `min_peak_area`: when a peak is considered clear.
- `analysis.plateau_max_growth_percent`: maximum permitted growth, currently 5%.
- `analysis.plateau_max_decline_percent`: maximum permitted decline, currently 2%.
- `analysis.plateau_consecutive_intervals`: consecutive low-growth intervals required, currently 3.

Plateau stopping is evaluated separately within each reaction stage. With the
default value of three intervals, at least four consecutive valid timepoints
are needed. Every timepoint in that window must have a detected peak inside the
configured ppm tolerance and pass SNR, prominence-SNR, minimum-area, finite-area,
and numerical-epsilon checks. Each interval must satisfy `-2% <= area change <=
+5%`. Missing, invalid, nonfinite, or low-quality spectra break the consecutive
window; a large decline never counts as plateau. Receiver auto-gain is
intentionally disabled and rejected by validation because changing gain would
make peak areas less comparable over time.

## Monitoring modes and scheduling

Each active stage has an explicit policy. `plateau_stopping_enabled: false`
means fixed scheduled-count monitoring. Plateau metrics and detections are still
calculated, plotted, and journaled, but a detected plateau is marked
`detected_but_ignored` and cannot stop the stage. Successful completion after
all slots is `scheduled_monitoring_completed`.

`plateau_stopping_enabled: true` means plateau-or-limit monitoring. A verified
plateau ends the stage as `plateau_reached`. Reaching `max_measurements` without
plateau is the non-success outcome `plateau_not_reached_within_limit`; later
chemistry is not authorized. Reaching `max_hours` is separately reported as
`maximum_duration_reached` with stage outcome `runtime_limit_reached`.

The active initial stage is deliberately configured as:

```yaml
interval_minutes: 60
measure_immediately: false
plateau_stopping_enabled: false
max_measurements: 24
max_hours: 26
```

Its fixed deadlines are stage start + 1 hour through stage start + 24 hours.
The 24th physical cycle may finish after the 24-hour boundary. The 26-hour
ceiling allows pump, equilibration, NMR, analysis, and modest operator latency,
but remains a hard safety limit.

Addition stages use six scheduled 15-minute slots, do not measure immediately,
enable plateau stopping, and use a two-hour hard ceiling. Their last scheduled
slot is at 90 minutes; the extra ceiling time does not authorize extra slots.

Scheduling uses monotonic fixed start-to-start deadlines:

```text
scheduled time N = stage start + interval * (N - 1)  # immediate
scheduled time N = stage start + interval * N        # not immediate
```

Cycle duration therefore does not accumulate into later deadlines. If work
overruns a deadline, the next cycle is late and its nonnegative scheduling delay
is recorded; deadlines are never silently shifted.

A slot is consumed when `measurement_started` is durably journaled, immediately
before the physical sample cycle. This increments both
`scheduled_measurement_number` and `acquisition_attempt_number`. A successfully
processed spectrum increments `valid_analysis_count`, even if peak-quality
metrics do not qualify it for plateau. An acquisition or processing exception
consumes the attempted slot but terminates under its instrument or analysis
failure status; it cannot produce successful fixed-count completion.

Capacity validation simulates two complete cycle repetitions before any
hardware adapter is initialized. The cycle must return to its initial retained
volume, must never infuse more than it contains, and must satisfy:

```text
maximum cumulative retained volume + safety margin <= syringe capacity
```

For the current sequence, the maximum cumulative retained volume is 13 mL
(8 mL followed by 5 mL before the 13 mL infusion). Missing capacity fails
closed.

## Failure and interruption behavior

Every pump transfer and every workflow exit performs a best-effort `stop`
command before the serial connection closes. This includes normal exceptions,
operator abort, analysis/NMR failure, `Ctrl+C`, `KeyboardInterrupt`, and
`SystemExit`. Stop success, failure, or missing confirmation is included in the
run summary.

If execution is interrupted after a pump command may have started, or a stop
cannot be confirmed, the run ends with `safety_stop`. No return infusion,
retry, reagent addition, or later transfer is started. The operator must inspect
the pump, syringe, tubing, needle, and reaction manually outside the automatic
run.

Reaching a stage's maximum duration is `maximum_duration_reached`, not reaction
completion. The workflow terminates and does not prompt for or start the next
reagent-addition stage. Only a verified `completed` stage authorizes the next
configured chemistry stage.

## Terminal statuses and process exit codes

| Status | Exit code | Meaning |
|---|---:|---|
| `completed` | 0 | Every configured stage met the validated plateau criterion. |
| `validation_failure` | 2 | Configuration/preflight validation failed before hardware contact. |
| `operator_aborted` | 3 | Operator declined, terminal was noninteractive, or interruption occurred outside uncertain motion. |
| `maximum_duration_reached` | 4 | Stage duration expired without completion; no next chemistry stage starts. |
| `instrument_failure` | 5 | Pump/NMR failure with no detected uncertain active transfer. |
| `safety_stop` | 6 | Physical pump/transfer state is uncertain; manual recovery is required. |
| `analysis_inconclusive` | 7 | Analysis could not support a decision. |
| `unexpected_failure` | 8 | Unclassified software failure. |
| `plateau_not_reached_within_limit` | 9 | A plateau-required stage exhausted its slots; no later chemistry starts. |

## Per-run output

Every real run creates exactly one folder such as:

```text
results/runs/si6/20260722_140506_si6/
  config_snapshot.json
  operation_journal.jsonl
  run_state.json
  manifest.json
  operations.csv
  time_series.csv
  spectra_long.csv
  raw_nmr/
  plots/
```

`time_series.csv` contains one row per acquisition, including peak area, height,
SNR, prominence SNR, growth percentage, clarity, plateau decision, stage start,
scheduled time, actual cycle/NMR/analysis timestamps, schedule delay, scheduled
measurement number, acquisition-attempt number, and valid-analysis index.
`spectra_long.csv` contains the point-wise ppm and magnitude values for every
iteration. The plots folder contains per-spectrum peak-review plots plus:

- peak area versus elapsed time;
- signal-to-noise ratio versus elapsed time;
- percent peak-area growth versus elapsed time;
- normalized target-region spectral overlays.

Raw `.dx` files stay in the same timestamped run folder under `raw_nmr/`.

## Authoritative operation journal

`operation_journal.jsonl` is the source of truth for run state. Each line is a
complete JSON object using journal schema version 1. Records contain a run ID,
strict sequence number, unique event ID, UTC timestamp, monotonic elapsed time,
workflow phase, operation identifiers, lifecycle/result data, physical-state
certainty, compact command parameters, and sanitized errors. Spectrum arrays
are never embedded; records refer to raw files by run-relative path.

Physical operations use this lifecycle:

1. `planned` is serialized, appended, flushed, and fsynced.
2. `dispatch_started` is serialized, appended, flushed, and fsynced.
3. Only then may the adapter be called.
4. `completed` is written only after positive completion confirmation.
5. `failed` is used only when motion can be proven not to have started.
6. `uncertain` is used whenever dispatch may have occurred but durable,
   positive completion evidence is unavailable. `skipped` is available for a
   planned operation deliberately not executed.

A replayed `planned` operation without `dispatch_started` is a clean
nonphysical interruption. A `dispatch_started` pump operation without a later
`completed`, safely classified `failed`, or `skipped` record is uncertain and
requires manual inspection. This intentionally permits false uncertainty and
does not permit false certainty.

Pump stops are journaled with the same lifecycle. Emergency stop is the one
safety-prioritized exception: if its write-ahead journal record fails, the code
still attempts `pump.stop()` and retains the persistence failure in diagnostics.
Required cleanup is attempted before the authoritative terminal event. Once a
terminal event is durable, the writer prohibits another normal or physical
dispatch.

### Durability boundary

Each record is fully serialized before a single newline-terminated byte payload
is written. The file is flushed and `os.fsync()` is called before the sequence
is reported durable. A failed write does not advance the in-memory sequence.
Journaling failure before physical dispatch prevents the adapter call.
Journaling failure after motion may have begun makes the pump state uncertain,
attempts a stop, and prohibits compensating motion.

These guarantees are limited by Python, Windows, the mounted filesystem, and
the storage device. `fsync()` requests that Windows flush the opened file, but
cannot prove that a device with volatile write caching survived sudden power
loss. A single JSONL append is not claimed to be sector-atomic. The parent
directory is not fsynced because portable directory handles with POSIX fsync
semantics are not available here. The journal also cannot prove the physical
controller or plunger state after a cable, controller, or power failure.

## Derived state and atomic replacement

`run_state.json` is a compact, rebuildable projection. It includes the last
applied sequence, phase, terminal status, active and last operations, physical
certainty, retained-volume estimate, completed cycle count, plateau progress,
last valid analysis, and the manual-inspection flag.

The corresponding journal event is always made durable first. The state JSON is
then written to a uniquely named temporary file in the run directory, flushed,
fsynced, and installed with `os.replace()`. A stale, missing, ahead-of-journal,
or corrupt state file never overrides the journal.

The manifest is also a rebuildable projection and is replaced atomically. CSV
and raw-spectrum transactional refactoring remains outside this phase.

## Offline recovery inspection

Inspection branches before workflow configuration loading and pump/NMR
construction:

```powershell
conda run -n ai python -B scripts\02_si6_automated_nmr.py --inspect-run results\runs\si6\RUN_ID
```

Inspection is read-only by default. To explicitly rebuild only the derived
snapshot from a valid journal:

```powershell
conda run -n ai python -B scripts\02_si6_automated_nmr.py --inspect-run results\runs\si6\RUN_ID --rebuild-state
```

| Classification | Exit code | Meaning |
|---|---:|---|
| `terminal_completed` | 0 | Journal has a valid successful terminal event. |
| `terminal_noncompletion` | 10 | Journal ended in timeout, abort, instrument/analysis failure, or another non-success terminal status. |
| `clean_nonphysical_interruption` | 11 | No terminal event, but no physical dispatch is unresolved. It is only a possible candidate for a future resume design. |
| `physical_state_uncertain` | 12 | A dispatched pump operation is unresolved or explicitly uncertain. |
| `journal_corrupt` | 13 | Schema, JSON, identity, ordering, transition, or terminal-barrier validation failed. |
| `legacy_run_without_journal` | 14 | Historical directory lacks authoritative journal evidence. |
| `manual_inspection_required` | 15 | State is not classified physically uncertain but recorded conditions still require operator inspection. |

Replay validates schema version, consistent run ID, consecutive sequence
numbers, unique event IDs, legal lifecycle transitions, the terminal barrier,
and unresolved operations. It never imports or constructs an instrument adapter
and writes only when `--rebuild-state` is explicitly supplied.

A malformed middle record makes the journal corrupt. A partial trailing record
or a complete final record missing its required newline is reported explicitly.
Earlier complete records—including a completed terminal record—remain visible;
the original journal is never silently repaired or rewritten. Corruption plus
any ambiguous dispatched pump operation remains a manual physical-inspection
case even though the top-level classification is `journal_corrupt`.

Legacy directories remain readable as historical data but are classified
`legacy_run_without_journal`. CSV files, plots, and old manifests are not used
to infer certainty and cannot make a legacy run resumable.

## Example event sequences

Successful withdrawal:

```text
withdraw planned -> withdraw dispatch_started
-> pump_stop planned -> pump_stop dispatch_started -> pump_stop completed
-> withdraw completed
```

Interrupted withdrawal:

```text
withdraw planned -> withdraw dispatch_started -> withdraw uncertain
-> pump_stop planned -> pump_stop dispatch_started -> pump_stop completed
-> terminal safety_stop
```

Operator abort:

```text
operator_checkpoint requested -> operator_checkpoint aborted
-> pump_stop planned -> pump_stop dispatch_started -> pump_stop completed
-> terminal operator_aborted
```

Successful terminal completion:

```text
analysis_result valid -> completion_decision completed
-> final pump_stop completed -> terminal completed
```

## Resume remains disabled

Inspection never executes a transfer and there is no `--resume` option. Even a
`clean_nonphysical_interruption` is described only as a possible future resume
candidate. Manual reconciliation of the pump, syringe, tubing, needle, sample,
reagents, and controller state remains mandatory after uncertainty. A later
resume phase must require an exclusive run lock, explicit operator approval,
physical reconciliation evidence, deterministic plan matching, and refusal of
every unresolved irreversible operation.
