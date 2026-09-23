# Supervised D3/D4 demo prerequisites

Do not add wiring or change DM542S switch settings. Review and record the
already-working D3 STEP / D4 DIR wiring, the 24 V driver-power disconnect, and
the actual free movement of the axis before commanding a live move.

The current software requires **no ENABLE wire and no upper/lower limit
switches**. The staged unloaded Test 2 still requires a mechanically decoupled
motor and an operator who can safely observe its shaft. Test 3 requires an
inspected connected needle axis and a prior matching Test 2 result. Configure
and physically verify `needle.steps_per_unit`, `needle.up_step_sign`,
`needle.min_position`, `needle.max_position`, and the named UP/DOWN positions.
Do not copy a guessed calibration from an example file. The old 90-degree =
200-step bench example is evidence of a motor move, not evidence of safe
needle travel.

If no trusted state file exists, physically place/inspect the needle at the
chosen HOME, then run explicit `confirm-home`. A restart reloads the saved
estimate; if the axis may have been moved manually or while unpowered,
reinspect and confirm HOME again. For a supervised demonstration only: there
is no physical homing or collision protection, and software limits are not a
substitute for hardware protection in unattended operation.
