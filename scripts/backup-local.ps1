[CmdletBinding()]
param(
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Destination) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Destination = Join-Path $projectRoot "var\backups\catflow-$stamp.zip"
}
& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'local_backup.py') backup $Destination
if ($LASTEXITCODE -ne 0) { throw 'CatFlow backup failed.' }
