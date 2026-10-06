# Experiment recipes

Copy a complete YAML here to `configs/experiments/<your_name>.yaml`, then edit
the copy. These recipes use the existing runner/schema. See
`docs/CREATE_NEW_EXPERIMENT.md` for commands and configuration precedence.

| Recipe | Sampling | Stages |
| --- | --- | --- |
| `standard_si6.yaml` | Initial 60 min; additions 15 min | Current accepted Si6 settings |
| `reaction_30min.yaml` | Every stage 30 min | Initial + first addition + one acetone/silane pair |
| `reaction_60min.yaml` | Every stage 60 min | Same stage sequence |
| `reaction_mixed_intervals.yaml` | Initial 60, first addition 30, later 60 min | Same stage sequence |
| `short_attended.yaml` | One immediate full sampling cycle | One stage, no later additions, no plateau early stop |
| `long_monitoring.yaml` | Hourly for a 24-hour stage (23 slots) | One stage, fixed schedule, attended operation |

Scans/gain are global for each run. Pump SOP and detailed NMR processing defaults
are preserved. Stage times are examples to edit for the chemistry; long duration
does not introduce unattended operation. The standard template is equivalent
to the existing default recipe. No template selects live mode.

## Sequential channel-2 dose example

[`si6_two_channel_once.yaml`](si6_two_channel_once.yaml) is a complete simulation
recipe for channel-1 sampling, one 50 µL channel-2 dose between stages, and
channel-1 sampling again. See
[channel configuration and HOME simulation commands](../../docs/CHEMYX_CHANNEL_WORKFLOW.md).

## Two-stage Si6

`si6_two_stage_nominal.yaml` is the complete decreasing → one Ch2 dose →
increasing plateau profile. `si6_two_stage_fast_sim.yaml` exercises that entire
sequence on an explicit virtual clock. `si6_two_stage_stage1_development.yaml`
and `si6_two_stage_stage2_development.yaml` are editable full-reaction development
starting points with chemical prerequisites preserved. See
`docs/SI6_TWO_STAGE_CONFIGURATION.md`, `docs/SI6_TWO_STAGE_OPERATOR_GUIDE.md`
and `docs/OFFLINE_DEPLOYMENT.md`. Nominal thresholds/syringe dimensions need
WORK calibration; controller simulation is not physical verification.
