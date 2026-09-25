# Configuration

The canonical experiment configuration is
`configs/experiments/02_si6_automated_nmr.yaml`. Machine-specific addresses live
in the tracked starting point `configs/machines/00_machine.local.yaml`; verify
its ports on each laptop before live use.

## Workflow 02 vocabulary

- `stage`: one reaction-monitoring phase.
- `cycle`: one sample withdrawal, NMR measurement, and return sequence.
- `measurement`: one scheduled NMR sampling slot.
- `operation`: one withdraw, infuse, pause, NMR request, or checkpoint.
- `plateau`: the full integrated-area stability criterion.

The active schema uses descriptive `action` values. It does not accept the
retired Workflow 01 W/N/I event shorthand.

## Stage fields

Every stage explicitly requires:

- `name` and `operator_prompt`;
- positive `interval_minutes`;
- Boolean `measure_immediately`;
- Boolean `plateau_stopping_enabled`;
- positive `max_hours`: the stage duration.

`max_measurements` is optional. Without it, measurements are scheduled every
interval while the stage is younger than `max_hours` (60 min over 26 h gives
25; 15 min over 2 h gives 7). An explicit positive integer is a cap that must
still start before `max_hours`; the three-instrument workflow rejects a cap that
would end a stage before its duration.

When plateau stopping is disabled, all slots run and successful completion is
`scheduled_monitoring_completed`. Plateau is still analyzed and recorded. When
enabled, verified plateau stops early. Reaching the limit without plateau is a
non-success outcome in the legacy workflow; the three-instrument workflow asks
the operator to CONTINUE, ADVANCE, or ABORT.

## Pump safety fields

- `syringe_capacity_ml`: required physical capacity.
- `initial_retained_volume_ml`: starting retained-volume estimate.
- `syringe_safety_margin_ml`: capacity reserve unavailable to automation.
- `syringe_diameter_mm`, `units`, `rate_ml_min`, and channel settings.

Validation simulates repeated complete cycles and requires maximum cumulative
retained volume plus margin not to exceed capacity.

## NMR and analysis

The NMR section fixes route, FID result type, scans, receiver gain, acquired
window, and the 5.8 ppm tracked-resonance target. With
`analysis.detection_window_ppm: 0.10` the window is 5.70-5.90 ppm, the
`target_peak` window in `configs/nmr/analysis.yaml`. Auto-gain must remain
disabled for comparable peak areas. The analysis section defines the ppm
window, SNR/prominence/area quality requirements, and the explicit acceptable
plateau growth band. The three-instrument workflow measures the window with
production `process_fid`; the legacy workflow still uses the magnitude
detector.

Unknown top-level, section, stage, and cycle-event fields are rejected before
hardware construction.

## Safe commands

```powershell
conda run -n ai python -B scripts\02_si6_automated_nmr.py --validate-only
conda run -n ai python -B scripts\02_si6_automated_nmr.py --dry-run
```

See `docs/SI6_AUTOMATED_WORKFLOW.md` for the complete scientific, scheduling,
journal, recovery, and attended-operation semantics.
