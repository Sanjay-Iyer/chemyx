# Phase 3 Audit

Date: 2026-07-20

Auditor: read-only sub-agent

Final status: PASS

## Audit History

Initial audit: FAIL

- Config containers used raw dictionaries and did not validate nested keys or
  field types.
- Configured loaders contradicted documented precedence.
- `scripts/first_real_test.py` only applied the machine Chemyx port, not baud,
  timeout, or response delay.
- Tests only covered successful YAML reading.

Second audit: FAIL

- Nested validation and machine fields were corrected.
- Precedence still included unclear legacy config behavior and the active
  script did not distinguish CLI overrides from experiment defaults.

Third audit: PASS WITH CORRECTIONS

- New YAML workflow precedence was corrected to CLI, explicit environment,
  machine config, experiment config, safe default.
- The remaining issue was missing direct tests for the active script's
  argparse-specific precedence path.

Final audit: PASS

- `tests/test_first_real_config.py` directly exercises
  `scripts/first_real_test.py` `parse_args`, `load_pump_settings`, and
  `load_nmr_settings`.
- Tests cover environment over machine/experiment, machine over experiment, and
  CLI over environment.
- Tests use dry-run/config paths and do not contact hardware.

## Acceptance Criteria

- Numbered experiment config: met.
- Machine template and ignored local machine filename: met.
- Typed parsing and nested validation: met.
- Machine-specific values removed from active Python source: met.
- Config precedence and fields documented: met.
- Offline tests updated: met for test creation; execution remains blocked until
  `conda run -n ai` is available.
