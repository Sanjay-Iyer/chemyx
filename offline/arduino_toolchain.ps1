<#
.SYNOPSIS
    Portable Arduino CLI and UNO R4 core for the needle firmware, usable offline.

.DESCRIPTION
    Everything lives in offline\arduino\ (ignored by Git; copy it with the
    repository folder). The needle_controller sketch includes only the Arduino
    core and C library headers, so no third-party Arduino library is needed.

    On a computer WITH internet, from the repository root:

        powershell -ExecutionPolicy Bypass -File offline\arduino_toolchain.ps1 -Build

    Downloads arduino-cli (SHA-256 verified), installs the "Arduino UNO R4
    Boards" core (arduino:renesas_uno) with its compiler and upload tools, and
    compiles the firmware once to prove the bundle. Driver post-install scripts
    are skipped; see docs\OFFLINE_SETUP.md for the upload driver.

    On any computer, offline, with no hardware:

        powershell -ExecutionPolicy Bypass -File offline\arduino_toolchain.ps1 -Compile

    Compiles arduino\firmware\needle_controller with network access blocked.

    On the instrument laptop only, after the wiring review and with 24 V off:

        powershell -ExecutionPolicy Bypass -File offline\arduino_toolchain.ps1 -Upload -Port COM5
#>
param(
    [switch]$Build,
    [switch]$Compile,
    [switch]$Upload,
    [string]$Port = "",
    [string]$CliVersion = "1.5.1",
    [string]$CliSha256 = "fabe42e0eb04d00e776a66178299ff95a46c623dbc260f997e58fd514853dd40",
    [string]$Core = "arduino:renesas_uno"
)

$ErrorActionPreference = "Stop"
$offline = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $offline
$bundle = Join-Path $offline "arduino"
$cli = Join-Path $bundle "arduino-cli.exe"
$sketch = Join-Path $repo "arduino\firmware\needle_controller"
$buildDir = Join-Path $bundle "build\needle_controller"
$buildScratch = Join-Path $bundle "build\scratch"
$fqbn = "arduino:renesas_uno:minima"

# Keep every arduino-cli directory inside the bundle, never in %LOCALAPPDATA%.
$env:ARDUINO_DIRECTORIES_DATA = Join-Path $bundle "data"
$env:ARDUINO_DIRECTORIES_DOWNLOADS = Join-Path $bundle "staging"
$env:ARDUINO_DIRECTORIES_USER = Join-Path $bundle "user"
$env:ARDUINO_UPDATER_ENABLE_NOTIFICATION = "false"

function Invoke-Cli {
    & $cli @args
    if ($LASTEXITCODE -ne 0) { throw "arduino-cli $($args -join ' ') failed (exit $LASTEXITCODE)." }
}

function Invoke-OfflineCompile {
    if (-not (Test-Path $cli)) { throw "Missing $cli. Build the bundle first with -Build." }
    # The bundled 32-bit gcc cannot open files past the 260-character Windows
    # path limit, and its include search paths add about 200 characters.
    if ($repo.Length -gt 50) {
        throw ("The repository path '$repo' is $($repo.Length) characters. Move the folder " +
            "to a short path such as C:\code\chemyx_pump (50 characters at most) to compile or upload firmware.")
    }
    # A dead proxy makes any network attempt fail, proving the compile is offline.
    $names = @("HTTP_PROXY", "HTTPS_PROXY", "ARDUINO_NETWORK_PROXY")
    $saved = @{}
    foreach ($name in $names) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, "http://127.0.0.1:9", "Process")
    }
    try {
        New-Item -ItemType Directory -Force -Path $buildScratch | Out-Null
        Invoke-Cli compile --fqbn $fqbn --build-path $buildScratch --output-dir $buildDir $sketch
    } finally {
        foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
    }
    $binary = Get-Item (Join-Path $buildDir "needle_controller.ino.bin") -ErrorAction SilentlyContinue
    if (-not $binary) { throw "Compile produced no needle_controller.ino.bin in $buildDir." }
    Write-Host "      compiled -> $($binary.FullName) ($($binary.Length) bytes)"
}

if (-not ($Build -or $Compile -or $Upload)) {
    throw "Choose -Build (internet), -Compile (offline check), or -Upload -Port COMx (instrument laptop)."
}

if ($Build) {
    Write-Host "[1/4] Downloading arduino-cli $CliVersion"
    New-Item -ItemType Directory -Force -Path $bundle | Out-Null
    $zip = Join-Path $bundle "arduino-cli_${CliVersion}_Windows_64bit.zip"
    $url = "https://github.com/arduino/arduino-cli/releases/download/v$CliVersion/arduino-cli_${CliVersion}_Windows_64bit.zip"
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $hash = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLower()
    if ($hash -ne $CliSha256.ToLower()) {
        Remove-Item $zip
        throw "arduino-cli checksum mismatch: expected $CliSha256, got $hash."
    }
    Expand-Archive -Path $zip -DestinationPath $bundle -Force

    Write-Host "[2/4] Installing $Core and its compiler/upload tools"
    Invoke-Cli core update-index
    Invoke-Cli core install $Core --skip-post-install

    Write-Host "[3/4] Compiling needle_controller with the network blocked"
    Invoke-OfflineCompile

    Write-Host "[4/4] Writing offline\arduino\ARDUINO_MANIFEST.txt"
    $lines = @("arduino-cli $CliVersion windows 64-bit zip sha256 $hash")
    $lines += (& $cli version)
    $lines += (& $cli core list)
    foreach ($file in @(Get-ChildItem -Path (Join-Path $bundle "staging") -File -Recurse)) {
        $lines += "{0}  {1,12}  {2}" -f (Get-FileHash -Algorithm SHA256 -Path $file.FullName).Hash, $file.Length, $file.FullName.Substring($bundle.Length + 1)
    }
    Set-Content -Path (Join-Path $bundle "ARDUINO_MANIFEST.txt") -Value $lines -Encoding ASCII
    $mb = [math]::Round(((Get-ChildItem $bundle -File -Recurse | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
    Write-Host "Done: offline\arduino is $mb MB."
}

if ($Compile -and -not $Build) {
    Write-Host "Compiling needle_controller with the network blocked"
    Invoke-OfflineCompile
}

if ($Upload) {
    if (-not $Port) { throw "-Upload needs -Port COMx (the Arduino UNO R4 Minima port)." }
    Invoke-OfflineCompile
    Write-Host "Uploading to $Port (UNO R4 Minima). Keep 24 V off until identity is verified."
    Invoke-Cli upload --port $Port --fqbn $fqbn --input-dir $buildDir
}
