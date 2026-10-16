# Si6 time-driven proof of concept

The real recipe is `configs/experiments/si6_real_two_stage.yaml`. One startup
`y` starts the existing automatic sampling sequence, followed by one protected
Channel 2 addition and Stage 2. There are no intermediate prompts.

These are the editable timing settings, in the existing workflow structure:

```yaml
workflow:
  initial_stage:
    control_mode: fixed_duration
    # Accepted protocol choice: add reagent at the time endpoint even when
    # NMR does not verify chemical completion.
    advance_at_duration: true
    interval_minutes: 120
    max_hours: 48
    measure_immediately: true
    plateau_stopping_enabled: true  # retain calculation; control_mode makes it advisory
    completion:
      method: area_only
      trend: decreasing
      near_zero_fraction: 0.025
      consecutive_iterations: 4
      epsilon: 1.0e-12
  first_addition_stage:
    control_mode: minimum_then_nmr
    interval_minutes: 30
    min_completion_hours: 2.0
    max_hours: 6
    measure_immediately: true
    plateau_stopping_enabled: true
    completion:
      method: area_only
      trend: increasing
      minimum_growth:
        enabled: true
        fraction_of_initial: 0.25
      relative_change_threshold: 0.02
      consecutive_iterations: 4
      epsilon: 1.0e-12
```

This excerpt omits names, prompts, and existing boundary actions; edit the
complete recipe rather than replacing it with the excerpt. Keep the existing
Channel 2 infusion at 1.8 mL and 1.0 mL/min, with its existing HOME/UP sequence.
There is one duration source per stage: `max_hours`. Do not add observation caps
or a Stage 1 minimum. The validator rejects conflicting policies. Both new modes
are explicitly selected together; recipes that omit them keep their former
NMR/iteration-limit/solvent-fallback behavior.

Stage 1 acquires throughout its 48 hours. A calculated chemical endpoint at
8, 40, or 46 hours is saved as advisory evidence and cannot end the stage or
dispatch reagent. The scheduler returns `time_limit_completed` with successful
operational status at the duration endpoint. An already-started physical cycle
finishes its existing return, wash and idle verification first. Thus the dose
may occur later than exactly 48 hours when an active cycle overruns that time.
No new acquisition starts at or beyond the stage deadline.

That time endpoint, together with the explicit `advance_at_duration: true`,
authorizes the existing protected transition. The controller records separate
`chemical_completion_verified` evidence in `stage_transition_authorized`.
It durably writes dose `DISPATCH_INTENT`, performs the unchanged infusion,
verifies the existing completion evidence, and writes `CONFIRMED`. Only then
does Stage 2 start its own monotonic clock. A reservation or uncertain dispatch
cannot be automatically replayed after restart. Time completion never means
that chemistry or an unconfirmed physical dose succeeded.

Stage 2 collects from startup but only actual acquisitions starting at or after
two elapsed hours count toward its four-point chemical confirmation. Original
baseline, growth and peak identity remain available. Four such observations at
nominal 2, 2.5, 3 and 3.5 hours give an earliest nominal endpoint of 3.5 hours;
actual sampling preparation and cleanup add overhead. Without qualifying
evidence it completes at six hours through the normal cleanup path, with no
extra dose. Missing solvent still disables chemical completion after three
misses; both stages retain their original deadlines.

Scientific failures in this explicitly selected protocol are handled narrowly:

- Undetected targets and invalid/nonfinite/negative target areas are unavailable
  (`peak_area: null`, CSV blank) and excluded from chemical completion. A
  nondetection is never treated as measured zero.
- Numerical processing, ACME, retained spectral evidence, integration and
  completion-evaluation errors are caught only at scientific function boundaries.
  After the existing cleanup preconditions and full cleanup succeed, an
  unavailable row, raw acquisition path/hash, metadata and diagnostic JSON are
  persisted, and the next scheduled cycle continues.
- Missing/empty/malformed retrieved sample data retains the existing solvent
  evidence and fallback records. QC warnings remain advisory. Optional final
  reporting remains retrospective and cannot change the established outcome.

Acquisition/RPC communication errors, uncertain pump/needle execution, failed
cleanup/STOP, journal or storage failures, unexpected programming exceptions,
operator interruption and loss of power still interrupt the run. These are not
classified as recoverable numerical failures. No physical execution is invented.
The software does not resume a partially run physical experiment automatically.

ACME, FFT, peak detection, integration, solvent thresholds, sampling movements,
pump commands and standalone HOME utilities are unchanged. Existing result
directories are unchanged. Stage summaries, configuration snapshots, journal
decisions, final QC and dose records include the effective time policy and
separate chemical evidence. Scientific plot times still use JCAMP LONG DATE;
the operational stage deadlines use monotonic time, never filenames or mtime.

The previous minimum-duration policy and its limitations are documented in
[SI6_MINIMUM_STAGE_DURATIONS.md](SI6_MINIMUM_STAGE_DURATIONS.md); its Stage 1
40-hour gate is historical and is no longer selected by the real recipe.

Offline validation and detailed timelines are saved under
`results/timed_progression_validation/`. All instruments are mocked, clocks
are virtual, and serial/network/operator-input calls are prohibited by the
full-workflow harness. No commit or push is part of this change.
