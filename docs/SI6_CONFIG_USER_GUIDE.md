# Si6 configuration: which file do I edit?

Change experiment parameters in a reviewed run YAML, then validate without
`--live`. There is no script rewrite for the configured Ch1 sampling/one Ch2
addition/Ch1 sampling sequence. The [script guide](SI6_SCRIPT_USER_GUIDE.md) gives
commands; the [operator guide](SI6_TWO_STAGE_OPERATOR_GUIDE.md) gives physical setup.

## Configuration hierarchy

| Concern | File selected / keys |
|---|---|
| Chemistry, cycle, channels, acquisition, completion, outputs | `--workflow-config`: copy `config_templates/experiments/si6_two_stage_nominal.yaml` to a reviewed run YAML. |
| This computer's instrument addresses/timeouts | `--machine-config`: copy `config_templates/machines/si6_instrument_settings.example.yaml`; keys `chemyx` and `nmr`. |
| Needle/controller calibration and transport | `--arduino-config`: reviewed `arduino/configs/arduino.local.yaml`, based on `arduino.example.yaml`. |
| Raw spectrum processing and peak QC | `configs/nmr/analysis.yaml`; optional `configs/nmr/analysis.local.yaml` overrides shared processing defaults. Production runner's tracked target/window also come from experiment YAML. |

These are distinct layers. Machine COM/IP settings do not define syringe size.
Arduino geometry does not define NMR cadence. Experiment `analysis` controls the
workflow measurement/completion gates, while NMR processing YAML controls baseline,
phase, regional picking and processor peak-QC. Both sets of QC must pass.

The runner reads the explicitly selected experiment file and merges selected
machine endpoints into pump/NMR connection settings. Arduino is loaded separately.
An unknown key fails schema validation. `three_instrument.initial_plateau_stopping_enabled`
overrides the initial stage flag during preparation; keep both enabled for this
two-stage profile. Auto gain must stay false for comparable integrals.

Explicit `CHEMYX_*` / `NMR_*` environment variables take precedence during
instrument-settings resolution. For example `NMR_DEFAULT_SCANS`, `NMR_TARGET_PPM`,
`NMR_AUTO_GAIN`, `CHEMYX_RATE` and `CHEMYX_DIAMETER` can override YAML acquisition
or default-channel settings. Review/remove unexpected environment overrides
before starting and inspect the run's `instrument_settings_snapshot.json`.
Resolved auto gain is checked again and rejected for quantitative completion.

## Experiment templates

`si6_two_stage_nominal.yaml` is the full two-stage starting template. Its
`SET_UNIQUE_EXPERIMENT_ID` placeholder must be replaced for a newly prepared
physical reaction. The same reaction retains its ID across inspection/restart;
do not change it to evade a dose reservation. Thresholds are editable starting
criteria, not commissioned chemistry calibration.

`si6_two_stage_fast_sim.yaml` adds a mock-only virtual clock and controller JSON
trend fixture. Wall time is compressed; its YAML acquisition cadences remain
**120min /30min**, and its limits remain48h /6h. It directly injects metrics;
use the new synthetic command to validate processed-spectrum measurements.
Simulation configuration is rejected by the live runner.

`si6_two_stage_stage1_development.yaml` and `si6_two_stage_stage2_development.yaml`
are full safe two-stage templates with development names/comments. They currently
retain the nominal cycle, dose, cadence and criteria. Copy and edit the named
stage for development; they do not isolate that stage, provide partial-run
resume or bypass Stage1 chemistry. Stage2 cannot start without the confirmed
boundary dose.

The final synthetic validator uses its own labeled processed-spectrum model and
extends **only its simulation Stage2 ceiling to 9h**. Its longer success history
completes at 16 observations/7.5h, beyond nominal 6h. This does not change nominal
YAML or establish that 6h is sufficient for real chemistry.

The synthetic spectrum validator explicitly reads shared
`configs/nmr/analysis.yaml`, so its PASS does not commission/test per-rig
`analysis.local.yaml` processing overrides. The normal processor merges local
overrides unless an explicit `--config` is supplied.

## Stage1 and the sampling cycle

The following blocks are copied from the nominal YAML. Stage1 cadence and
maximum duration are independent of the completion minimum duration.

