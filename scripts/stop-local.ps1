[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidFile = Join-Path $projectRoot 'var\work\local-processes.json'

if (-not (Test-Path -LiteralPath $pidFile)) {
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
Write-Host 'CatFlow API and Worker stopped. PostgreSQL, media, configuration and backups were preserved.'
