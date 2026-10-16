# Offline deployment: standalone instrument checkout

The complete updated working-tree ZIP includes uncommitted source plus the
existing offline wheels, installer, drivers and toolchain. **No commit/push**
is required. No runtime HOME/dose state is shipped from HOME.

On HOME, copy the prepared release folder to USB (example USB drive E:):

```powershell
cd C:\code\chemyx_pump
robocopy dist\si6_simple_instrument_check_ready_20261016 E:\Si6_simple_instrument_check_ready_20261016 /E
if ($LASTEXITCODE -ge 8) { throw 'Release copy failed' }
```

On WORK, with the rig stopped and all instrument programs closed:

```powershell
$Si6Usb = 'E:\Si6_simple_instrument_check_ready_20261016'
$Si6Root = 'C:\code\chemyx_pump'
$Si6Stage = 'C:\code\si6_simple_checkout_release_20261016'
$Si6Zip = @(Get-ChildItem -LiteralPath $Si6Usb -Filter '*.zip')
if ($Si6Zip.Count -ne 1) { throw 'Expected exactly one release ZIP' }
$Si6Expected = ((Get-Content -LiteralPath ($Si6Zip[0].FullName + '.sha256') -Raw) -split '\s+')[0]
if ((Get-FileHash -LiteralPath $Si6Zip[0].FullName -Algorithm SHA256).Hash -ne $Si6Expected) { throw 'ZIP checksum mismatch' }
if (Test-Path -LiteralPath $Si6Stage) { throw 'Use a fresh staging directory; do not reuse an older release' }
Expand-Archive -LiteralPath $Si6Zip[0].FullName -DestinationPath $Si6Stage
if (!(Test-Path -LiteralPath "$Si6Stage\scripts\03_simple_instrument_checkout.py")) { throw 'New checkout script missing' }
```

Back up WORK records **before** updating code, using WORK's existing Python:

```powershell
$Si6Python = "$Si6Root\.venv\Scripts\python.exe"
if (!(Test-Path -LiteralPath $Si6Python)) { throw 'Select the existing WORK Python before backing up' }
$Si6BackupArgs = @('--state-path', 'runtime', '--state-path', 'configs', '--state-path', 'arduino/configs')
if ($env:CHEMYX_POSITION_STATE_DIR) { $Si6BackupArgs += @('--state-path', (Join-Path $env:CHEMYX_POSITION_STATE_DIR 'chemyx_state')) }
& $Si6Python "$Si6Stage\scripts\rig_state_backup.py" backup --root $Si6Root --destination "$Si6Usb\WORK_state_backup" @Si6BackupArgs
if ($LASTEXITCODE -ne 0) { throw 'WORK state backup failed' }
```

The backup also inventories configured needle state and default dose/journal
paths. Add `--state-path` for any custom external state path not covered. Save
the printed backup path; existing instrument software/licenses/firmware remain
installed on WORK. Keep their installation media separately if needed.

Overlay code with **no /MIR**, excluding physical state and installed settings:

```powershell
robocopy $Si6Stage $Si6Root /E /XD runtime runs results rig_backups .git .venv "$Si6Stage\configs" "$Si6Stage\arduino\configs"
if ($LASTEXITCODE -ge 8) { throw 'Code update failed' }
New-Item -ItemType Directory -Force "$Si6Root\configs\checkouts" | Out-Null
if (!(Test-Path -LiteralPath "$Si6Root\configs\checkouts\si6_simple_instrument_check.yaml")) {
    Copy-Item -LiteralPath "$Si6Stage\configs\checkouts\si6_simple_instrument_check.yaml" -Destination "$Si6Root\configs\checkouts\si6_simple_instrument_check.yaml"
}
cd $Si6Root
& $Si6Python offline\verify_bundle.py
if ($LASTEXITCODE -ne 0) { throw 'Offline payload verification failed' }
```

This retains WORK's machine configs, Arduino calibration, manual HOME convention,
production recipe, runtime/chemyx_state, needle position, dose history, results
and existing `.venv`. Intentional WORK YAML differences are reported by the
release verifier, not silently overwritten. If packages need installation, use
the existing offline installer (no network, no full regression campaign):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File offline\install_offline.ps1
```

If no suitable Python is installed, use `offline\installers\python-3.11.9-amd64.exe`
and run the same installer. Never copy HOME's `.venv` or HOME references onto WORK.
Verify the complete release and new checkout before contacting hardware:

```powershell
& $Si6Python scripts\verify_si6_environment.py --expect-commit 3284fedb1aca9d761bae0fb2a8f868f29c7f3a19 --machine-config C:\code\chemyx_pump\configs\machines\si6_real_COM4.yaml
if ($LASTEXITCODE -ne 0) { throw 'Release/environment verification failed' }
& $Si6Python scripts\03_simple_instrument_checkout.py --validate-config
if ($LASTEXITCODE -ne 0) { throw 'Checkout config validation failed' }
& $Si6Python scripts\03_simple_instrument_checkout.py --help
```

The release verifier checks source-file hashes, including the new runner/module,
and reports the working-tree source identity; the base commit alone is not the
identity of these uncommitted additions. Use the COM6 override in both validation
commands if needed. Compare the saved WORK backup before proceeding if state
preservation needs auditing. Run `--live` only on WORK after the physical checks.
