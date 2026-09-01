[CmdletBinding()]
param()

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
$pidFile = Join-Path $runtimePaths.WorkRoot 'local-processes.json'
$workerReadyFile = Join-Path $runtimePaths.WorkRoot 'worker-ready.json'

if (-not (Test-Path -LiteralPath $pidFile)) {
    if (Test-Path -LiteralPath $workerReadyFile) {
        Remove-Item -LiteralPath $workerReadyFile
    }
    Write-Host 'No CatFlow process record exists; business data was not touched.'
    return
}

$recorded = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
foreach ($processId in @($recorded.apiPid, $recorded.workerPid)) {
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        Stop-Process -Id $processId
        [void]$process.WaitForExit(10000)
    }
}
Remove-Item -LiteralPath $pidFile
if (Test-Path -LiteralPath $workerReadyFile) {
    Remove-Item -LiteralPath $workerReadyFile
}
Write-Host 'CatFlow API and Worker stopped. PostgreSQL, media, configuration and backups were preserved.'
