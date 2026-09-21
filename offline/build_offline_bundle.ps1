<#
.SYNOPSIS
    Download everything the offline laptop needs into offline\ (needs internet).

.DESCRIPTION
    Run on a Windows computer that HAS internet, from the repository root:

        powershell -ExecutionPolicy Bypass -File offline\build_offline_bundle.ps1 -Python C:\path\to\python.exe

    -Python must be a 64-bit CPython 3.11 with pip. The wheels it collects only
    work on 64-bit Windows with Python 3.11, which is what the offline laptop
    runs.

    Fills these folders (all ignored by Git):
        offline\wheelhouse\            every package in requirements-lock.txt
        offline\installers\            python-3.11.9-amd64.exe from python.org
        offline\BUNDLE_MANIFEST.txt    size and SHA-256 of every file above

    Then copy the whole repository folder to the offline laptop and run
    offline\install_offline.ps1 there. See docs\OFFLINE_SETUP.md.
#>
param(
    [string]$Python = "python",
    [string]$PythonInstallerVersion = "3.11.9",
    [switch]$SkipPythonInstaller
)

$ErrorActionPreference = "Stop"
$offline = Split-Path -Parent $MyInvocation.MyCommand.Path
$lock = Join-Path $offline "requirements-lock.txt"
$wheelhouse = Join-Path $offline "wheelhouse"
$installers = Join-Path $offline "installers"

Write-Host "[1/5] Checking the Python used to collect wheels"
$probe = "import sys, sysconfig; print('%d.%d %s' % (sys.version_info[0], sys.version_info[1], sysconfig.get_platform()))"
$tag = & $Python -c $probe
if ($LASTEXITCODE -ne 0) {
    throw "Could not run '$Python'. Pass -Python with the full path to python.exe."
}
if ($tag -ne "3.11 win-amd64") {
    throw "Need 64-bit Windows Python 3.11 to collect matching wheels; '$Python' is '$tag'."
}

New-Item -ItemType Directory -Force -Path $wheelhouse, $installers | Out-Null

Write-Host "[2/5] Collecting wheels into $wheelhouse"
& $Python -m pip wheel --disable-pip-version-check --wheel-dir $wheelhouse -r $lock
if ($LASTEXITCODE -ne 0) { throw "pip wheel failed (exit $LASTEXITCODE)." }

Write-Host "[3/5] Proving the wheelhouse installs without the network"
& $Python -m pip install --dry-run --ignore-installed --no-index --disable-pip-version-check `
    --find-links $wheelhouse -r $lock
if ($LASTEXITCODE -ne 0) { throw "The wheelhouse is incomplete; see the pip error above." }

if ($SkipPythonInstaller) {
    Write-Host "[4/5] Skipped the Python installer (-SkipPythonInstaller)"
} else {
    $exe = "python-$PythonInstallerVersion-amd64.exe"
    $target = Join-Path $installers $exe
    if (Test-Path $target) {
        Write-Host "[4/5] $exe is already present"
    } else {
        $url = "https://www.python.org/ftp/python/$PythonInstallerVersion/$exe"
        Write-Host "[4/5] Downloading $url"
        [Net.ServicePointManager]::SecurityProtocol = `
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing
    }
    $signature = Get-AuthenticodeSignature -FilePath $target
    if ($signature.Status -ne "Valid" -or
        $signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        throw "$exe does not carry a valid Python Software Foundation signature; delete it and rerun."
    }
}

Write-Host "[5/5] Writing the manifest"
$manifest = Join-Path $offline "BUNDLE_MANIFEST.txt"
$files = @(Get-ChildItem -Path $wheelhouse, $installers -File -Recurse)
$lines = foreach ($file in $files) {
    $hash = (Get-FileHash -Algorithm SHA256 -Path $file.FullName).Hash
    "{0}  {1,12}  {2}" -f $hash, $file.Length, $file.FullName.Substring($offline.Length + 1)
}
Set-Content -Path $manifest -Value $lines -Encoding ASCII

$totalMb = [math]::Round((($files | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Write-Host ""
Write-Host "Done: $($files.Count) files, $totalMb MB. Manifest: $manifest"
Write-Host "Next: copy the whole repository folder to the offline laptop, then run"
Write-Host "      powershell -ExecutionPolicy Bypass -File offline\install_offline.ps1"
