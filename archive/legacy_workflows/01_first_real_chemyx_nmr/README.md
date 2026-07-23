# Archived Workflow 01

Archived: 2026-07-22

Last active repository commit: `7f20628`

Old command: `python scripts/01_first_real_chemyx_nmr.py`

Workflow 01 was the original attended withdraw -> NMR -> infuse bench test. It
used the shorthand `W`, `N`, and `I` event language and a separate experiment
configuration parser. Workflow 02 (`scripts/02_si6_automated_nmr.py`) now
provides the canonical descriptive `action`/`cycle` model, cumulative-volume
validation, conservative plateau logic, fixed scheduling, emergency-stop
behavior, append-only journaling, and offline recovery inspection.

The legacy workflow was archived only after repository-wide dependency search.
Its four helpers still needed by Workflow 02 were copied without behavioral
change into the active `chemyx_lab/workflows/instrument_operations.py` module.
Pump and NMR adapters, analysis code, machine configuration, diagnostics, and
experimental results remain active and were not moved.

## Archived dependency map

| Archived artifact | Former callers/references | Disposition evidence |
|---|---|---|
| `scripts/01_first_real_chemyx_nmr.py` | README, Quickstart, validation and operator docs | Only launched the archived package workflow. |
| `scripts/first_real_test.py` | Historical compatibility command only | Re-exported the archived package workflow; no active caller. |
| `configs/01_first_real_chemyx_nmr.yaml` | Workflow 01 and its tests | Sole active user of the W/N/I schema. |
| `package/first_real_chemyx_nmr.py` | Two launchers, two tests, Workflow 02 helper imports | Workflow 02 imports were redirected to the active neutral helper module. |
| `tests/test_first_real_chemyx_nmr_commands.py` | Pytest discovery | Characterized only the retired workflow. |
| `tests/test_first_real_chemyx_nmr_config.py` | Pytest discovery | Exercised only the retired schema and precedence path. |
| `docs/WORKFLOW_01.md` | README documentation index | Described only the retired command and schema. |

The Workflow 01-specific experiment dataclasses, loader, configured-loader
wrappers, and W/N/I parser are no longer part of the active package. The full
historical implementation remains here and in Git history.

## Support status

Archived code is historical evidence, not a supported execution path. Do not
run it from the archive against instruments. If restoration is ever required,
recover the desired version through Git history into a review branch and repeat
offline and separately authorized work-laptop qualification.
