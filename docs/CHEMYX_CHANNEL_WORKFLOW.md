# Sequential Chemyx channels in the three-instrument workflow

Use `scripts/02_si6_experiment.py` for configurable channel selection with the
Arduino needle and NMR. The existing nine-action sampling cycle keeps its
order. Each `withdraw` or `infuse` can specify `channel: 1` or `channel: 2`.
Omitting `channel` uses the experiment default, independently of the previous
operation. Both channels share one serial connection and execute sequentially.

## Configure the syringes

Existing flat `pump: {channel: 1, ...}` configurations still work. To configure
two syringes, move the syringe settings into `channels`:

```yaml
pump:
  default_channel: 1
  channels:
    "1":
      syringe_diameter_mm: 20.0
      syringe_capacity_ml: 20.0
      initial_retained_volume_ml: 0.0
      syringe_safety_margin_ml: 1.0
      units: mL/min
      rate_ml_min: 5.0
      default_volume_ml: 5.0
    "2":
      syringe_diameter_mm: 4.7
      syringe_capacity_ml: 1.0
      initial_retained_volume_ml: 0.5
      syringe_safety_margin_ml: 0.1
      units: mL/min
      rate_ml_min: 0.5
      default_volume_ml: 0.05
```

These are simulation example values, not measured syringe definitions. Each
channel requires its own diameter, capacity, units, and rate. Initial volume
and capacity reserve default to zero; default volume defaults to 5 mL. A
preloaded channel-2 dose needs a sufficient `initial_retained_volume_ml`.
Do not mix flat syringe settings with `channels`. `pump.channel` remains an
alias for `default_channel`; if both are present they must agree. Integer and
quoted channel keys are accepted. Legacy channel 0 retains its unprefixed
command behavior; explicitly addressed operations and `channels` use 1 or 2.

All `volume_ml` and retained-volume fields are in mL: **0.05 mL = 50 µL**.
For compatibility, `rate_ml_min` retains its existing field name, but its
numeric value is interpreted in the configured `units` (use mL/min normally).
The workflow converts target volumes for µL device units and computes travel
time with that channel's units/rate. A transfer may override `rate_ml_min`;
the next transfer returns to its channel's configured default rate.

Existing `CHEMYX_*` environment overrides apply to the default channel's
effective settings. `CHEMYX_CHANNEL` changes the default and must name a
configured channel when using `channels`. Check these variables if YAML and
console output appear to disagree.

## Deliver a one-time dose

Each stage can have `before_monitoring` and `after_monitoring` lists. The
first runs after that stage's operator checkpoint; the second runs after
successful monitoring or an explicit operator ADVANCE. Neither runs once per
sampling cycle, and CONTINUE does not repeat them. ABORT/failure skips the
after list. A stage in `repeating_stages` executes its lists once per expanded
round; put a dose in a non-repeating stage to deliver it exactly once per run.

For example, add this to the first stage to switch **1 → 2 → 1**:

```yaml
after_monitoring:
  - action: needle
    position: DOWN
  - action: infuse
    channel: 2
    volume_ml: 0.05
    rate_ml_min: 0.5
    needle_position: DOWN
  - action: pause
    seconds: 15
  - action: needle
    position: UP
```

The next monitoring stage resumes the standard channel-1 sampling/NMR cycle.
The complete example is
[`si6_two_channel_once.yaml`](../config_templates/experiments/si6_two_channel_once.yaml).

Boundary lists accept `withdraw`, `infuse`, `needle`, `pause`, and `nmr`.
Pump and NMR actions require an explicit `needle_position: UP` or `DOWN`.
Needle actions use `position`. Each list starts and finishes at UP; its position
requirements must match preceding needle actions. Additional NMR uses the
configured scans and the existing processing, QC, metadata timing, and output
path. Its series gets a separate stage label, keeping those measurements out
of the monitoring stage's plateau window. Configure the full sample transfer,
settle, and return sequence around additional NMR when the liquid path needs it.

## Validation, logs, and recovery

Unknown/missing channels, invalid diameter/rate, invalid needle sequences,
over-capacity withdrawals, and doses larger than the retained volume fail
before transports open. Every repeated sampling cycle must balance **each**
channel independently. Validation checks one-time actions across all expanded
stages; runtime checks repeat capacity and volume bounds before every transfer.

The workflow selects and configures the requested channel for every transfer.
Both configured channels must have confirmed STOPs before needle movement or
another transfer. Errors attempt STOP on both, even if one STOP fails. Interrupted
or unconfirmed transfers preserve the last proven volume, mark that channel
uncertain, and require inspection; no automatic redosing/resume is provided.
Boundary-action failures require inspection and do not guess a reverse transfer.
The established sample-return cleanup still runs after a sampling NMR failure
only when all channel states and needle position are proven.

The console prints `CHEMYX channel=...` for each transfer.
`operation_journal.jsonl` records `channel` on every transfer and STOP lifecycle
event, plus per-channel settings and expected volumes. `run_state.json` has
`pump_channels["1"]` and `["2"]` with retained volume, cumulative infusion and
withdrawal, settings, motion, uncertainty, and STOP evidence. The old scalar
retained-volume field remains the default channel's estimate. Offline journal
inspection/rebuilding reconstructs both channels. No historical files change.

The legacy two-instrument runner rejects these extensions explicitly; use the
three-instrument entry point. Existing flat recipes keep working in both.

## Validate on the HOME laptop

From `C:\code\chemyx_pump`, run simulation-only commands:

```powershell
conda activate ai
python -B scripts\02_si6_experiment.py --workflow-config config_templates\experiments\si6_two_channel_once.yaml
python -B scripts\02_si6_experiment.py --mock --mock-cycles-per-stage 2 --workflow-config config_templates\experiments\si6_two_channel_once.yaml --arduino-config arduino\configs\arduino.example.yaml
python -B -m pytest tests\test_si6_pump_channels.py tests\test_three_instrument_si6.py tests\test_chemyx.py tests\test_si6_safety.py tests\test_si6_automated_nmr.py tests\test_si6_journal_integration.py tests\test_runtime_journal.py
```

Mocks use a real stored JCAMP spectrum with simulated instruments; the first
NMR runs production processing. Mock results go to unique folders under
`results/runs/si6_two_channel_example_mock`. The command without `--mock`
validates only and opens no hardware.

On the actual Fusion 4000, verify addressed commands and STOP acknowledgements
on both drives, independent settings, physical syringe diameters/capacities,
delivery and completion timing at the chosen rates, and the channel-2 reagent
tubing path. Confirm its dose reaches the intended vessel without interfering
with the primary sample loop. These hardware behaviors are not proven by mocks.
