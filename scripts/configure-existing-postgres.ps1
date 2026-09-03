[CmdletBinding()]
param(
    [string]$SourceEnv = (Join-Path $PSScriptRoot '..\..\cat-video-generator\.env'),
    [string]$DatabaseName = 'catflow_studio'
)

$ErrorActionPreference = 'Stop'

if ($DatabaseName -notmatch '^[a-zA-Z][a-zA-Z0-9_]{0,62}$') {
    throw 'DatabaseName must be a PostgreSQL identifier containing only letters, digits, and underscores.'
}

$resolvedSource = (Resolve-Path -LiteralPath $SourceEnv).Path
$targetEnv = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..')).Path '.env'
$sourceValues = @{}

foreach ($line in Get-Content -LiteralPath $resolvedSource) {
    if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $sourceValues[$matches[1]] = $matches[2]
    }
}

$requiredKeys = @(
    'CAT_VIDEO_DB_HOST',
    'CAT_VIDEO_DB_PORT',
    'CAT_VIDEO_DB_USER',
    'CAT_VIDEO_DB_PASSWORD',
    'CAT_VIDEO_DB_SSLMODE'
)
foreach ($key in $requiredKeys) {
    if (-not $sourceValues.ContainsKey($key)) {
        throw "Source environment is missing $key."
    }
}

$targetLines = @(
    "CATFLOW_DB_HOST=$($sourceValues['CAT_VIDEO_DB_HOST'])",
    "CATFLOW_DB_PORT=$($sourceValues['CAT_VIDEO_DB_PORT'])",
    "CATFLOW_DB_NAME=$DatabaseName",
    "CATFLOW_DB_USER=$($sourceValues['CAT_VIDEO_DB_USER'])",
    "CATFLOW_DB_PASSWORD=$($sourceValues['CAT_VIDEO_DB_PASSWORD'])",
    "CATFLOW_DB_SSLMODE=$($sourceValues['CAT_VIDEO_DB_SSLMODE'])",
    'CATFLOW_MEDIA_ROOT=var/media',
    'CATFLOW_WORK_ROOT=var/work',
    'CATFLOW_CANON_ROOT=assets/canon/v4',
    'CATFLOW_LOG_ROOT=var/logs',
    'CATFLOW_BACKUP_ROOT=var/backups',
    'CATFLOW_PAID_CALLS_ENABLED=false'
)

foreach ($providerKey in @(
    'ARK_API_KEY',
    'ARK_BASE_URL',
    'ARK_PLANNING_MODEL',
    'ARK_IMAGE_MODEL',
    'ARK_VIDEO_MODEL',
    'FFMPEG_PATH',
    'FFPROBE_PATH'
)) {
    if ($sourceValues.ContainsKey($providerKey)) {
        $targetLines += "$providerKey=$($sourceValues[$providerKey])"
    }
}

[System.IO.File]::WriteAllLines(
    $targetEnv,
    $targetLines,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "CatFlow database configuration written to the ignored .env file."
Write-Host "Target database: $DatabaseName"
