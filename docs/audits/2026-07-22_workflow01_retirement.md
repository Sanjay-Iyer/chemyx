# Workflow 01 Retirement Audit

Date: 2026-07-22

Decision: Workflow 02 is the canonical active experiment workflow.

## Dependency search

Repository searches covered both numbered and package names, the literal
`event: W`, `event: N`, and `event: I` forms, and generic `sequence:` references.
The latter was reviewed manually to distinguish the legacy experiment language
from journal sequence numbers and NMR pulse-sequence vendor documentation.

| Current path before retirement | Purpose | Active callers/references | Disposition | Move risk and evidence |
|---|---|---|---|---|
| `scripts/01_first_real_chemyx_nmr.py` | Workflow 01 launcher | Legacy docs only; imported retired package module | Archive | Low after docs update. |
| `scripts/first_real_test.py` | Compatibility bridge | No active caller | Archive | Low; wildcard-reexported only retired module. |
| `configs/experiments/01_first_real_chemyx_nmr.yaml` | W/N/I experiment | Workflow 01 and its tests | Archive | Low after parser/test retirement. |
| `chemyx_lab/workflows/first_real_chemyx_nmr.py` | Legacy runner and W/N/I parser | Launchers/tests plus four Workflow 02 helpers | Archive after helper extraction | High until `instrument_operations.py` replaced the active import; then tested. |
| `tests/test_first_real_chemyx_nmr_commands.py` | Legacy command characterization | Pytest only | Archive | Low after module retirement. |
| `tests/test_first_real_chemyx_nmr_config.py` | Legacy precedence tests | Pytest only | Archive | Low after legacy config loaders removed. |
| `docs/WORKFLOW_01.md` | Legacy operator guide | README index | Archive | Low after canonical docs update. |

## Active code retained

- Chemyx, NMR, and valve adapters: shared hardware boundaries used by Workflow
  02 and diagnostics.
- NMR analysis and output modules: active scientific processing.
- Machine configuration and generic pump/NMR loaders: active across Workflow 02
  and diagnostics.
- `workflows/instrument_operations.py`: the four helper capabilities required by
  Workflow 02, isolated from the W/N/I parser and legacy CLI.
- All numbered diagnostics and historical raw/processed result records.

## Removed active compatibility paths

The Workflow 01 experiment dataclasses, experiment loader, configured pump/NMR
loader wrappers, and active W/N/I parser path were removed. Active Workflow 02
configuration rejects `sequence` and `event` fields in favor of descriptive
`cycle` and `action` fields.

## Other candidates

| Candidate | Classification | Reason |
|---|---|---|
| `docs/diagrams/diagram_chemyx_nmr_arduino2.drawio` | Uncertain—human review | Alternate source cannot be declared obsolete from name/reference search alone. |
| Duplicate processed NMR analysis directories | Uncertain—human review | They are scientific records with provenance; no automatic move or deletion. |
| `_archive/**` | Keep historical | Existing ignored archive and manifest remain intact. |
| Raw DX files and result manifests | Keep active records | Experimental evidence is explicitly outside cleanup scope. |

No additional file had sufficiently strong evidence for archive or deletion.
