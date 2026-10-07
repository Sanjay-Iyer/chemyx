# Si6 area-only configuration guide

The full workflow uses `scripts/02_si6_experiment.py` with the selected run YAML.
One start confirmation launches Stage 1, one Channel 2 addition, then Stage 2.
The needle lowers/raises automatically; the cycle's `operator` entries encode
needle positions in this three-instrument runner, not recurring human prompts.

| What to edit | File / section |
|---|---|
| Run ID, stage cadence, iteration limits, endpoint thresholds, pump volumes/rates, NMR scans/gain | Your `--workflow-config`, copied from `config_templates/experiments/si6_two_stage_nominal.yaml` |
| Chemyx COM4 and NMR host/port | Your `--machine-config`, normally `configs/machines/00_machine.local.yaml`; Chemyx `port: COM4` |
| Arduino COM3 and calibrated needle positions | `--arduino-config`, normally `arduino/configs/arduino.local.yaml`; `serial.port: COM3` |
| Phase and baseline processing | `configs/nmr/analysis.yaml` and optional `configs/nmr/analysis.local.yaml` |
| Peak identity and moving integration | Run YAML `analysis.peak_finding`, `analysis.peak_area` |
| Review warnings only | Run YAML `qc_reporting`; `affect_workflow` must be false |

Local YAMLs may be gitignored. The four tracked `si6_two_stage_*.yaml` templates
contain the current schema; a local copy made before this change must be migrated.
Historical QC/statistical profiles are rejected before opening live transports.
The development templates still run the full sequence; they do not resume Stage 2.
`si6_two_stage_fast_sim.yaml` is mock-only and uses a virtual clock.

Stage 1 defaults (the full block matches the tracked nominal template):

```yaml
initial_stage:
  name: stage_1
  operator_prompt: Confirm rig at rest, loaded syringes and readiness for Stage 1.
  interval_minutes: 120
  max_hours: 48
  measure_immediately: true
  plateau_stopping_enabled: true
  completion:
    method: area_only
    trend: decreasing
    max_iterations: 20
    near_zero_fraction: 0.025
    consecutive_iterations: 3
    epsilon: 1.0e-12
  after_monitoring:
  - action: needle
    position: DOWN
  - action: infuse
    channel: 2
    volume_ml: 1.8
    rate_ml_min: 1.0
    needle_position: DOWN
  - action: needle
    position: UP
```
Stage 1 records each identified peak's area. Divide by the first Stage 1 area
(or epsilon if it is zero). Three consecutive fractions <=0.025 complete the
stage; a larger observation resets the count. A missing/lost tracked peak in a
successfully processed spectrum records exactly zero. No bounded-absence,
noise, uncertainty, phase, SNR QC, minimum elapsed time, range or slope test
participates. Reaching 20 observations without completion ends with
`STAGE_1_MAX_ITERATIONS_REACHED` and no Channel 2 dose.

Stage 2 defaults:

```yaml
first_addition_stage:
  name: stage_2
  operator_prompt: Confirm dose evidence and readiness for Stage 2.
  interval_minutes: 30
  max_hours: 12
  measure_immediately: true
  plateau_stopping_enabled: true
  completion:
    method: area_only
    trend: increasing
    max_iterations: 20
    minimum_growth:
      enabled: true
      fraction_of_initial: 0.25
    relative_change_threshold: 0.02
    consecutive_iterations: 4
    epsilon: 1.0e-12
```
Growth is `(largest area so far - first Stage 2 area) / max(abs(first area), epsilon)`.
It must reach 0.25 when enabled. A zero starting area therefore permits positive
growth; an all-zero series never meets enabled growth. Once growth is reached,
start a new stable window. Adjacent areas are stable when
`abs(current - previous) / max(abs(previous), epsilon) <= 0.02`.
Four consecutive stable observations mean **three adjacent stable pairs**.
A change above the threshold resets the window to one observation. Initial
flat observations before growth do not count. No proximity to a historical
maximum is required: a later low plateau can pass this intentionally simple rule.
Missing peaks also record zero in Stage 2, with a diagnostic warning.
Twenty observations without the endpoint end with `STAGE_2_MAX_ITERATIONS_REACHED`.

