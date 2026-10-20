# 07 — Other and older experiment workflows

[All documentation](../00_START_HERE.md)

These guides concern the older `scripts/02_si6_experiment.py` workflow,
experiment templates and their historical output/error conventions. Its
`si6_real_two_stage.yaml` uses fixed 48-hour Stage 1 but allows Stage 2 chemical
completion after its minimum, up to a 6-hour maximum.

For **both phases running their full times**, use
[03 — Fixed-time experiment](../03_fixed_time_experiment/00_START_HERE.md).

| Order | Guide |
|---|---|
| 01 | [Older experiment index](01_legacy_experiment_index.md) |
| 02 | [Older run guide](02_legacy_run_guide.md) |
| 03 | [Older configuration](03_legacy_configuration.md) |
| 04 | [Older timing/endpoint policy](04_legacy_timing.md) |
| 05 | [Solvent fallback](05_solvent_fallback.md) |
| 06 | [Older experiment outputs](06_legacy_outputs.md) |
| 07 | [Older errors and fixes](07_legacy_errors_and_fixes.md) |
| 08 | [Create experiments and use analysis tools](08_create_experiment.md) |

The older runner is [scripts/02_si6_experiment.py](../../scripts/02_si6_experiment.py),
with [configs/experiments/si6_real_two_stage.yaml](../../configs/experiments/si6_real_two_stage.yaml).
Templates remain under [config_templates/experiments](../../config_templates/experiments/README.md).
These are separate workflows, not additional steps for the fixed-time run.
