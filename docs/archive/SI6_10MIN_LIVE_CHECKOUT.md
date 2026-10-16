> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../SI6_SIMPLE_RUN_GUIDE.md) and [errors/fixes](../SI6_ERRORS_AND_FIXES.md) at the instruments.

# Si6 short live instrument checkout

Run this on the instrument-connected WORK laptop, from `C:\code\chemyx_pump`.
HOME development and validation use mocks only. This checkout uses
`scripts\02_si6_experiment.py`, the production drivers, sampling cycle,
FFT/ACME/baseline/peak analysis, scheduler, dose guard, cleanup and journal.
It is a physical checkout with test fluid, not a reaction endpoint test.

## Prepare the separate recipe

Edit **only** `configs\experiments\si6_real_10min_checkout.yaml` for the checkout.
It reads `si6_real_two_stage.yaml` as its base without writing to it. The base
supplies the existing syringe geometry, Channel 1 cycle, NMR settings and
analysis/QC settings. Verify that those settings match the actual installed
syringes and rig before proceeding. The checkout deliberately supplies no
approved volume, rate, fluid or syringe geometry of its own.

Fill all these fields; the shipped recipe refuses to run until they are filled:

| YAML field | Operator setting |
| --- | --- |
| `checkout.test_fluid` | Actual approved test fluid |
| `checkout.routing` | Verified reservoir/flask/NMR/return/waste connections |
| `workflow.experiment_id` | Fresh identifier beginning `SI6_CHECKOUT_`, for example `SI6_CHECKOUT_20261016_001` |
| `workflow.initial_stage.after_monitoring` → Channel 2 `infuse.volume_ml` | Approved test volume in mL |
| Same action → `rate_ml_min` | Approved test rate in mL/min |
| `pump.channels.'2'.initial_retained_volume_ml` | Actual volume loaded in Channel 2 |

The single `after_monitoring` infusion is the authoritative test dose. Its
volume/rate also populate the effective Channel 2 defaults; the production
1.8 mL reagent dose is **not inherited**. Normal capacity and retained-volume
checks still apply. No numerical test-transfer values in automated tests are
approved live settings.

Both stages use `fixed_duration`, `max_hours: 0.08333333333333333` (five minutes)
and `interval_minutes: 1`. You can edit those durations and intervals here.
Keep Stage 1 `advance_at_duration: true` and Stage 2 `false`. Chemical endpoint
flags never shorten either checkout stage. A missing target or solvent is
recorded honestly, and analysis is still attempted on received data. Recoverable
numerical unavailability is documented after verified cleanup.

## Timing to expect

Five minutes means elapsed stage time, measured by the existing monotonic
scheduler. It is not five mock iterations. A physical cycle already underway
finishes its transfer, acquisition and cleanup before the stage decision.
An acquisition that has not started when the deadline is reached is skipped,
and the sample is cleaned up safely.

The current production sampling/cleanup cycle alone takes **452 seconds
(7 min 32 s)**, before NMR acquisition, processing and needle motion. A requested
60-second interval therefore cannot be achieved; cycles never overlap. Expect
one acquisition cycle per stage and a total longer than ten minutes. Do not
shorten the established transfers, acquisition or cleanup to force ten minutes.

In a mock example with a 30-second acquisition and negligible processing/needle
time, Stage 1 lasts 8 min 2 s. A **mock-only** test transfer taking eight seconds
puts Stage 2 start at 8 min 10 s, and completion at 16 min 12 s. Your approved
transfer settings and actual instrument overhead determine the live total.

## Verify configuration, then launch

After filling the recipe, configuration-only validation opens no instruments:

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Launch the live checkout with Chemyx on COM4 and Arduino on COM3:

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

For Chemyx COM6, use:

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

The same COM6 substitution works for `--validate-config`. The printed summary
shows CHECKOUT mode, experiment ID, ports/NMR host, syringe geometry and loaded
volumes, Channel 1 transfers, one Channel 2 test transfer, durations and
intervals. Read it before entering **`y` once** at the existing startup prompt.
There are no additional stage or dose confirmations.

