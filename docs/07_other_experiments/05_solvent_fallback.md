> [Documentation home](../00_START_HERE.md) | [Other and older experiment workflows](00_START_HERE.md)

> Other/older workflow. For the full 48-hour / 6-hour run use the [fixed-time experiment](../03_fixed_time_experiment/00_START_HERE.md). Older Stage 2 endpoint rules can finish before 6 hours.

# Si6 startup, needle positions and solvent-loss fallback

**Current real-recipe timing:** the [time-driven update](04_legacy_timing.md)
sets Stage 1 to a fixed 48 hours with explicit time-based dose authorization,
and Stage 2 to minimum/maximum 2/6 hours, with 120/30-minute schedules.
Solvent-loss evidence still disables chemical completion and preserves those
deadlines. Stage 1 no longer depends on either chemical completion or a solvent
fallback latch to advance. Its chemical evidence is advisory. The earlier
40-hour gate and four-hour Stage 2 examples below retain their historical scope.

The October 14 update uses one startup `y`, the existing saved Arduino HOME
reference, and an independent solvent-presence check before chemical endpoints.
The implementation and all validation on HOME use mocks/offline data only.

The subsequent [HOME recovery update](../01_arduino/02_home_recovery.md) makes the
existing `home` command sufficient for both known-position movement and a single
operator-confirmed manual reference when tracking is UNKNOWN. `set-home` remains
available; it is not an additional prerequisite before every experiment.

New main experiments use [the dated output layout](06_legacy_outputs.md):
`results/experiments/20261015_exp1/`, then `_1`, `_2` without overwriting.
Change `workflow.experiment_id` for a new physical reaction; the existing
`output.run_root_dir` controls the root. Step-specific NMR/analysis/evidence is in
`step_1/` and `step_2/`; `channel_2_dose/dose_record.json` references the shared
physical-ID ledger. Metadata, journal/live status and final reports live in
`metadata/`, `logs/` and `final_qc/`. Read the local start time and mode in
`metadata/experiment_info.json` to select the latest run. Numeric execution suffixes
never reset dose protection, and there are no new prompts or flags.

## Needle and manual commands

HOME=0 is in the flask. UP=1 is out of the flask. DOWN is a compatibility alias
for HOME=0 in the current Arduino configs; normal automation never visits -1.
The 200 steps/unit and existing direction calibration are preserved.

On the instrument laptop, establish the reference once after physically placing
the needle at the intended liquid position:

```powershell
python arduino\scripts\needle_control.py set-home --live --config arduino\configs\arduino_real_COM3.yaml
```

This asks for `y` and records the current position as zero. It does not move.
`confirm-home` remains an equivalent command. After that:

```powershell
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino_real_COM3.yaml
```

Each manual movement asks for `y`. `home` and `down` move to 0; `up` moves to 1.
Repeated up/down commands do not add another unit of travel. `return-home`
remains an alias. `status` reads saved position and controller state without a
motion prompt. There are no acknowledgment flags.

Every automated run reads the saved position, moves back to HOME=0, verifies
the recorded position, then raises to UP=1 before its first pump cycle. Unknown
or interrupted motion state is not silently re-referenced. With no encoder or
home switch, physically restore/re-establish HOME with `set-home` after such a
failure. Normal known-position starts need only the main run's startup `y`.

## One-confirmation automation

The main reaction runs Stage 1, the single Channel 2 dose, Stage 2 and cleanup
without additional prompts. Previous-run review is advisory, and the
`--acknowledge-review` CLI flag has been removed from the three-instrument
diagnostic, experiment and soak scripts.
The experiment script defaults to the updated real two-stage recipe; `--mock`
selects the fast simulation recipe unless you explicitly supply another file.

An experiment ID still identifies one physical reaction. The durable reagent
dose ledger continues to refuse replay of the same reserved reaction, including
uncertain doses. Set a new `workflow.experiment_id` for a newly prepared
reaction. A startup confirmation does not erase an existing dose receipt.

## Distinguishing target disappearance from absent sample

The archived 06-08-26 and 06-09-26 spectra contain strong signals around
2.077–2.081 ppm and 6.981–6.990 ppm. The retained 2026-08-10 fixture has them near
2.079 and 6.994 ppm. Both are present in the real 06-09-26 0900 spectrum without
a detected target peak. These are the solvent signals identified by the operator.
The archived-data check is recorded in
`docs/99_archive/audits/SI6_SOLVENT_REFERENCE_CHECK_2026-10-14.csv`.

The full, unnormalized magnitude FFT checks both solvent windows independently
of the target's phase, baseline and regional peak detection. It uses 65,536
points and 0.3 Hz broadening consistently for this check. A window passes only
with a local peak above the height, prominence and noise-relative thresholds;
high flat backgrounds, noise and a lone solvent peak do not pass.

The current experiment YAML has this editable block:

```yaml
solvent_monitor:
  consecutive_missing: 3
  minimum_height: 10000.0
  minimum_prominence: 5000.0
  minimum_snr: 10.0
  windows_ppm: [[1.90, 2.25], [6.80, 7.10]]
  noise_windows_ppm: [[3.0, 4.0], [8.0, 9.0]]
  expected_centers_ppm: [2.08, 6.99]
  maximum_shift_ppm: [0.15, 0.10]
  maximum_pair_shift_difference_ppm: 0.04
```

Noise is estimated from robust adjacent differences in the noise windows. Both
solvent windows and all noise windows must fit inside the acquired spectrum.
Review these starting thresholds if solvent, scan count, receiver gain or
instrument changes. They pass the archived spectra but do not measure flask
volume or identify evaporation uniquely: empty tubing, failed sampling and
other sample-delivery problems can produce the same evidence.

