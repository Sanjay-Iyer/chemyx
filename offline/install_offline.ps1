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
    4. Creates configs\machines\00_machine.local.yaml from the example if it is
       missing. Edit its COM port afterwards.
    5. Validates Workflow 02 without hardware. -RunTests also runs the offline
       test suite (about 2 minutes).

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

Write-Host "[1/5] Finding 64-bit Python 3.11"
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

Write-Host "[2/5] Installing packages into $venv (no network)"
if (-not (Test-Path $venvPython)) {
    & $pythonExe @pythonPrefix -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create $venv." }
}
& $venvPython -m pip install --no-index --disable-pip-version-check --find-links $wheelhouse -r $lock
if ($LASTEXITCODE -ne 0) { throw "Offline install failed; see the pip error above." }

Write-Host "[3/5] Checking imports"
& $venvPython -c "import serial, yaml, numpy, scipy, matplotlib, nmrglue, pandas, pyqtgraph, pytest; import PySide6.QtWidgets; print('      all packages import')"
if ($LASTEXITCODE -ne 0) { throw "A package failed to import." }

Write-Host "[4/5] Machine config"
$localConfig = Join-Path $repo "configs\machines\00_machine.local.yaml"
if (Test-Path $localConfig) {
    Write-Host "      keeping existing $localConfig"
} else {
    Copy-Item (Join-Path $repo "configs\machines\00_machine.example.yaml") $localConfig
    Write-Host "      created $localConfig"
    Write-Host "      EDIT chemyx.serial_port to the pump's COM port on this laptop."
}

Write-Host "[5/5] Validating Workflow 02 without hardware"
& $venvPython -B scripts\02_si6_automated_nmr.py --validate-only --machine-config $localConfig
if ($LASTEXITCODE -ne 0) { throw "Workflow validation failed." }

if ($RunTests) {
    Write-Host ""
    Write-Host "Running the offline test suite"
    & $venvPython -B -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Offline tests failed." }
}

Write-Host ""
Write-Host "Offline install complete. Next steps are in docs\OFFLINE_SETUP.md, section 4."
