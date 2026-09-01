[CmdletBinding()]
param(
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$environmentFile = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $environmentFile) {
    foreach ($line in Get-Content -LiteralPath $environmentFile) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
        }
    }
}
. (Join-Path $PSScriptRoot 'runtime-paths.ps1')
$runtimePaths = Get-CatFlowRuntimePaths -ProjectRoot $projectRoot
if (-not $Destination) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Destination = Join-Path $runtimePaths.BackupRoot "catflow-$stamp.zip"
}
& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'local_backup.py') backup $Destination
if ($LASTEXITCODE -ne 0) { throw 'CatFlow backup failed.' }