## Exact stage rules

With both solvent signals present, Stage 1 requires four consecutive target
areas at or below 2.5% of the first trusted Stage 1 area. Target absence in a
solvent-confirmed spectrum is a real zero-area observation.

Stage 2 requires at least 25% growth from its first trusted area, then four
consecutive stable observations (three adjacent changes at most 2%). Missing
sample observations reset the consecutive endpoint window and cannot create
growth, target disappearance or plateau.

One or two consecutive missing solvent observations are recorded as untrusted;
sampling continues. After three, the current stage permanently switches to its
configured time limit. The console and journal report that solvent/sample is
probably low or not reaching NMR. Even if solvent signals subsequently return,
the stage continues to its deadline; it cannot finish early.

The timer runs from the original stage start, not from the third missing
observation. The real/nominal defaults are:

| Stage | Sampling cadence | Timed fallback duration |
|---|---|---|
| Stage 1 (`workflow.initial_stage`) | 120 minutes | 48 hours |
| Stage 2 (`workflow.first_addition_stage`) | 30 minutes | 4 hours |

Edit each stage's `max_hours` to change its fallback duration. The unchanged
development/simulation recipes retain their 12-hour Stage 2 testing ceiling.
These values are loaded at startup; editing YAML during a run has no effect.

Timed operation continues sampling past the ordinary 20-observation endpoint
cap. At 48 hours it can therefore have 24 scheduled cycle starts at two-hour intervals
(0, 2, …, 46 h). Stage 2 at 4 hours has eight scheduled starts (0, 0.5, …, 3.5 h).
No cycle starts at or beyond the deadline. A cycle already in progress finishes
its return/cleanup before transition; slow cycles may reduce the total count.

When Stage 1's fallback timer expires, cleanup precedes the protected Channel 2
dose, then Stage 2 starts automatically. Stage 2 has its own fresh missing-sample
counter. When its fallback timer expires, the automation finishes and reports
**timed fallback; chemical completion unverified**, rather than claiming an NMR
endpoint. Healthy stages that exhaust their count/time limits without an
endpoint still stop; limits alone do not authorize dosing unless solvent-loss
fallback was explicitly latched.

Empty, missing or unusable retrieved NMR files count as untrusted acquisitions
and can activate the same fallback. RPC acquisition exceptions/timeouts,
processor crashes on solvent-confirmed data, pump/needle uncertainty, STOP
failures and journal failures still stop the run. An absent sample cannot waive
instrument-motion or required persistence checks.

## Evidence and local checks

Each `step_*/analysis/time_series.csv` and `final_qc/full_time_series.csv` records `solvent_present`, `solvent_missing_streak`,
`stage_timed_fallback`, `endpoint_eligible` and `solvent_evidence_path`.
Untrusted target areas are blank, not zero. Per-acquisition JSON retains the
solvent peaks, noise, thresholds and raw-data hash when available. The journal
records fallback activation and `timed_fallback_completed` transitions. The
dose receipt records whether Stage 1 ended by an endpoint or timed fallback.
`final_qc/` summaries report fallback and distinguish automation completion
from verified chemistry completion.

NMR plot timing still comes only from JCAMP LONG DATE. No spectrum or timestamp
is invented for missing data; time plots are omitted if metadata is unavailable.
Existing figures retain dataset identity in their visible titles and manifests.

Validate the real recipe locally without opening instruments:

```powershell
conda activate ai
```

```powershell
python -B scripts\02_si6_experiment.py --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

```powershell
python -B -m pytest tests\test_si6_solvent_fallback.py
```

Use the full run commands in `REAL_INSTRUMENT_RUN_COMMANDS.md` only on the
instrument laptop. This update does not require new Arduino firmware.

## Initial October 14 validation (before final hardening)

- 150 existing workflow, area-endpoint, needle, cleanup, recovery and soak
  regression checks passed.
- All 30 new sample-presence/fallback checks passed across the full test run and
  focused follow-up runs. Cases include missing solvent in either/both stages,
  empty/missing/unreadable data, full 48 h/4 h virtual deadlines, one dose only,
  recovery after a latched failure, preserved unavailable areas, and a transport
  failure during fallback stopping without a dose.
- The independent synthetic spectrum-to-workflow validator passed, retaining
  production measurement, both chemistry trends and the matched dose journal.
- The additional admission/endpoint checks passed; outdated expectations were
  corrected for four Stage 1 observations and the HOME prompt. Nine saved
  needle-reference/state checks passed, including unknown-position refusal.
- All 15 archived spectra passed the then-current narrower solvent windows. A separate
  counterexample confirms that the strong 7.12 ppm neighboring signal cannot
  substitute for missing 6.99 ppm solvent.
- The real recipe and default CLI validated without opening instruments. All
  work here used the `ai` environment and no hardware was contacted.
- `python -B scripts/02_si6_experiment.py --mock` completed through the default
  recipe selection, both stages, one dose, cleanup and the final NMR summary.

## Final pre-deployment hardening

The windows were widened to 1.90–2.25 and 6.80–7.10 ppm. Full-spectrum local maxima are tested against expected centers and maximum shifts, then both solvent shifts must agree within 0.04 ppm. The 7.12 ppm neighbor exceeds the allowed 0.10 ppm displacement from 6.99; a shifted neighbor with inconsistent paired displacement is also rejected. Conservatively rejected sample signals still activate the existing timed fallback. See [the primary real-run checklist](../99_archive/SI6_FINAL_REAL_RUN_CHECKLIST.md) and [prompt audit](../99_archive/SI6_PROMPT_AUDIT.md).
