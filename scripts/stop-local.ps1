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
$workerSupervisorFile = Join-Path $runtimePaths.WorkRoot 'worker-supervisor.json'

function Stop-RecordedProcess {
    param([AllowNull()][object]$ProcessId)
    if ($null -eq $ProcessId) { return }
    $process = Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        Stop-Process -Id ([int]$ProcessId)
        [void]$process.WaitForExit(10000)
    }
}

if (-not (Test-Path -LiteralPath $pidFile)) {
    if (Test-Path -LiteralPath $workerReadyFile) {
        Remove-Item -LiteralPath $workerReadyFile
    }
    if (Test-Path -LiteralPath $workerSupervisorFile) {
        Remove-Item -LiteralPath $workerSupervisorFile
    }
    Write-Host 'No CatFlow process record exists; business data was not touched.'
    return
}

$recorded = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
$workerProcessId = $recorded.workerPid
if (Test-Path -LiteralPath $workerSupervisorFile) {
    try {
        $supervisorState = Get-Content -LiteralPath $workerSupervisorFile -Raw | ConvertFrom-Json
        if ($null -ne $supervisorState.workerPid) {
            $workerProcessId = $supervisorState.workerPid
        }
    } catch {
        Write-Warning 'Worker supervisor state could not be read; only recorded process IDs will be stopped.'
    }
}
Stop-RecordedProcess -ProcessId $recorded.workerSupervisorPid
Stop-RecordedProcess -ProcessId $workerProcessId
Stop-RecordedProcess -ProcessId $recorded.apiPid
Remove-Item -LiteralPath $pidFile
if (Test-Path -LiteralPath $workerReadyFile) {
    Remove-Item -LiteralPath $workerReadyFile
}
if (Test-Path -LiteralPath $workerSupervisorFile) {
    Remove-Item -LiteralPath $workerSupervisorFile
}
Write-Host 'CatFlow API and Worker stopped. PostgreSQL, media, configuration and backups were preserved.'