```yaml
  initial_stage:
    name: stage_1
    operator_prompt: Confirm rig at rest, loaded syringes and readiness for Stage 1.
    interval_minutes: 120
    max_hours: 48
    measure_immediately: true
    plateau_stopping_enabled: true
    completion:
      trend: decreasing
      minimum_points: 8
      minimum_duration_hours: 12
      window_points: 4
      consecutive_confirmations: 3
      minimum_progress_fraction: 0.95
      low_fraction: 0.025
      max_window_range_fraction: 0.01
      max_abs_slope_fraction_per_hour: 0.001
```

`minimum_points: 8` does not guarantee stopping at8. A candidate must satisfy all
criteria for3 overlapping4-point windows, including at least12h from its first
acquisition. Low means the uncertainty-inclusive upper signal is at most2.5%
of the initial integral; progress must be at least95%. Range is at most1% of
initial area and absolute normalized slope at most0.001/hour. A single low point
or flat high signal cannot satisfy these conditions.

Each of the3 confirmation windows must itself meet the8-point minimum. With
these settings the earliest possible completion is therefore observation10,
even for an otherwise ideal qualifying history (18h at Stage1's cadence).

```yaml
  cycle:
    - {action: withdraw, channel: 1, volume_ml: 8.0, rate_ml_min: 5.0}
    - {action: operator, position: DOWN, prompt: Lower needle.}
    - {action: withdraw, channel: 1, volume_ml: 5.0, rate_ml_min: 5.0}
    - {action: pause, seconds: 300}
    - {action: nmr}
    - {action: infuse, channel: 1, volume_ml: 13.0, rate_ml_min: 5.0}
    - {action: operator, position: UP, prompt: Raise needle.}
    - {action: withdraw, channel: 1, volume_ml: 5.0, rate_ml_min: 5.0}
    - {action: infuse, channel: 1, volume_ml: 5.0, rate_ml_min: 5.0}
  pump_extra_seconds: 2.0
```

The nine entries preserve the reviewed order: air withdraw8 UP; needleDOWN;
liquid withdraw5; pause300s; acquire; return13 DOWN; needleUP; clear withdraw5;
clear infuse5. Adjust volumes/rates together so retained volume balances each
cycle and capacity/margin checks pass. Pause duration is not the NMR cadence.
`workflow.pump_extra_seconds: 2.0` is the metered motion margin.

## One Channel2 transition

```yaml
    after_monitoring:
      - {action: needle, position: DOWN}
      - {action: infuse, channel: 2, volume_ml: 1.8, rate_ml_min: 1.0, needle_position: DOWN}
      - {action: needle, position: UP}
```

Edit the infusion's `volume_ml` and `rate_ml_min` for the reviewed reagent dose.
This profile requires channel2 and the DOWN/UP boundary sequence; changing the
channel or adding extra dose actions is not an arbitrary supported edit.
The configured dose must fit Ch2's initial retained estimate/capacity. There is
one durable reservation per physical experiment, not one per output folder.
After each transfer selection returns to `pump.default_channel: 1`.

## Stage2

```yaml
  first_addition_stage:
    name: stage_2
    operator_prompt: Confirm dose evidence and readiness for Stage 2.
    interval_minutes: 30
    max_hours: 6
    measure_immediately: true
    plateau_stopping_enabled: true
    completion:
      trend: increasing
      expected_duration_hours: 3
      minimum_points: 8
      minimum_duration_hours: 3
      window_points: 5
      consecutive_confirmations: 3
      minimum_progress_fraction: 0.25
      max_window_range_fraction: 0.02
      max_abs_slope_fraction_per_hour: 0.012
```

The expected3h is descriptive; it does not force completion. Minimum3h and
maximum6h are separate. Stage2 requires8 observations,3 passing overlapping
5-point windows, at least25% growth on its greatest-observed-area scale,
uncertainty-expanded range <=2% and absolute slope <=0.012/hour.
Every lower-bound signal in each window must be >=98% of the greatest signal
seen over the entire stage. This proximity requirement uses
`max_window_range_fraction: 0.02`; there is no separate invented near-max key.
The same per-window observation gate makes observation10 the earliest possible
completion here (4.5h at30min cadence), rather than observation8 or the expected3h.

A high peak or one flat pair cannot finish. No-growth fails progress. A major
decline followed by a lower flat line fails proximity to the historical maximum.
The supplied short temporary plateau fails persistence/range/slope. A sufficiently
long convincing plateau can pass before unknown future growth; the software
cannot forecast unobserved chemistry. Commission minimum duration/criteria with
real data. Hitting maximum without valid completion stops as analysis inconclusive.

## Syringes, acquisition and measurement QC

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
      syringe_diameter_mm: 12.06
      syringe_capacity_ml: 5.0
      initial_retained_volume_ml: 2.0
      syringe_safety_margin_ml: 0.2
      units: mL/min
      rate_ml_min: 1.0
      default_volume_ml: 1.8
```

Verify measured inner diameter and physically loaded retained volume for each
channel. Capacity includes the configured safety margin; larger dose/sampling
volume requires checking these constraints. The example Ch2 diameter12.06mm and
capacity5mL are placeholders to verify. Volume state is a software estimate.

```yaml
nmr:
  route: iflow
  result_type: fid
  scans: 8
  receiver_gain: 12.0
  auto_gain: false
  spectral_center: 5.0
  sweep_width: 20.0
  target_ppm: 5.8
```

Scans apply to both stages. Keep receiver gain comparable and `auto_gain: false`.
Changing acquisition windows requires instrument review; the target resonance
also needs to remain inside the shared runner's fixed5.0–6.5ppm search region.

```yaml
analysis:
  detection_window_ppm: 0.10
  integration_window_ppm: 0.10
  plot_window_ppm: 0.5
  line_broadening_hz: 0.3
  min_peak_snr: 5.0
  min_prominence_snr: 3.0
  min_peak_area: 0.0
  area_epsilon: 1.0e-12
  plateau_max_growth_percent: 5.0
  plateau_max_decline_percent: 2.0
  plateau_consecutive_intervals: 3
  measurement_qc:
    noise_multiplier: 3.0
    max_noise_fraction: 0.01
    max_area_uncertainty_fraction: 0.005
    undetected_max_fraction: 0.025
```

`integration_window_ppm` is a half-width:0.10 means5.7–5.9ppm for target5.8.
The same fixed window supplies completion areas; picked peak areas use different
peak-dependent bounds. `plot_window_ppm` also defines the sideband-noise context
and must exceed the integral window. Noise is compared to the first Stage1
detected height, and uncertainty to its initial fixed integral, including Stage2.
Undetected peaks need independent bounded-low evidence; a zero-filled table
alone is insufficient. Negative phase lobes or rejected candidates fail closed.
Legacy `analysis.plateau_*` settings remain for older profiles; explicit stage
`completion` rules govern this profile.

Nominal retained-real-fixture limitations remain: noise3.48% exceeds1%, area
uncertainty about29% exceeds0.5%. Synthetic success does not justify loosening
gates merely to finish. Review measurement precision and actual chemical endpoints
on WORK. Missing/non-increasing LONG DATE prevents completion decisions.

## Processing YAML

`configs/nmr/analysis.yaml` controls the unchanged processor. Current key groups:

| Group | Important actual keys |
|---|---|
| `processing` | `phase_method`, `baseline_method`, `normalization`, `line_broadening_hz`, `zero_fill_points`, `truncation_window`, `baseline_polynomial_order`, `smoothing_window_ppm` |
| `regional_analysis` | `ppm_min`, `ppm_max`, `detection_trace`, `min_prominence_snr`, `min_peak_distance_ppm`, `min_peak_width_ppm` |
| `peak_qc` | `min_snr`, `min_prominence_snr`, `min_width_hz`, `max_width_hz`, `require_positive_area` |
| `simple_table` | `restrict_to_window`, `target_ppm`, `window_ppm`; the workflow supplies its tracked target/window to the processor CLI. |
| `statistics` / `target_peak` | `dataset_display_name` used by their analysis plots when configured; otherwise dataset/run metadata provides visible identity. |

The shared runner explicitly supplies region5.0–6.5ppm, overriding the processing
YAML region bounds. Changing those bounds affects standalone processing, but
does not move the runner's search region. A target/integration window outside
that region requires a separately reviewed wrapper change. Experiment
`analysis.line_broadening_hz` affects the magnitude diagnostic; quantitative
production line broadening comes from `processing.line_broadening_hz`.

Standalone `process_fid --config PATH` selects that explicit mapping. Without it,
shared YAML plus optional local YAML are merged, then CLI flags override. Other
NMR diagnostic scripts also use `common`/their own sections; `common.target_ppm`
alone does not change the two-stage runner's target. Never use per-spectrum max
normalization for comparable quantitative endpoint areas.

## Machine configuration: WORK1 vs WORK2

Copy the unset machine template to a reviewed rig-specific file. Default
`configs/machines/00_machine.local.yaml` contains earlier-rig example COM/IP
values, not identification evidence for this computer.

```yaml
# Per-rig connection layer. Copy to configs/machines/si6_work.local.yaml.
# Identify actual devices; these are deliberately unset for a new rig.
# Do not copy a needle position state from a different physical rig.
chemyx:
  serial_port: null
  baud_rate: 115200
  timeout_seconds: 2.0
  response_delay_seconds: 0.2
nmr:
  host: null
  port: 5000
  scheme: http
  timeout_seconds: 10.0
  poll_seconds: 2.0
  max_wait_seconds: 600.0
```

Change `chemyx.serial_port` and `nmr.host` to actual verified endpoints. USB COM
numbers can differ between laptops; baud must match device setup. NMR port,
scheme, timeouts/polling/max wait must match the RPC service. `max_wait_seconds`
limits an acquisition wait, not hours of chemistry or stage cadence.

This repository intentionally tracks default `.local.yaml` files; the suffix
does **not** make them private or ignored. Use separately named per-rig files
selected explicitly by CLI and keep rig-only details out of commits (for example,
place private files outside the repository, or add their exact paths to local
`.git/info/exclude`). Inspect `git status` before committing. Share calibration
changes only when deliberately intended for the same physical rig. This guide
does not change the existing repository config policy.

## Arduino configuration

Select the reviewed Arduino YAML explicitly. The example intentionally contains
unset calibration and disabled motion; do not fill it with guesses to pass live
admission. Verify these actual settings on each physical rig:

| Setting | Meaning |
|---|---|
| `arduino.port`, `baud_rate`, `expected_device`, `expected_board`, `expected_version`, `fingerprint` | Connection and controller identity. |
| `firmware.motion_enabled`, `limits_enabled`, `runtime_configurable`, `version` | Installed firmware behavior/expectations. |
| `needle.home_position`, `up_position`, `down_position`, `min_position`, `max_position` | Reviewed software coordinates/travel. |
| `needle.steps_per_unit`, `up_step_sign` | Measured motion scale and direction; confirm clearance and immersion physically. |
| `needle.state_path` | Durable position/certainty estimate, normally `runs/arduino/needle_state.json`. |
| `motion.maximum_speed_steps_s`, `maximum_acceleration_steps_s2` | Commissioned movement limits. |
| `signal_interface`, `motor`, `driver`, `safety` | Reviewed actual wiring, current/microsteps, mechanics and safety configuration. |

Explicitly confirm the inspected HOME using the existing supervised WORK command
in the script guide. Logical STATUS is not a physical-height sensor. Do not copy
another rig's state/calibration blindly.

## What do I change?

Here, **run YAML** means your reviewed copy of the nominal experiment; **machine
YAML** and **Arduino YAML** mean the explicit per-rig files passed on the CLI.

| I want to change… | File | Actual setting |
|---|---|---|
| Stage1 every2h →90min | Run YAML | `workflow.initial_stage.interval_minutes: 90` |
| Stage1 maximum duration | Run YAML | `workflow.initial_stage.max_hours` |
| Stage2 every30min →20min | Run YAML | `workflow.first_addition_stage.interval_minutes: 20` |
| Stage2 expected / maximum time | Run YAML | `workflow.first_addition_stage.completion.expected_duration_hours` / `workflow.first_addition_stage.max_hours` |
| Scans8 →16 | Run YAML | `nmr.scans: 16` |
| Target5.8ppm | Run YAML | `nmr.target_ppm`; retain the target/integral window within the runner's fixed5.0–6.5ppm search region |
| Detection / integral half-window | Run YAML | `analysis.detection_window_ppm` / `analysis.integration_window_ppm` |
| Pre-NMR wait | Run YAML | `workflow.cycle[3].seconds` |
| Air / liquid / return / clear volumes | Run YAML | `workflow.cycle[0,2,5,7,8].volume_ml`; maintain channel balance |
| Sampling transfer rates | Run YAML | `workflow.cycle[0,2,5,7,8].rate_ml_min` |
| Ch2 dose1.8 →2mL | Run YAML | `workflow.initial_stage.after_monitoring[1].volume_ml: 2.0`; verify Ch2 loaded volume |
| Ch2 dose rate | Run YAML | `workflow.initial_stage.after_monitoring[1].rate_ml_min` |
| Initial retained volume / capacity / margin | Run YAML | `pump.channels."1"` or `."2"`: `initial_retained_volume_ml`, `syringe_capacity_ml`, `syringe_safety_margin_ml` |
| Ch1 / Ch2 syringe inner diameter | Run YAML | `pump.channels."1".syringe_diameter_mm` / `pump.channels."2".syringe_diameter_mm` |
| Stage1 low threshold | Run YAML | `workflow.initial_stage.completion.low_fraction` |
| Stage1 stability | Run YAML | `workflow.initial_stage.completion.max_window_range_fraction`, `max_abs_slope_fraction_per_hour` |
| Stage2 minimum growth | Run YAML | `workflow.first_addition_stage.completion.minimum_progress_fraction` |
| Stage2 plateau strictness / proximity to max | Run YAML | `workflow.first_addition_stage.completion.max_window_range_fraction`, `max_abs_slope_fraction_per_hour` |
| More observations / longer observation period | Run YAML | Each stage's `completion.minimum_points`, `minimum_duration_hours` |
| More stable windows / larger window | Run YAML | Each stage's `completion.consecutive_confirmations`, `window_points` |
| Measurement precision bounds | Run YAML | `analysis.measurement_qc.max_noise_fraction`, `max_area_uncertainty_fraction`, `noise_multiplier`, `undetected_max_fraction`; commission with real data |
| Detector/width QC | Processing YAML | `peak_qc` and `regional_analysis`; separate from workflow QC |
| Chemyx COM / baud | Machine YAML | `chemyx.serial_port`, `baud_rate` |
| Arduino COM | Arduino YAML | `arduino.port` |
| Needle UP/DOWN / direction / scale | Arduino YAML | `needle.up_position`, `down_position`, `up_step_sign`, `steps_per_unit` |
| NMR network address | Machine YAML | `nmr.host`, `port`, `scheme` |
| Acquisition timeout | Machine YAML | `nmr.max_wait_seconds`, `timeout_seconds`, `poll_seconds` |
| Experiment results folder | Run YAML | `output.run_root_dir`; mocks automatically use `_mock` sibling |
| Synthetic validation folder | CLI | `validate_si6_synthetic_analysis.py --output-dir NEW_PATH` |
| New physical reaction identity | Run YAML | `workflow.experiment_id`; preserve ID for the same partial reaction |

Shorter cadence changes elapsed-time evidence and cannot shorten the configured
minimum duration by itself. Increasing a maximum does not declare completion.
After edits, run the main runner without mode flags and software validators before
supervised WORK operation.

## Settings to leave alone unless you know why

Do not edit journal lifecycle/sequence semantics, dose IDs/configuration digests,
replay records, runtime reservations or needle certainty files to unblock an
experiment. They encode uncertainty and prevent replay. Preserve history with
the physical rig; the runner has no automatic partial-stage resume.

Keep stored/manual phase sign/pivot conventions, raw decoder/FFT scaling,
processing filename/table rules, retained array schema and source hashes intact.
The measurement adapter relies on those contracts. Phase/baseline changes require
reviewed real spectra and regression checks, not only synthetic endpoint PASS.
Keep this profile's decreasing/increasing stage roles, safe cycle ordering,
confirmed one-dose boundary and readiness checks unless a separate workflow
change is designed and validated. Fractions are not universal chemistry constants.
