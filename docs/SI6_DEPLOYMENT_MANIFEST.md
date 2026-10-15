> Current operating authority: [SI6_START_HERE](SI6_START_HERE.md).
> The real Si6 recipe now explicitly selects ACME; see [phase selection and
> post-session comparison](SI6_NMR_PHASE_METHODS.md). Before replacing software,
> [back up rig state](RIG_STATE_BACKUP.md). The shared portable inventory is
> `offline/release_manifest.yaml`; `prepare_si6_release.py --working-tree
> --with-offline-payload` builds an uncommitted review snapshot. Older dated
> engineering evidence below retains its original validation scope.

# Si6 committed release and offline transfer manifest

Version: the committed Git HEAD delivered with this checklist. The exact full
commit is reported at delivery and embedded in generated `RELEASE_VERSION.json`.
Use `git rev-parse HEAD` in a checkout or
`python scripts/verify_si6_environment.py --expect-commit FULL_HASH` after export.

## Tracked source required

- `chemyx_lab/`: Chemyx/NMR interfaces, shared workflow, solvent monitor,
  production NMR processing, endpoints, journals/recovery and dose guard.
- `arduino/python`, `arduino/mock`, `arduino/scripts`, `arduino/configs` and
  `arduino/firmware/needle_controller`: host control, fixtures, calibration and
  existing firmware source. This release does not require a firmware reflash.
- `scripts/`, `configs/`, `config_templates/`, `docs/`, `tests/`, `smoke_test/`,
  `nmr_template/`, `requirements.txt`, `pyproject.toml`, `offline/` scripts/lock.
- Small committed `chemyx_lab/testing/fixtures` spectra and controller fixtures.
  `results/100426_algovalidation/code` supplies existing optional analysis tools;
  it is included without generated results.

Expected real configuration files:

```text
configs/experiments/si6_real_two_stage.yaml
configs/experiments/si6_real_toluene_soak_test.yaml
configs/experiments/si6_real_hardware_test.yaml
configs/experiments/si6_real_channel1_test.yaml
configs/experiments/si6_real_channel2_test.yaml
configs/machines/si6_real_COM4.yaml
configs/machines/si6_real_COM6.yaml
arduino/configs/arduino_real_COM3.yaml
configs/nmr/analysis.yaml
configs/nmr/analysis.local.yaml
```

These files are tracked; `.local.yaml` is not inherently private in this repo.
Do not put credentials in these files. Machine endpoints remain configurable;
NMR uses its local instrument LAN, not internet. No cloud account/model is needed.

## Dependencies and separate assets

Supported pinned deployment: **64-bit Windows CPython 3.11**, tested core package
versions in `offline/requirements-lock.txt`. Runtime requires pyserial, PyYAML,
NumPy, SciPy, matplotlib and nmrglue. NMR RPC uses Python's standard HTTP library.
The lock also supplies pytest and the existing optional PySide6/pyqtgraph/pandas
GUIs. The project source declares Python >=3.10, but the supplied locked wheel
bundle specifically targets 3.11 win-amd64; do not use it with another ABI.

The ignored `offline/wheelhouse`, `offline/installers` and
`offline/BUNDLE_MANIFEST.txt` must travel separately from Git. This HOME checkout
has 27 wheels and the Python 3.11.9 x64 installer. `offline/verify_bundle.py`
checks size/hash and rejects unverified wheels. The builder's dry installation
uses `--no-index`; a new bundle must be prepared online **before transfer**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File offline\build_offline_bundle.ps1 -Python C:\Path\To\Python311\python.exe
python offline\verify_bundle.py
python scripts\prepare_si6_release.py --with-offline-payload
```

The export includes these local wheels/installer plus tracked source and SHA-256
manifests; no Git or internet is required to extract/use it. Existing drivers,
NMR vendor software/licenses and any firmware upload toolchain must already be
installed or separately copied. Do not assume a Python environment folder is
portable between machines; verify the existing environment or install from wheels.

Rig-owned `runs/arduino/needle_state.json`, `runtime/si6_doses`, journals and actual
experimental data remain ignored and are **not** included in a source/offline
release. Preserve them separately when moving the same physical rig/reaction.
Archived large datasets are optional for additional regression checks; core
fixtures are committed. No credentials, `.env` or generated experiment results
are deliberately added to the release.
The optional historical/deep-phaser comparison gallery also depends on its
archived output/model/runtime assets; those developer-analysis assets are not
requirements of the real Si6 run or the portable core validation suite.

## Reproducible transfer without WORK Git access

After commit, with a clean tree, run the exporter on HOME. Copy its ZIP,
`.sha256` and `_manifest.json` directly to the drive. The exporter uses
`git archive HEAD` for source, so it exports the exact committed version rather
than a mixture of local edits. It refuses to overwrite an existing release.
On the destination verify ZIP hash, extract to a new directory, verify source
and payload hashes with the environment checker, then review operator YAMLs.
Intentional YAML edits are reported; source-code hash mismatches are failures.

The normal Git route is equivalent: push from HOME, pull that exact commit on
WORK, build/export there and transfer the same package. The final checklist
contains all runtime and recovery commands. A copied Git checkout should also
be checked for an exact commit and clean tree before local YAML edits.
