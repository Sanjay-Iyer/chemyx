# Offline Si6 requirements

Normal experiment execution is local: Python, serial Chemyx/Arduino, and NMR
RPC on the instrument LAN. It requires no GitHub, package downloads, cloud LLM,
account credentials, internet, external phase-comparator downloads or Node.js.
Git is optional provenance; absence is recorded as unavailable.

## Prepared environment

Target the existing offline bundle: **64-bit CPython 3.11 on Windows**.
`offline/requirements-lock.txt` pins the complete tested environment including
dependencies and optional NMR GUIs. It is the authoritative exact-version list.
Core pinned versions are pyserial 3.5, PyYAML 6.0.3, NumPy 2.4.6, SciPy 1.17.1,
Matplotlib 3.10.9 and nmrglue 0.11; pytest 9.0.3 supports offline validation.
Optional GUI/report dependencies include PySide6 6.11.2, pyqtgraph 0.14.0,
pandas 3.0.2 and their pinned dependencies. `requirements.txt` remains the
looser normal installation convention. No new experiment dependency was added.

HOME tests use the existing Conda `ai` environment. WORK does not need that
environment name or a cloud-agent environment: use the repository `.venv`
prepared with `offline/install_offline.ps1`. Do not copy HOME's `.venv` or Conda
directory as a portable installation. The existing bundle builder can include
the Python 3.11.9 x64 installer; obtain dependencies while internet is available.
Verify wheel compatibility and installation with `--no-index` before leaving
WORK1. Software tests in an existing HOME environment do not prove installation
on either WORK machine.

## Drivers and vendor interfaces — install/test before going offline

| Item | Required preparation |
|---|---|
| Chemyx Fusion 4000 | USB/serial adapter driver for the installed hardware, assigned COM port, configured pump baud and independent syringe calibration. Python pyserial supplies host serial access, not the driver. |
| Arduino needle | Driver matching the actual UNO R4 Minima, commissioned firmware/version and D3 STEP/D4 DIR controller wiring/calibration. Retain the repo firmware and, if recompilation is required, the offline Arduino CLI/core bundle. |
| NMR | Vendor NMReady/iFlow software/RPC service, licenses/install media where required, Windows/network configuration and instrument-local communication. These vendor binaries cannot be assumed to come from Git. No public internet required for the local RPC once installed. |
| Python | Prepared Python 3.11 x64 or copied installer, compatible wheelhouse and lock file; fresh destination .venv. |
| PowerShell | Windows PowerShell for existing offline bundle/install scripts. |

Use `offline/serial_drivers.ps1` to inventory/export suitable installed drivers
where supported. Do not substitute a guessed serial driver/device. Physical
driver recognition, channel behavior, actual dose delivery, needle clearance and
real acquisition readiness must be checked on WORK.

## Repository and transfer manifest

Copy the source package, including `chemyx_lab`, `scripts`, `arduino` firmware,
host code and configs, `configs`, `config_templates`, `tests`, `docs`,
`nmr_template`, `offline`, root requirements/project/test files and optional Git
metadata. `scripts/validate_si6_offline.py` checks required two-stage resources
and all four configs; schema validation is in the repository Python modules and
documented in `docs/SI6_TWO_STAGE_CONFIGURATION.md`.

**A Git pull alone does not transfer ignored payload.** Before hard-drive copy,
also include `offline/wheelhouse`, `offline/installers`,
`offline/BUNDLE_MANIFEST.txt`, needed `offline/drivers` and `offline/arduino`
toolchain. The bundle manifest records payload hashes/sizes. Preserve the rig's
actual local machine/Arduino/experiment config, `runtime/si6_doses`, owning run
journals/results and reaction identity records if the same physical experiment
or rig history is moving. Historical experimental data are intentionally ignored
in Git. Copy them explicitly when needed for analysis/recovery. Never import
another rig's needle position state as proof of your needle position.

All calibration values/settings needed at runtime must be in the reviewed local
config layer; no external HOME calibration file is introduced. Optional GUI
system fonts and vendor executable locations are machine resources, not source
paths. Historical audit outputs with original absolute filenames are provenance
and are not runtime resources. The live two-stage workflow fails closed when
required evidence/calibration state is unavailable.