`interval_minutes` sets cadence between scheduled cycle starts. The optional
operational `max_hours` ceiling prevents starting another cycle after expiry:
48 h in Stage 1 and 12 h in Stage 2. It is not a scientific minimum duration.
Set the ceiling beyond the last scheduled start when changing count/cadence;
slow acquisition/cleanup can still reach the runtime ceiling first. A completing
observation is honored after full cleanup even if it ends beyond that ceiling.

Live identity and area settings:

```yaml
analysis:
  area_epsilon: 1.0e-12
  peak_area:
    method: trapezoid
  peak_finding:
    search:
      enabled: true
      half_width_ppm: 0.2
    previous_shift:
      enabled: true
      maximum_ppm: 0.08
    reference_shift:
      enabled: false
      maximum_ppm: 0.15
    continuity:
      enabled: true
    prominence:
      enabled: true
      minimum_snr: 5.0
    absolute_prominence:
      enabled: false
      minimum: 0.0
    width:
      enabled: true
      minimum_ppm: 0.015
      maximum_ppm: 0.167
    separation:
      enabled: true
      minimum_ppm: 0.04
```
The production detector uses regional phase/baseline correction, interpolation
and width estimation. `search` limits allowed center displacement from `nmr.target_ppm`;
`previous_shift` and optional `reference_shift` constrain the tracked identity.
`continuity` protects against switching to a known neighbor. `prominence`,
`absolute_prominence`, `width` and `separation` are peak-identification controls.
They answer which peak to integrate. If no eligible identity exists, area is zero;
the last detected center is retained across missing observations and the dose.

The area is the production `RegionPeak.positive_area`, calculated with
`numpy.trapezoid` (or `numpy.trapz` compatibility) on the positive corrected trace
inside that peak's moving center/width bounds. No fixed 5.70–5.90 integral drives
completion. Centers, actual sampled bounds, candidate rejections and lossless
phase/spectrum evidence are retained for each observation.

Post-run review settings:

```yaml
qc_reporting:
  enabled: true
  affect_workflow: false
  peak_filters:
    snr:
      enabled: true
      minimum: 5.0
    shoulder:
      enabled: true
      maximum_asymmetry: 0.5
    previous_shift:
      enabled: true
      maximum_ppm: 0.08
  measurement_qc:
    noise_multiplier: 3.0
    max_noise_fraction: 0.01
    max_area_uncertainty_fraction: 0.005
    undetected_max_fraction: 0.025
```
These filters and noise/uncertainty thresholds affect retrospective warnings
only. Disabling reports or forcing a QC failure does not change recorded areas,
cadence, completion, dosing or run status. `affect_workflow: true` is rejected.
A processor crash, missing/corrupt evidence or mismatched raw hash is an
operational failure with no usable area, distinct from a valid processed
spectrum whose peak is missing; it stops safely without dosing.

Sampling cycle and Channel 2 dose:

```yaml
cycle:
- action: withdraw
  channel: 1
  volume_ml: 8.0
  rate_ml_min: 5.0
- action: operator
  position: DOWN
  prompt: Lower needle.
- action: withdraw
  channel: 1
  volume_ml: 5.0
  rate_ml_min: 5.0
- action: pause
  seconds: 300
- action: nmr
- action: infuse
  channel: 1
  volume_ml: 13.0
  rate_ml_min: 5.0
- action: operator
  position: UP
  prompt: Raise needle.
- action: withdraw
  channel: 1
  volume_ml: 5.0
  rate_ml_min: 5.0
- action: infuse
  channel: 1
  volume_ml: 5.0
  rate_ml_min: 5.0
pump_extra_seconds: 2.0
```
Each cycle withdraws 8 mL UP, lowers, withdraws 5 mL, settles 300 s, acquires
NMR, returns 13 mL DOWN, raises, then withdraws/infuses 5 mL for cleanup.
Cleanup finishes before a completion result can authorize a stage transition.
Change volumes/rates while preserving balance and validated syringe capacities.

