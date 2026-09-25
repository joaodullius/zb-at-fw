<#
.SYNOPSIS
  Builds the AT firmware (app/) for the nRF54L15 DK into build/.

.EXAMPLE
  .\scripts\build.ps1
  .\scripts\build.ps1 -Pristine
  .\scripts\build.ps1 -Workspace D:\ncs\ncs-zigbee -NrfUtil nrfutil
#>
param(
  # west workspace created with: west init -m https://github.com/nrfconnect/ncs-zigbee --mr v1.4.0
  [string]$Workspace = 'C:\ncs\ncs-zigbee',
  [string]$NcsVersion = 'v3.4.0',
  [string]$Board = 'nrf54l15dk/nrf54l15/cpuapp',
  [string]$NrfUtil = 'nrfutil',
  [switch]$Pristine
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$source = (Join-Path $repo 'app') -replace '\\', '/'
# A relative build directory avoids drive-letter mangling through "nrfutil ... launch".
$buildDir = [IO.Path]::GetRelativePath($Workspace, (Join-Path $repo 'build'))
$pristineArg = if ($Pristine) { 'always' } else { 'auto' }

Push-Location $Workspace
try {
  & $NrfUtil sdk-manager toolchain launch --ncs-version $NcsVersion -- `
    west build -p $pristineArg -b $Board $source --build-dir $buildDir
  if ($LASTEXITCODE -ne 0) { throw "Build failed ($LASTEXITCODE)" }
} finally {
  Pop-Location
}
