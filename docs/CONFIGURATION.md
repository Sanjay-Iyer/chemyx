# Configuration

The canonical experiment configuration is
`configs/experiments/02_si6_automated_nmr.yaml`. Machine-specific addresses stay
in ignored `configs/machines/00_machine.local.yaml`, copied from
`00_machine.example.yaml` on the work laptop.

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
- positive integer `max_measurements`;
- positive `max_hours` extending beyond the last scheduled start.

When plateau stopping is disabled, all slots run and successful completion is
`scheduled_monitoring_completed`. Plateau is still analyzed and recorded. When
enabled, verified plateau stops early; exhausting slots without plateau is a
non-success outcome. `max_hours` is always a separate hard safety ceiling.

## Pump safety fields

- `syringe_capacity_ml`: required physical capacity.
- `initial_retained_volume_ml`: starting retained-volume estimate.
- `syringe_safety_margin_ml`: capacity reserve unavailable to automation.
- `syringe_diameter_mm`, `units`, `rate_ml_min`, and channel settings.

Validation simulates repeated complete cycles and requires maximum cumulative
retained volume plus margin not to exceed capacity.

## NMR and analysis

The NMR section fixes route, FID result type, scans, receiver gain, acquired
window, and 6.1 ppm target. Auto-gain must remain disabled for comparable peak
areas. The analysis section defines ppm tolerance, fixed integration window,
SNR/prominence/area quality requirements, and the explicit acceptable plateau
growth band.

Unknown top-level, section, stage, and cycle-event fields are rejected before
hardware construction.

## Safe commands

```powershell
conda run -n ai python -B scripts\02_si6_automated_nmr.py --validate-only
conda run -n ai python -B scripts\02_si6_automated_nmr.py --dry-run
```

See `docs/SI6_AUTOMATED_WORKFLOW.md` for the complete scientific, scheduling,
journal, recovery, and attended-operation semantics.
