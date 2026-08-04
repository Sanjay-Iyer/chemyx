# Required Hardware Before Live Motion

## Before Test 2

- Verified open-collector/open-drain Arduino-to-DM542T interface and exact type.
- Interface-specific wiring diagram and completed review.
- Confirmed DM542T 5 V signal-selector position and explicit inversion.
- Exact NEMA 17 model, datasheet phase current, and identified coil pairs.
- Exact 24 V supply current rating.
- Recorded DM542T current and microstep switch settings.
- Numeric microsteps-per-full-step matching those switches; Test 3 recomputes
  `steps_per_mm = full_steps_per_revolution * microsteps_per_full_step / lead`.
- Fuse, professionally completed wiring, and an emergency driver-power
  disconnect.
- Motor mechanically disconnected from the needle axis.
- Operator confirmation that the unloaded shaft can rotate safely.
- Firmware compile-time motion commissioning enabled only after this review,
  with the matching YAML flag set true.

## Additional items before Test 3

- A successful matching live Test 2 result record.
- Upper and lower normally closed limit switches.
- Interactive active/released verification of both switches.
- Mechanical hard stops.
- Verified lead-screw lead, motor steps/revolution, microsteps, and calculated
  steps/mm.
- Verified safe UP, conservative test DOWN, and maximum travel positions.
- Conservative speed/acceleration and home backoff.
- Documented emergency power disconnect.
- Evidence the vertical mechanism cannot fall dangerously when holding torque
  is removed.

Test 4B additionally requires matching live Tests 1-3 and Test 4A, explicit
selection of approved opposite-direction/equal-volume pump actions from the
existing experiment YAML, the existing configured 1D NMR diagnostic, positive
settling delays, expected artifact suffix, and continuity since Test 3. Null or
false values block live dispatch, and the whole plan must fit its pre-action
runtime budget.
