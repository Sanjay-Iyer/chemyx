# HOME → WORK1 → offline WORK2 deployment

All HOME evidence is software/simulation only. Source is portable; physical
calibration, installed vendor software and per-rig state need explicit WORK
verification. See `OFFLINE_REQUIREMENTS.md` for packages/drivers/resources and
`SI6_TWO_STAGE_OPERATOR_GUIDE.md` for experiment and recovery operation.

## HOME software package

Changes should travel through the normal Git commit/push → WORK1 pull process.
No runtime path requires `C:\code\chemyx_pump`; that is only an example checkout.
Source derives repository root from its own location. Portable configs/templates,
fixtures, code, strict schema, tests and validator reports travel in Git.

Run from the root with the existing HOME environment:

```powershell
conda activate ai
python -B scripts\02_si6_experiment.py --workflow-config config_templates\experiments\si6_two_stage_nominal.yaml
python -B scripts\02_si6_experiment.py --mock --workflow-config config_templates\experiments\si6_two_stage_fast_sim.yaml
python -B scripts\validate_si6_offline.py --run-tests --copy-test
```

The last command performs deterministic offline checks and copies portable
resources into a different temporary directory before running the full mock
from that copied root. It uses the installed Python environment, not a fresh OS
or new interpreter installation. `test_tmp_offline_validation` retains compact
copy-test evidence; no instrument transports are opened live.

## WORK laptop 1 after Git pull

1. Confirm the expected source revision and required files. Run resource/config
   validation. Review local configs because `.local.yaml` shared starting files
   are tracked in this repository; Git pull can affect them. Use explicit reviewed
   rig config filenames/CLI flags and retain backups. Check environment overrides.
2. While internet is available, prepare the existing offline payload:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File offline\build_offline_bundle.ps1
```

   Inspect that script's supported options if preparing driver/Arduino bundles;
   see `docs/OFFLINE_SETUP.md`, `offline/serial_drivers.ps1` and
   `offline/arduino_toolchain.ps1`. Online package fetching belongs to bundle
   preparation, never experiment startup. The bundle targets Python 3.11 x64.
3. Test a fresh environment using the prepared payload without internet fetching:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File offline\install_offline.ps1 -RunTests
```

4. Install/retain correct USB drivers and NMR vendor software/required licenses.
   Verify COM assignments, local LAN RPC, Arduino firmware and needle calibration
   on actual instruments with the existing supervised commissioning procedures.
5. Copy the **complete repository folder and ignored offline payload** onto the
   external hard drive. Include actual machine/Arduino/experiment configs and
   any relevant ignored journals/dose ledger/history. Verify bundle checksums and
   copied file presence. Do not rely on a Git pull to supply wheels/drivers/data.

## WORK laptop 2 after hard-drive copy

1. Copy the folder to any suitable local writable directory. Run every command
   from its root. No clone/pull/internet is needed on WORK2.
2. Install the copied Python installer if needed. Create a fresh `.venv` using
   `offline/install_offline.ps1`; it installs with `--no-index`. Copy vendor
   install media/drivers separately as appropriate; inspect bundle checksums.
3. Validate resources/configs, then run full mock and deterministic review:

```powershell
.venv\Scripts\python.exe -B scripts\validate_si6_offline.py --simulate
.venv\Scripts\python.exe -B scripts\validate_si6_offline.py --run-tests
```

4. Identify this laptop's COM ports/NMR endpoint. Review the rig configs/actual
   syringes/tubing and acquire the explicitly inspected needle HOME reference.
   If this is a different physical rig, previous needle state proves nothing.
   Do not discard reagent ledger/history for a reaction already in progress.
5. Before actual reaction, complete supervised hardware commissioning checks:
   independent channel selection, STOP behavior, actual 1.8 mL delivery/rate,
   needle UP/DOWN clearance, correct sample return/clearing, NMR scans/acquisition,
   real spectrum QC/phase evidence and endpoint thresholds. Review power/sleep
   settings and disk space for unattended operation. HOME mocks do not verify
   these items.
6. Use the operator guide's exact validation and WORK live command with your
   reviewed YAML. Keep driver-power disconnect/operator intervention available.

## Paths, offline operation and state

Repository fixtures, processing code/config and relative output directories use
the derived repository root. Machine layer holds ports/local LAN addresses;
legitimate vendor installation locations stay local. No HOME user-profile or
development path is an experiment dependency. Git availability is optional.

`runtime/si6_doses` is deliberately ignored Git physical-experiment state; keep
it with owning journal and rig, including when copying to another laptop. The
exclusive reservation prevents concurrent same-ID admission and fresh restart
even if output root changes. It cannot protect a deliberately erased ledger or
an unrelated fresh checkout without the physical experiment's history. That is
why history/identity transfer is an operator requirement. No automatic partial-run
resume is provided. Follow inspection/reconciliation instructions after a stop.

Separate status records belong in the handoff: HOME software verified, WORK1
pull/install/physical checks pending, WORK2 copy/offline install/physical checks
pending until actually performed there.
