# Si6 two-stage operator guide

This workflow reuses `scripts/02_si6_experiment.py` and its shared three-instrument
runner. HOME validation is software/simulation only. Physical WORK commissioning
is required before an unattended reaction. Example completion and spectral-QC
thresholds are starting values, not experimentally validated chemistry criteria.
The retained real repository fixture has a detected 5.792 ppm peak but is
rejected by the nominal new QC limits: sideband noise is 3.48% of target height
versus the 1% limit, and the conservative integral uncertainty is about 29%
versus the 0.5% limit. Thus successful controller simulation does not establish
that nominal YAML will complete on real spectra. Establish adequate measurement
precision and reviewed QC/endpoint settings on WORK before the reaction; do not
relax a gate merely to force completion.

## Before starting on WORK

1. Review `docs/OFFLINE_DEPLOYMENT.md`; validate the copied source package.
2. Identify the Chemyx, Arduino and NMR endpoints. Copy
   `config_templates/machines/si6_instrument_settings.example.yaml` to
   `configs/machines/si6_work.local.yaml`, then set serial port and local NMR host.
   NMR readiness/ping must succeed. Acquisition timeout is distinct from cadence.
3. Review your Arduino YAML: firmware/version/wiring, direction, steps per unit,
   UP/DOWN positions, speed, acceleration, travel and communication timeouts.
   Enable the commissioned motion configuration. Verify actual needle clearance
   and immersion, tubing and return path. Logical positions are software estimates.
4. Put the needle at the physically inspected HOME reference and explicitly
   confirm it using the existing supervised command below. Never reuse needle
   state from another rig. Two-stage live startup rejects unknown or demo-assumed
   HOME; it subsequently commands UP before the first air withdrawal.
5. Prepare channel 1 for sampling. The nominal maximum retained estimate is
   13 mL, so the example 20 mL syringe with 1 mL margin accommodates the cycle.
   Inspect actual starting retained volume and measured inner diameter.
6. Load channel 2 with the second reactant and confirm its separate delivery
   tubing reaches the intended reaction vessel. Example loaded volume is 2 mL,
   nominal dose 1.8 mL, leaving an estimated 0.2 mL. The example 5 mL syringe
   diameter is a placeholder to verify, not a calibration claim.
7. Copy nominal YAML to `configs/experiments/si6_run.local.yaml`. Assign a unique
   `workflow.experiment_id` **for the physical reaction**, review scans, fixed
   gain, volumes/rates, pauses, cadences, stage limits and completion/QC criteria.
   Record the reaction identity outside the laptop as well. Do not assign a new
   ID to evade a blocked restart of the same reaction.

These WORK commands are operator-run commissioning/experiment commands; they
were **not executed against instruments on HOME**:

```powershell
.venv\Scripts\python.exe -B arduino\scripts\needle_control.py confirm-home --live --config arduino\configs\arduino.local.yaml
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --workflow-config configs\experiments\si6_run.local.yaml --machine-config configs\machines\si6_work.local.yaml --arduino-config arduino\configs\arduino.local.yaml
.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_run.local.yaml --machine-config configs\machines\si6_work.local.yaml --arduino-config arduino\configs\arduino.local.yaml
```

Run from the repository root. The middle command validates without opening
hardware. The live command requires the existing interactive confirmation.
Per-stage readiness checkpoints also remain. Stage 2 awaits its checkpoint
after the confirmed dose; plan operator availability for that transition.

## Sampling and chemistry

Each cycle follows this exact order: verify needle UP → channel 1 withdraw
8 mL air UP → needle DOWN → withdraw 5 mL liquid DOWN → configured pause
(nominal 300 s) → NMR (nominal 8 scans) → infuse 13 mL DOWN → needle UP →
withdraw 5 mL UP → infuse 5 mL UP → verify pump idle. A completion decision
never skips the return/clearing sequence. Volumes, channels, per-transfer rates,
scans and pause are YAML values. UP/DOWN geometry and movement behavior are in
Arduino YAML. Air UP/liquid DOWN order is a safety invariant, not a free reorder.

Stage 1 tracks the region near 5.8 ppm with an explicitly decreasing trend.
Nominal acquisitions start every 120 min, up to 48 h. Completion requires a
valid detected initial peak, substantial decline, a low upper-bound integral,
multiple stable windows and minimum observation count/duration. A failed peak
detector is not treated as zero. Undetected signal can count only with matching
retained corrected trace/phase provenance, bounded noise/uncertainty and no
unresolved rejected peak candidates or significant negative phase lobes.

After genuine Stage-1 completion and cleanup, the configured boundary commands
needle DOWN, performs one channel-2 infusion (nominal 1.8 mL at 1 mL/min), then
returns UP. This assumes the reviewed reagent tubing/needle arrangement; software
does not infer extra valves or fluidic operations. Journal and ledger evidence
must be durable before Stage 2 is admitted. Pump selection returns to default
channel 1 after every transfer.

