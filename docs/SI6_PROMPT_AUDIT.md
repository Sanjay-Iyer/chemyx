# Supported operator prompt audit — October 14, 2026

Repository search covered `acknowledge-review`, `acknowledge_review`, `input(`,
Confirm/confirmation, operator approval, previous-run review and press-enter
wording in active scripts, workflow code, Arduino host scripts and smoke tests.
Historical archive code, offline NMR review GUIs and unrelated developer tools
were inspected/classified rather than blindly stripped of interaction.

| Entry point / live action | Confirmations | Decision |
|---|---:|---|
| `scripts/02_si6_experiment.py` current two-stage recipe | 1 startup y | No prompts during Stage 1, dose, Stage 2, return or cleanup. Explicit `--validate-config` opens nothing. |
| `scripts/03_si6_real_soak_test.py` | 1 startup y | No subsequent prompts. Explicit configuration-only mode. |
| `scripts/01_three_instrument_system_test.py` live selections | 1 startup y | Shared diagnostics have no nested approvals. `--process-only` is offline, zero prompts. |
| `arduino/scripts/needle_control.py set-home/home/up/down` | 1 y | Absolute targets; `set-home` explicitly defines physical reference without moving. |
| `needle_control.py status/stop` | 0 | Status is read-only; STOP must remain immediately available even with corrupt/unknown position. |
| `needle_control.py manual-up/manual-down` | 1 y | Explicit untracked relative jog; invalidates HOME and requires physical re-reference afterwards. |
| `arduino/scripts/test_01_arduino_connection.py` | 1 y | Independent staged communication test; retained. |
| `arduino/scripts/test_02_unloaded_motor.py` | 1 y | Retains mechanical-disconnection and shaft-safety prerequisites. |
| `arduino/scripts/test_03_needle_axis.py` | 1 y | Optional `--confirm-home` reference operation is combined into its startup wording; removed duplicate prompt. No guessed live reference. |
| `arduino/scripts/test_04_integrated_system.py` 4A / 4B | 1 y each | Separate explicitly selected tests, not nested dialogs. Existing forward/return action selection and commissioning prerequisites remain. No guessed live reference. |
| `scripts/diagnostics/02_verify_chemyx_movement.py` | 1 y normally | Decline precedes connecting. Existing deliberate `--yes` batch bypass remains for compatibility; not used in real-run checklist. `--mock` opens no hardware. |
| `scripts/diagnostics/04_run_nmr_1d_acquisition.py` | 1 y | Added explicit confirmation before any RPC request. `--dry-run` uses local example settings and zero requests/prompts. `--mock-settings` alone is NOT a hardware-free mode. |
| `scripts/diagnostics/01_list_serial_ports.py` | 0 | Enumerates ports; no instrument commands. |
| `scripts/diagnostics/03_check_nmr_connection.py` | 0 | Explicit read-only RPC status check; contacts instrument LAN when run. Not a HOME validation command. |
| `scripts/diagnostics/05_inspect_mx_controller.py`, `06_check_mx_valve.py` | 0 | Existing read-only valve bring-up tools; outside three-instrument chemistry. No change. |
| `scripts/diagnostics/07_analyze_nmr_results.py`, `scripts/nmr/*` | 0 startup y | Offline analysis/review; intentional GUI interaction remains. No live transport. |
| `smoke_test/01_smoke_chemyx.py` | 0 | Read-only HELP serial check; contacts the pump, never START. |
| `smoke_test/02_smoke_nmr.py` | 0 | Read-only status RPC; no acquisition. |
| `smoke_test/03_smoke_arduino.py` | 0 | READY/PING/IDENTITY only; no movement. |
| `scripts/02_si6_automated_nmr.py` | 1 startup + historical operator checkpoints | Explicit legacy two-instrument workflow, not the current automation entry point. Kept for legacy operation/journal inspection; do not use it to start the new experiment. |

No active operator CLI requires obsolete `--acknowledge-review`. Previous-run
review is advisory, not a bypass for a corrupt needle record or existing dose
reservation. The single startup y never overrides STOP/state/dose interlocks.
Staged commissioning files and physical action selections are real safety
prerequisites, not duplicate confirmation dialogs, and remain enforced.

Tests exercise decline before transport opening, exact main/manual prompt counts,
configuration-only no-transport behavior, absolute targets, corrupt record reset,
unknown-position STOP, full automatic stage operation and preserved dose replay.
