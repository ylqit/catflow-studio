[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$LegacyEnv = (Join-Path $PSScriptRoot '..\..\cat-video-generator\.env')
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$arguments = @((Join-Path $PSScriptRoot 'legacy/import_legacy_assets.py'), '--legacy-env', $LegacyEnv)
if ($Apply) { $arguments += '--apply' }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Legacy approved-asset import failed.' }
