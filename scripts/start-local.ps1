[CmdletBinding()]
param(
    [switch]$SkipWebBuild,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$environmentFile = Join-Path $projectRoot '.env'

if (-not (Test-Path -LiteralPath $environmentFile)) {
    throw 'The ignored .env file is missing. Run scripts\configure-existing-postgres.ps1 first.'
}

foreach ($line in Get-Content -LiteralPath $environmentFile) {
    if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
$env:CATFLOW_ROOT = $projectRoot
$catflowPort = if ($env:CATFLOW_PORT) { [int]$env:CATFLOW_PORT } else { 8877 }
. (Join-Path $PSScriptRoot 'runtime-paths.ps1')
$runtimePaths = Get-CatFlowRuntimePaths -ProjectRoot $projectRoot
$runtimeDirectory = $runtimePaths.WorkRoot
$logDirectory = $runtimePaths.LogRoot
$pidFile = Join-Path $runtimeDirectory 'local-processes.json'
$workerReadyFile = Join-Path $runtimeDirectory 'worker-ready.json'
$workerSupervisorFile = Join-Path $runtimeDirectory 'worker-supervisor.json'

New-Item -ItemType Directory -Force -Path $runtimeDirectory, $logDirectory | Out-Null

if (Test-Path -LiteralPath $pidFile) {
    $recorded = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $live = @($recorded.apiPid, $recorded.workerSupervisorPid, $recorded.workerPid) | Where-Object {
        $null -ne $_ -and $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue)
    }
    if ($live.Count -gt 0) {
        throw 'CatFlow already has recorded local processes. Run scripts\stop-local.ps1 first.'
    }
    Remove-Item -LiteralPath $pidFile
}
if (Test-Path -LiteralPath $workerReadyFile) {
    Remove-Item -LiteralPath $workerReadyFile
}
if (Test-Path -LiteralPath $workerSupervisorFile) {
    Remove-Item -LiteralPath $workerSupervisorFile
}

$portOwner = Get-NetTCPConnection -LocalPort $catflowPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $portOwner) {
    $ownerProcess = Get-Process -Id $portOwner.OwningProcess -ErrorAction SilentlyContinue
    $ownerName = if ($null -ne $ownerProcess) { $ownerProcess.ProcessName } else { 'unknown' }
    throw "Port $catflowPort is already used by PID $($portOwner.OwningProcess) ($ownerName). Stop that service before starting CatFlow."
}

if (-not $SkipWebBuild) {
    & npm --prefix (Join-Path $projectRoot 'apps\web') run build
    if ($LASTEXITCODE -ne 0) { throw 'Vue production build failed.' }
}

$alembic = Join-Path $projectRoot '.venv\Scripts\alembic.exe'
& $alembic -c (Join-Path $projectRoot 'services\api\alembic.ini') upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Alembic migration failed.' }

$venvPythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonExecutable = (& $venvPythonExecutable -c "import sys; print(getattr(sys, '_base_executable', sys.executable))").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Unable to resolve the Python interpreter behind the virtual environment launcher.'
}
$env:__PYVENV_LAUNCHER__ = $venvPythonExecutable
$apiStart = @{
    FilePath = $pythonExecutable
    ArgumentList = @('-m', 'catflow.interfaces.cli', 'serve', '--port', $catflowPort.ToString())
    WorkingDirectory = $projectRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = (Join-Path $logDirectory 'api.out.log')
    RedirectStandardError = (Join-Path $logDirectory 'api.err.log')
    PassThru = $true
}
$apiProcess = Start-Process @apiStart
$workerStart = @{
    FilePath = $pythonExecutable
    ArgumentList = @('-m', 'catflow_worker.cli', 'supervise')
    WorkingDirectory = $projectRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = (Join-Path $logDirectory 'worker.out.log')
    RedirectStandardError = (Join-Path $logDirectory 'worker.err.log')
    PassThru = $true
}
$workerSupervisorProcess = Start-Process @workerStart

@{
    apiPid = $apiProcess.Id
    workerSupervisorPid = $workerSupervisorProcess.Id
    startedAt = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8

$ready = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    if ($apiProcess.HasExited -or $workerSupervisorProcess.HasExited) { break }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$catflowPort/api/v1/health" -TimeoutSec 1
        if ($health.status -eq 'ok') {
            $bootstrap = Invoke-RestMethod -Uri "http://127.0.0.1:$catflowPort/api/v1/runtime/bootstrap" -TimeoutSec 1
            if ($bootstrap.databaseReady -and $bootstrap.worker.ready -and $bootstrap.ffmpegReady -and $bootstrap.ffprobeReady) {
                $ready = $true
                break
            }
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $ready) {
    & (Join-Path $PSScriptRoot 'stop-local.ps1')
    $apiErrorLog = Join-Path $logDirectory 'api.err.log'
    $tail = if (Test-Path -LiteralPath $apiErrorLog) { Get-Content -LiteralPath $apiErrorLog -Tail 30 } else { 'No API error log was created.' }
    throw "CatFlow API did not become ready within 30 seconds.`n$($tail -join [Environment]::NewLine)"
}

Write-Host 'CatFlow API, PostgreSQL connection and Worker are ready.'
Write-Host "Logs: $logDirectory"
if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:$catflowPort/projects"
}
