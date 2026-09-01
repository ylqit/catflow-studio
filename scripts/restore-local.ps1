[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Archive,
    [switch]$Replace
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$arguments = @((Join-Path $PSScriptRoot 'local_backup.py'), 'restore', $Archive)
if ($Replace) { $arguments += '--replace' }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'CatFlow restore failed.' }
