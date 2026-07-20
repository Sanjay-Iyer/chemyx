# Phase 4 Audit

Date: 2026-07-20

Auditor: read-only sub-agent

Final status: PASS

## Audit History

Initial audit: PASS WITH CORRECTIONS

- Pump movement order was tested, but setup order was not.
- NMR iFlow RPC order was not directly tested.
- The Phase 4 report overclaimed command coverage.

Final audit: PASS

- `tests/test_first_real_commands.py` now verifies setup order:
  `set_units`, `set_diameter`, `set_rate`.
- `tests/test_first_real_commands.py` now verifies movement order:
  signed `set_volume`, `start(delay=0)`, `stop`.
- `tests/test_first_real_commands.py` now verifies iFlow order:
  `iflow_1d_settings`, `iflow_experiment_settings`,
  `set_iflow_1d_settings`, `run_iflow_experiment`,
  `wait_for_idle`, `iflow_experiment_status`.
- The iFlow test monkeypatches the NMR client and does not contact hardware.

## Acceptance Criteria

- Chemyx serial isolated: met.
- NMR networking isolated: met.
- Verified command behavior preserved: met.
- Mock/fake transports and offline tests: met.
- Exceptions and timeout handling clear enough: met.
- Hardware was not contacted: met.