Stage 2 explicitly expects increasing signal. Nominal cadence is 30 min,
expected duration 3 h, maximum duration 6 h. Expected duration is descriptive;
minimum duration and maximum ceiling are separate controls. The example requires
at least 3 h from its first acquisition, eight points, five-point windows, three
consecutive stable checks, material prior growth, and bounded range/slope.
The stable signal must also stay near its greatest observed valid integral;
a large decline followed by a flat low level is not a maximum-yield plateau.
Near-zero Stage-2 baseline is allowed only as a QC-qualified observation, with
later detected growth. A flat initial baseline or one flat interval cannot finish.
No completion at the configured maximum ends the experiment as inconclusive;
it never authorizes the dose or an operator advance past chemical prerequisites.

## Results

Live results use `output.run_root_dir`; mocks use its `_mock` sibling.
Each timestamped run retains config snapshots, append-only
`operation_journal.jsonl`, replayable `run_state.json`, raw acquisitions and
per-acquisition processing. Processing retains normal phase, baseline, peak QC,
plots and spectral evidence. Fixed target integrals are an additional metric,
distinct from detected-peak areas; the processing algorithms are unchanged.

`stages/stage_1` and `stages/stage_2` contain separate CSVs, titled PNG/SVG/PDF
plots, summaries and per-observation completion evidence. They update after each
completed sample cleanup. `transition/channel2_addition.json` records the dose.
`final` contains the combined CSV, plot with dose marker, summary and plot
manifest. All visible figure titles include the run/dataset identity. The
existing `final_nmr_summary` is also produced by the CLI after services close.

Time-series positions come from JCAMP LONG DATE; missing, repeated or backwards
acquisition timestamps stop completion monitoring. Stage plots use hours from
the first stage acquisition (an explicit metadata origin; the readiness
checkpoint/cycle loading happens before this origin). The combined plot uses
the first experiment acquisition. The dose marker is a journal boundary aligned
to the last acquisition using elapsed monotonic time, not an NMR acquisition.

## Recovery and reagent replay

There is **no automatic resume or replay of a partial run**. This is a deliberate
physical-state constraint. The normal successful experiment doses once. Across
crashes/restarts the software provides at-most-once automatic dispatch, rather
than claiming it can guarantee measured physical delivery after power loss.

`runtime/si6_doses/<experiment_id>.json` is an exclusive, fsynced reservation
outside the selectable output directory. It has the config digest, stable
experiment/dose ID and owning run. Any existing reservation—including a torn or
corrupt one—blocks a fresh live run of that reaction. Changing output root or
acknowledging a previous run does not clear it. Never delete/edit the ledger to
retry an uncertain dose; preserve it and the run history with the physical rig.

| Ledger state | Meaning and action |
|---|---|
| RESERVED | Dose not dispatched by this run, but Stage-1 motion may have occurred. Inspect journal and rig; fresh automatic restart blocked. |
| DISPATCH_INTENT | Boundary admitted; dose may be not started, partial, or completed without durable receipt. STOP/reconcile and inspect matching journal; never redose automatically. |
| CONFIRMED | Timed move, accepted STOP, verified cleanup and durable completion journal/receipt. Delivery is an estimate until physically verified. Do not repeat dose. |

The journal's metered-move lifecycle distinguishes planning, dispatch and
completion within DISPATCH_INTENT. A completed dose journal with an unconfirmed
ledger is still blocked; inspect both, do not guess. No physical recovery
command is issued by inspection:

```powershell
.venv\Scripts\python.exe -B scripts\02_si6_automated_nmr.py --inspect-run <run-directory> --rebuild-state
```

On interruption, inspect actual needle, syringes, tubing, stopped channels and
reaction chemistry before any operator-directed continuation. For an NMR failure
only, the existing runner returns/clears the sample when all pump/needle/journal
preconditions remain certain, then stops for review. Physical uncertainty or
Ctrl+C stops available instruments without guessed reversing/dosing. Preserve
raw spectra and diagnostics. Continuing the same partial chemical reaction
requires a separately reviewed manual plan; the current runner has no automatic
Stage-2-only resume command. A new experiment ID is for a newly prepared reaction.

## HOME/offline simulation

```powershell
conda activate ai
python -B scripts\02_si6_experiment.py --mock --workflow-config config_templates\experiments\si6_two_stage_fast_sim.yaml
python -B scripts\validate_si6_offline.py --copy-test
```

With a prepared offline `.venv`, replace `python` with
`.venv\Scripts\python.exe`; no Conda environment name is required. Fast simulation
uses explicit controller trend fixtures and a virtual clock at the nominal
cadence. It does not validate physical acquisition, chemistry, or production
spectral processing from those synthetic values. Existing real-fixture processor
regression tests provide the separate software processing check.
