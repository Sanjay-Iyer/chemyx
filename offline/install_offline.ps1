<#
.SYNOPSIS
    Set up chemyx_pump on a laptop with no internet, from offline\wheelhouse.

.DESCRIPTION
    Install Python first with offline\installers\python-3.11.9-amd64.exe
    (tick "Add python.exe to PATH"). Then, from the repository root:

        powershell -ExecutionPolicy Bypass -File offline\install_offline.ps1 -RunTests

    1. Finds 64-bit Python 3.11 (or use -Python C:\path\to\python.exe).
    2. Creates .venv\ in the repository and installs requirements-lock.txt from
       offline\wheelhouse\ with --no-index, so it never touches the network.
    3. Checks that every package imports.
    4. Verifies that the source, firmware, config, fixture, and documentation
       files required for offline operation were transferred.
    5. Creates configs\machines\00_machine.local.yaml from the example if it is
       missing. Edit its COM port afterwards.
    6. Validates the Si6 workflow configuration without hardware.
    7. Compiles the needle firmware with offline\arduino when that bundle was
       transferred. No board is contacted.

    -RunTests also runs the main and Arduino test suites and the Level 1 and
    Level 2 mock workflows (about 8 minutes). Nothing contacts hardware.

    Afterwards, run everything from the repository root with
    .venv\Scripts\python.exe. No activation is needed.