```yaml
after_monitoring:
- action: needle
  position: DOWN
- action: infuse
  channel: 2
  volume_ml: 1.8
  rate_ml_min: 1.0
  needle_position: DOWN
- action: needle
  position: UP
```
The one dose is 1.8 mL from Channel 2 at 1 mL/min while DOWN. A durable
reservation, dispatch intent, matched pump completion and confirmation protect
against replay. A failed Stage 1 endpoint or operational limit never doses.
Use a unique `workflow.experiment_id` for a new physical reaction, not to bypass
an existing reservation. No automatic partial-run resume is provided.

Syringe and acquisition parameters:

```yaml
pump:
  default_channel: 1
  channels:
    '1':
      syringe_diameter_mm: 20.0
      syringe_capacity_ml: 20.0
      initial_retained_volume_ml: 0.0
      syringe_safety_margin_ml: 1.0
      units: mL/min
      rate_ml_min: 5.0
      default_volume_ml: 5.0
    '2':
      syringe_diameter_mm: 12.06
      syringe_capacity_ml: 5.0
      initial_retained_volume_ml: 2.0
      syringe_safety_margin_ml: 0.2
      units: mL/min
      rate_ml_min: 1.0
      default_volume_ml: 1.8
```

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
Set the actual syringe diameters, capacities and loaded volumes. Keep fixed
receiver gain and `auto_gain: false` for comparable areas. Check effective
`CHEMYX_*` / `NMR_*` environment overrides and the saved instrument snapshot.
Machine endpoints are separate from these experiment settings:

```yaml
chemyx:
  serial_port: null
  baud_rate: 115200
  timeout_seconds: 2.0
  response_delay_seconds: 0.2
```
For your WORK laptop set the selected machine YAML's Chemyx `port` to `COM4`
and the selected Arduino YAML's `serial.port` to `COM3`; keep your instrument's
configured NMR host. The machine example above is a schema example.

From the repository root, using the already installed Python environment:

```powershell
Copy-Item config_templates/experiments/si6_two_stage_nominal.yaml configs/experiments/si6_run.local.yaml
python scripts/02_si6_experiment.py --workflow-config configs/experiments/si6_run.local.yaml --machine-config configs/machines/00_machine.local.yaml --arduino-config arduino/configs/arduino.local.yaml
python scripts/02_si6_experiment.py --mock --workflow-config config_templates/experiments/si6_two_stage_fast_sim.yaml --machine-config configs/machines/00_machine.local.yaml --arduino-config arduino/configs/arduino.local.yaml
python scripts/02_si6_experiment.py --live --workflow-config configs/experiments/si6_run.local.yaml --machine-config configs/machines/00_machine.local.yaml --arduino-config arduino/configs/arduino.local.yaml
```

Copy once; edit the local run ID and physical settings before the live command.
The first command after copying validates only. The mock command opens no real
instruments. The live command is for the connected WORK laptop; answer its one
start confirmation, then the two stages and dosing proceed automatically.

Each run retains `time_series.csv`, raw DX files, processed spectra/phase audit,
per-observation `tracking_evidence.json`, `stages/<stage>/evidence/*.json`, stage
summaries, the dose receipt and replayable hardware journal. At termination,
`final_qc/` contains `qc_summary.csv`, `qc_summary.json`, `qc_report.md`, and peak
position, area, SNR and width vs time plots in PNG/SVG/PDF with a plot manifest.
Every visible plot title identifies the dataset. All four time plots use the
same CSV table and JCAMP LONG DATE metadata. Missing metadata omits time plots
with an explanation; filename schedule labels and file mtime never substitute.
Report generation failures cannot change the established run outcome.