For an offline run of a filled recipe, replace `--live` with `--mock`. This uses
the existing mock drivers and virtual clock; its acquisition files and results
do not demonstrate physical movement or real NMR performance. Do not interpret
an offline pass as a live instrument checkout.

## Operator observations

Before startup, check test-fluid compatibility, sufficient sample/solvent,
actual syringe geometry/loading, tubing connections and the intended destination
of the Channel 2 test infusion. Prepare the existing saved needle and syringe
HOME references using the normal operating procedure if needed. The checkout
does not reset production position tracking or establish a new HOME reference.

After the single startup `y`, observe:

1. Existing startup needle HOME/UP behavior, then HOME for sampling.
2. Channel 1 pre-withdrawal and sample withdrawal; NMR sample arrival.
3. NMR acquisition and a saved raw file, followed by processing or an explicit
   numerical-unavailability record.
4. Existing sample return and cleanup: Channel 1 infusion, wash withdrawal,
   then wash infusion, with needle movements in the established SOP order.
5. Stage 1 `time_limit_completed`, then needle HOME and exactly one configured
   Channel 2 test infusion with positive completion confirmation; needle UP.
6. Automatic Stage 2 sampling, NMR and cleanup, then its `time_limit_completed`
   outcome and clean process completion.

## Results and success criteria

The existing output allocator creates a dated directory, for example:

```text
results/experiments/YYYYMMDD_SI6_CHECKOUT_<unique ID>/
  metadata/                 # effective configuration, provenance and outcomes
  logs/                     # journal, command/response evidence and errors
  step_1/
    nmr_raw/
    nmr_processed/
    analysis/
    figures/
    logs/
  channel_2_dose/            # dose_record.json and command evidence
  step_2/
    nmr_raw/
    nmr_processed/
    analysis/
    figures/
    logs/
  final_qc/                 # usual QC plus checkout_report.json/.md
```

Use the exact `Results:` path printed by the command; an existing folder causes
the allocator to append a numeric suffix without overwriting it. The effective configuration
records the read-only base recipe path/hash and operator-supplied checkout
settings. Acquired JCAMP metadata remains the authoritative NMR time source.

An operationally completed run has two timed stage outcomes, one confirmed test
infusion and completed cleanup. Also inspect `final_qc/checkout_report.md`:
`live_command_evidence_complete` requires command completion evidence, raw NMR
files and analysis success or documented numerical unavailability from **each
stage**, both timed
outcomes and cleanup. A mock report says `mock_validation_passed` instead.
Neither report independently measures needle position or delivered volume;
compare the records with your physical observations. Numerical unavailability
shows that processing was attempted, not that spectral quality is acceptable.
Missing solvent/sample, absent raw files or unusable spectra require investigation
before starting the real experiment, even when timed progression completed.

`checkout_evidence_incomplete` means the records do not demonstrate the full
checkout, for example if slow sample preparation left no time to begin NMR.
An instrument, transport, STOP, storage or uncertain-motion failure remains a
failure; timed mode does not override it. An uncertain Channel 2 dispatch blocks
Stage 2 and replay. Preserve the original dose record and inspect/reconcile the
physical state using the existing recovery procedure. Do not retry a possibly
delivered dose by assigning a new ID. Never delete production ledgers to run
this checkout.

## Return to the real experiment

The checkout's unique durable dose record is separate from the production
experiment's identity. Successful physical commands still update the existing
syringe-position ledger: Channel 1 returns its transferred volume; Channel 2
infusion consumes the test-fluid volume. Prepare/reconcile the actual production
syringes, fluid loading and routing before changing recipes. Do not reset HOME
merely to hide displacement or erase dose history.

Select the original recipe explicitly (COM4 example; use COM6 as appropriate):

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Its settings remain Stage 1 fixed 48 hours / 120-minute interval, the original
protected reagent dose, and Stage 2 minimum 2 hours / maximum 6 hours /
30-minute interval with four qualifying post-minimum measurements.