#>
param(
    [string]$Python = "",
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$offline = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $offline
$lock = Join-Path $offline "requirements-lock.txt"
$wheelhouse = Join-Path $offline "wheelhouse"
$venv = Join-Path $repo ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$probe = "import sys, sysconfig; print('%d.%d %s' % (sys.version_info[0], sys.version_info[1], sysconfig.get_platform()))"

function Test-Python311 {
    param([string]$Exe, [string[]]$Prefix)
    try {
        $tag = & $Exe @Prefix -c $probe
        return ($LASTEXITCODE -eq 0 -and $tag -eq "3.11 win-amd64")
    } catch {
        return $false
    }
}

Set-Location $repo

Write-Host "[1/7] Finding 64-bit Python 3.11"
$candidates = @()
if ($Python) { $candidates += , @($Python) }
$candidates += , @("py", "-3.11")
$candidates += , @("python")
$candidates += , @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe")
$candidates += , @("$env:ProgramFiles\Python311\python.exe")
$pythonExe = $null
$pythonPrefix = @()
foreach ($candidate in $candidates) {
    $prefix = @()
    if ($candidate.Count -gt 1) { $prefix = $candidate[1..($candidate.Count - 1)] }
    if (Test-Python311 -Exe $candidate[0] -Prefix $prefix) {
        $pythonExe = $candidate[0]
        $pythonPrefix = $prefix
        break
    }
}
if (-not $pythonExe) {
    throw ("64-bit Python 3.11 not found. Run offline\installers\python-3.11.9-amd64.exe " +
        "(tick 'Add python.exe to PATH'), open a new PowerShell window, and rerun.")
}
Write-Host "      using: $pythonExe $($pythonPrefix -join ' ')"

if (-not (Test-Path (Join-Path $wheelhouse "*.whl"))) {
    throw "No wheels in $wheelhouse. Build them first with offline\build_offline_bundle.ps1."
}

Write-Host "[2/7] Installing packages into $venv (no network)"
if (-not (Test-Path $venvPython)) {
    & $pythonExe @pythonPrefix -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create $venv." }
}
& $venvPython -m pip install --no-index --disable-pip-version-check --find-links $wheelhouse -r $lock
if ($LASTEXITCODE -ne 0) { throw "Offline install failed; see the pip error above." }

Write-Host "[3/7] Checking imports"
& $venvPython -c "import serial, yaml, numpy, scipy, matplotlib, nmrglue, pandas, pyqtgraph, pytest; import PySide6.QtWidgets; print('      all packages import')"
if ($LASTEXITCODE -ne 0) { throw "A package failed to import." }

Write-Host "[4/7] Checking required offline source files"
$requiredRelativePaths = @(
    "arduino\firmware\needle_controller\needle_controller.ino",
    "arduino\python\controller.py",
    "arduino\mock\fake_arduino.py",
    "arduino\configs\arduino.example.yaml",
    "arduino\scripts\test_01_arduino_connection.py",
    "arduino\scripts\test_02_unloaded_motor.py",
    "arduino\scripts\test_03_needle_axis.py",
    "arduino\scripts\test_04_integrated_system.py",
    "chemyx_lab\instruments\chemyx.py",
    "chemyx_lab\instruments\nmr.py",
    "chemyx_lab\analysis\nmr.py",
    "chemyx_lab\analysis\completion.py",
    "chemyx_lab\workflows\si6_automated_nmr.py",
    "chemyx_lab\workflows\three_instrument_si6.py",
    "chemyx_lab\runtime_journal.py",
    "chemyx_lab\runtime_state.py",
    "chemyx_lab\recovery.py",
    "chemyx_lab\testing\fixtures\tracked_resonance_phsi4_20260810.dx",
    "chemyx_lab\testing\fixtures\no_resonance_phsi2_20260609_0900.dx",
    "configs\experiments\02_si6_automated_nmr.yaml",
    "configs\machines\00_machine.example.yaml",
    "configs\nmr\analysis.yaml",
    "scripts\02_si6_automated_nmr.py",
    "scripts\01_three_instrument_system_test.py",
    "scripts\02_si6_experiment.py",
    "scripts\nmr\process_fid.py",
    "scripts\diagnostics\01_list_serial_ports.py",
    "scripts\diagnostics\02_verify_chemyx_movement.py",
    "scripts\diagnostics\03_check_nmr_connection.py",
    "scripts\diagnostics\04_run_nmr_1d_acquisition.py",
    "nmr_template\experiment_data_1782760626.959469.dx",
    "offline\arduino_toolchain.ps1",
    "offline\serial_drivers.ps1",
    "tests\test_three_instrument_si6.py",
    "docs\THREE_INSTRUMENT_ARCHITECTURE.md",
    "docs\LIVE_COMMISSIONING_CHECKLIST.md",
    "docs\OFFLINE_SETUP.md"
)
foreach ($relativePath in $requiredRelativePaths) {
    if (-not (Test-Path (Join-Path $repo $relativePath))) {
        throw "Offline transfer is incomplete; missing $relativePath"
    }
}
Write-Host "      all $($requiredRelativePaths.Count) required files present"

Write-Host "[5/7] Machine config"
$localConfig = Join-Path $repo "configs\machines\00_machine.local.yaml"
if (Test-Path $localConfig) {
    Write-Host "      keeping existing $localConfig"
} else {
    Copy-Item (Join-Path $repo "configs\machines\00_machine.example.yaml") $localConfig
    Write-Host "      created $localConfig"
    Write-Host "      EDIT chemyx.serial_port to the pump's COM port on this laptop."
}

Write-Host "[6/7] Validating the Si6 workflows without hardware"
& $venvPython -B scripts\02_si6_automated_nmr.py --validate-only --machine-config $localConfig
if ($LASTEXITCODE -ne 0) { throw "Workflow validation failed." }
& $venvPython -B scripts\01_three_instrument_system_test.py --machine-config $localConfig
if ($LASTEXITCODE -ne 0) { throw "Three-instrument diagnostic configuration validation failed." }
& $venvPython -B scripts\02_si6_experiment.py --machine-config $localConfig
if ($LASTEXITCODE -ne 0) { throw "Three-instrument experiment configuration validation failed." }

Write-Host "[7/7] Needle firmware toolchain"
if (Test-Path (Join-Path $offline "arduino\arduino-cli.exe")) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $offline "arduino_toolchain.ps1") -Compile
    if ($LASTEXITCODE -ne 0) { throw "Offline firmware compile failed." }
} else {
    Write-Host "      offline\arduino was not transferred. Upload firmware 1.1.0 before going"
    Write-Host "      offline, or copy that bundle to reflash here (docs\OFFLINE_SETUP.md)."
}

if ($RunTests) {
    Write-Host ""
    Write-Host "Running the offline test suites"
    & $venvPython -B -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Offline tests failed." }
    # The Arduino host-controller suite sits outside the default testpaths.
    & $venvPython -B -m pytest -q -p no:cacheprovider arduino\tests
    if ($LASTEXITCODE -ne 0) { throw "Offline Arduino controller tests failed." }
    Write-Host "Running the Level 1 and Level 2 mock workflows (no hardware)"
    & $venvPython -B scripts\01_three_instrument_system_test.py --mock --all --machine-config $localConfig
    if ($LASTEXITCODE -ne 0) { throw "Level 1 mock workflow failed." }
    & $venvPython -B scripts\02_si6_experiment.py --mock --machine-config $localConfig
    if ($LASTEXITCODE -ne 0) { throw "Level 2 mock workflow failed." }
}

Write-Host ""
Write-Host "Offline install complete. Next steps are in docs\LIVE_COMMISSIONING_CHECKLIST.md."
