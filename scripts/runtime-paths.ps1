function Resolve-CatFlowRepositoryPath {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$EnvironmentName,
        [Parameter(Mandatory = $true)][string]$DefaultValue
    )

    $configured = [Environment]::GetEnvironmentVariable($EnvironmentName, 'Process')
    if ([string]::IsNullOrWhiteSpace($configured)) { $configured = $DefaultValue }
    # IsPathRooted supports Windows PowerShell 5.1 and also rejects drive-relative
    # (C:media) and root-relative (\media) paths, which are not repository-relative.
    if ([System.IO.Path]::IsPathRooted($configured)) {
        throw "$EnvironmentName must be a relative repository path."
    }
    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\', '/')
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $root $configured))
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    if (-not $candidate.Equals($root, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$EnvironmentName must remain inside the repository."
    }
    return $candidate
}

function Get-CatFlowRuntimePaths {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $root = [System.IO.Path]::GetFullPath($ProjectRoot)
    return [pscustomobject]@{
        ProjectRoot = $root
        MediaRoot = Resolve-CatFlowRepositoryPath $root 'CATFLOW_MEDIA_ROOT' 'var/media'
        WorkRoot = Resolve-CatFlowRepositoryPath $root 'CATFLOW_WORK_ROOT' 'var/work'
        CanonRoot = Resolve-CatFlowRepositoryPath $root 'CATFLOW_CANON_ROOT' 'assets/canon/v4'
        LogRoot = Resolve-CatFlowRepositoryPath $root 'CATFLOW_LOG_ROOT' 'var/logs'
        BackupRoot = Resolve-CatFlowRepositoryPath $root 'CATFLOW_BACKUP_ROOT' 'var/backups'
    }
}
