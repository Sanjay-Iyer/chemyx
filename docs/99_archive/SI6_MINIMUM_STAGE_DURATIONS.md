> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../07_other_experiments/02_legacy_run_guide.md) and [errors/fixes](../07_other_experiments/07_legacy_errors_and_fixes.md) at the instruments.

# Mandatory stage durations

> Historical policy: the current real recipe now runs Stage 1 for a fixed 48 hours
> and explicitly authorizes its protected dose at that time endpoint. Stage 2
> keeps its 2-hour minimum and 6-hour maximum. See
> [the current time-driven protocol](../07_other_experiments/04_legacy_timing.md). The 40-hour
> Stage 1 minimum and ordinary-timeout refusal below describe the previous policy.

The real recipe, `configs/experiments/si6_real_two_stage.yaml`, uses these
operator-editable keys. No Python edit is needed:

```yaml
workflow:
  initial_stage:
    min_completion_hours: 40.0
    max_hours: 48
    interval_minutes: 120
  first_addition_stage:
    min_completion_hours: 2.0
    max_hours: 6
    interval_minutes: 30
```

These are excerpts, not replacement stage blocks: retain the existing completion
criteria and boundary actions. Minimum must be finite, nonnegative, and strictly
less than the finite positive maximum. Validation occurs before transport setup.
Acquisition intervals, ACME, area integration, peak tracking, solvent thresholds,
four-observation confirmation, syringe volumes and reagent quantities are unchanged.

Every sampling cycle still runs from stage startup. Raw acquisitions, processing,
tracking, solvent checks and QC are saved before and after the minimum. Stage 1
normalizes to its original eligible reference area; Stage 2 retains its original
reference and growth history. Earlier data are never discarded or reprocessed
just because they cannot count toward the confirmation window.

The timing boundary uses **actual monotonic NMR acquisition start minus that
stage's monotonic monitoring start**. Exactly at the minimum qualifies; just
before it does not, even if processing finishes later. A fresh consecutive
confirmation window begins at the threshold. Stage 2's first eligible observation
cannot gain a confirmation from an adjacent pre-threshold observation.

With ideal acquisition times, Stage 1's four possible first confirmations are
40, 42, 44 and 46 hours. Stage 2's are 2, 2.5, 3 and 3.5 hours, provided growth
and the existing stable-area rule hold. Actual sampling transfers precede the
NMR command, so acquisition starts and the eventual decision/cleanup can occur
later than these nominal times. Stage 2 has its own clock after the confirmed
reagent transition; it does not inherit Stage 1 elapsed time.

`stage_elapsed_hours` and `stage_elapsed_source` record this operational clock.
They are separate from `acquired_at` / `elapsed_hours`, which remain JCAMP LONG
DATE acquisition metadata for scientific time plots. There is no filename or
file-modification-time fallback. Journals record minimum, maximum, clock elapsed,
eligibility state and the qualifying confirmation count. Configuration snapshots,
`metadata/experiment_info.json` (`stage_timing`), stage summaries and final QC
retain the effective timing settings.

Stages with `min_completion_hours` are governed by their **maximum duration**.
Do not add `completion.max_iterations` or `max_measurements`: conflicting caps
are rejected. The scheduler derives 24 Stage 1 slots (nominal starts 0–46 h) and
12 Stage 2 slots (0–5.5 h), then waits to the deadline if chemistry is incomplete.
The old 20-observation exit at about 38 hours no longer applies to the real recipe.
Historical recipes that omit the new key and retain `completion.max_iterations`
keep their previous count-limited behavior. No 40-hour/2-hour minimum is silently
imposed on those recipes.

No sampling cycle starts at or after a stage's deadline. If an already-active
transfer reaches the deadline before the NMR command, the command is skipped and
verified return/cleanup completes before a time-limit decision. No artificial
missing-solvent measurement is created for this skip. A genuine acquisition
started before the deadline may finish, be analyzed and cleaned up afterward;
the existing policy permits a valid endpoint from that acquisition. Thus the
deadline does not abort active physical transfers. Any cleanup or communication
failure still follows the existing safety-stop policy.

Ordinary expiration is `runtime_limit_reached`, with chemistry unverified:
Stage 1 does **not** automatically add Channel 2 or enter Stage 2. A verified
chemical endpoint uses the existing durable protected transition. Separately,
three missing-solvent observations still latch the existing timed fallback,
now using 48 hours / **6 hours**. That fallback still authorizes the Stage 1
reagent transition despite unverified chemistry; this remains an explicit
chemical-protocol approval decision. No dosing policy or replay protection changed.

The timing gate prevents early authorization; it **does not validate noisy
nondetections, eliminate the live zero-area nondetection behavior, or add a
near-maximum/minimum-stability rule**. Four misleading post-threshold low areas
or a qualifying temporary plateau may still authorize an endpoint. HOME tests
must report that scientific limitation separately from timing-gate success.
No claim of full unattended scientific acceptance or hardware commissioning
follows from this change alone.
