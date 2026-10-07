# Si6 two-stage configuration

See [Si6 configuration guide](SI6_CONFIG_USER_GUIDE.md) for the current schema and commands.
Use `scripts/02_si6_experiment.py` with a copy of `config_templates/experiments/si6_two_stage_nominal.yaml`. Older statistical/QC completion YAML must be migrated before live use.

- Stage 1: `completion.method: area_only`, `max_iterations: 20`, `near_zero_fraction: 0.025`, `consecutive_iterations: 3`.
- Stage 2: 20 iterations maximum, enabled growth of 25% of its initial area, relative change <=2%, four stable observations (three adjacent pairs after growth).
- `analysis.peak_finding` contains identity controls; `analysis.peak_area.method: trapezoid` uses the production moving area.
- `qc_reporting.affect_workflow: false` is mandatory. Diagnostic thresholds never reject an observation or control dosing.
- Missing tracked peaks in processed spectra record zero. Failed processing/artifact integrity remains an operational failure, without dosing.
- Cadences are 120/30 min; secondary runtime ceilings are 48/12 h. Each stage has a distinct iteration-limit outcome.
- A unique physical `workflow.experiment_id` and the durable Channel 2 guard remain required.

[Implementation report](SI6_AREA_ONLY_IMPLEMENTATION.md) records behavior, termination conditions, files and tests.
