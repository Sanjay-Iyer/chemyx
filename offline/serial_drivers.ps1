<#
.SYNOPSIS
    Show the driver behind every COM port; export or install those drivers offline.

.DESCRIPTION
    List ports and drivers (any user; read-only):
        powershell -ExecutionPolicy Bypass -File offline\serial_drivers.ps1

    Export third-party drivers (Administrator PowerShell, on the laptop where the
    pump and Arduino already work, with both plugged in):
        powershell -ExecutionPolicy Bypass -File offline\serial_drivers.ps1 -Export

    Install the exported drivers (Administrator PowerShell, on the offline laptop):
        powershell -ExecutionPolicy Bypass -File offline\serial_drivers.ps1 -Install

    A driver whose INF is not oemNN.inf ships with Windows, for example
    usbser.inf for a USB CDC port, and needs nothing. Exported drivers go to
    offline\drivers\, which Git ignores.
#>
param(
    [switch]$Export,
    [switch]$Install,
    [string]$DriverDir = ""
)

$ErrorActionPreference = "Stop"
$offline = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $DriverDir) { $DriverDir = Join-Path $offline "drivers" }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DeviceProperty {
    param([string]$InstanceId, [string]$Key)
    try {
        return (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName $Key -ErrorAction Stop).Data
    } catch {
        return $null
    }
}

function Get-ComPortDrivers {
    $ports = @(Get-PnpDevice -Class Ports -PresentOnly -ErrorAction SilentlyContinue)
    foreach ($port in $ports) {
        $id = $port.InstanceId
        # FTDI ports sit under a separate "USB Serial Converter" parent with its
        # own driver, so the parent INF is reported (and exported) as well.
        $parent = Get-DeviceProperty $id "DEVPKEY_Device_Parent"
        $vidPid = "-"
        if ("$id $parent" -match "VID_([0-9A-Fa-f]{4})[&+]PID_([0-9A-Fa-f]{4})") {
            $vidPid = ("{0}:{1}" -f $Matches[1], $Matches[2]).ToUpper()
        }
        $parentInf = $null
        $parentName = $null
        if ($parent) {
            $parentInf = Get-DeviceProperty $parent "DEVPKEY_Device_DriverInfPath"
            $parentDevice = Get-PnpDevice -InstanceId $parent -ErrorAction SilentlyContinue
            if ($parentDevice) { $parentName = $parentDevice.FriendlyName }
        }
        [pscustomobject]@{
            Port       = $port.FriendlyName
            Status     = $port.Status
            VidPid     = $vidPid
            Provider   = Get-DeviceProperty $id "DEVPKEY_Device_DriverProvider"
            Version    = Get-DeviceProperty $id "DEVPKEY_Device_DriverVersion"
            Inf        = Get-DeviceProperty $id "DEVPKEY_Device_DriverInfPath"
            ParentName = $parentName
            ParentInf  = $parentInf
        }
    }
}

if ($Install) {
    if (-not (Test-Admin)) { throw "-Install must run in an Administrator PowerShell." }
    if (-not (Test-Path (Join-Path $DriverDir "*"))) { throw "No exported drivers in $DriverDir." }
    & pnputil.exe /add-driver (Join-Path $DriverDir "*.inf") /subdirs /install
    Write-Host "pnputil exit code $LASTEXITCODE (0 = installed, 3010 = installed, restart needed)."
    Write-Host "Unplug and replug the pump and Arduino, then run this script without switches to check."
    exit 0
}

$rows = @(Get-ComPortDrivers)
if ($rows.Count -eq 0) {
    Write-Host "No COM ports are present. Plug in the pump and the Arduino, then run again."
} else {
    Write-Host "COM ports on $env:COMPUTERNAME (an Inf of oemNN.inf is a third-party driver):"
    $rows | Format-List
    Write-Host "Arduino UNO R4 boards report VID 2341. The pump is the port that appears when its USB cable is plugged in."
}

if ($Export) {
    if (-not (Test-Admin)) { throw "-Export must run in an Administrator PowerShell." }
    $infs = @($rows | ForEach-Object { $_.Inf; $_.ParentInf } |
        Where-Object { $_ -match "^oem\d+\.inf$" } | Sort-Object -Unique)
    if ($infs.Count -eq 0) {
        Write-Host "Nothing to export: every COM port uses a driver built into Windows."
        exit 0
    }
    New-Item -ItemType Directory -Force -Path $DriverDir | Out-Null
    foreach ($inf in $infs) {
        $target = Join-Path $DriverDir ([IO.Path]::GetFileNameWithoutExtension($inf))
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        & pnputil.exe /export-driver $inf $target
        if ($LASTEXITCODE -ne 0) { throw "pnputil could not export $inf (exit $LASTEXITCODE)." }
    }
    $rows | Format-List | Out-File -FilePath (Join-Path $DriverDir "ports_at_export.txt") -Encoding ascii
    Write-Host "Exported $($infs.Count) driver package(s) to $DriverDir"
}
