<#
.SYNOPSIS
  Programs the AT firmware from scripts/build.ps1 into one or more nRF54L15 DKs.

.EXAMPLE
  .\scripts\flash.ps1 -SerialNumber 1057771188
  .\scripts\flash.ps1 -SerialNumber 1057771188,1057766689,1057786049 -Erase

  -Erase wipes the whole chip, including S-registers and Zigbee NVRAM.
  List connected DKs and their serial numbers with: nrfutil device list
#>
param(
  [Parameter(Mandatory)] [string[]]$SerialNumber,
  [switch]$Erase,
  [string]$Workspace = 'C:\ncs\ncs-zigbee',
  [string]$NcsVersion = 'v3.4.0',
  [string]$NrfUtil = 'nrfutil'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$buildDir = [IO.Path]::GetRelativePath($Workspace, (Join-Path $repo 'build'))
$extra = @()
if ($Erase) { $extra += '--erase' }

Push-Location $Workspace
try {
  foreach ($sn in $SerialNumber) {
    Write-Host "=== Flashing $sn"
    & $NrfUtil sdk-manager toolchain launch --ncs-version $NcsVersion -- `
      west flash --build-dir $buildDir --dev-id $sn @extra
    if ($LASTEXITCODE -ne 0) { throw "Flash of $sn failed ($LASTEXITCODE)" }
  }
} finally {
  Pop-Location
}
