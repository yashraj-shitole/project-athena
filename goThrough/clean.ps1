#Requires -Version 7.0
<#
.SYNOPSIS
    Remove build artifacts and temporary files (non-destructive to data).

.DESCRIPTION
    Cleans:
      - on-disk build artifacts: backend __pycache__/.pytest_cache/.mypy_cache/
        .ruff_cache/.coverage/htmlcov, frontend dist/.vite, node logs
      - stopped/dangling project containers and dangling images
      - Docker build cache (builder prune)

    Does NOT remove named volumes, the running stack, or your source. Use
    prune.ps1 for a deeper disk cleanup or reset.ps1 to wipe the stack.

.EXAMPLE
    .\goThrough\clean.ps1
    .\goThrough\clean.ps1 -Verbose
#>
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

function Remove-Glob {
    param([string[]]$Paths)
    foreach ($p in $Paths) {
        Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
}

try {
    if (-not (Test-Prereqs)) { exit 1 }

    Write-Step 'Removing on-disk build artifacts'
    $artifacts = @(
        'backend\**\__pycache__',
        'backend\.pytest_cache',
        'backend\.mypy_cache',
        'backend\.ruff_cache',
        'backend\.coverage',
        'backend\htmlcov',
        'frontend\dist',
        'frontend\.vite',
        '*.log'
    )
    Remove-Glob -Paths $artifacts
    Write-Ok 'On-disk artifacts removed.'

    Write-Step 'Pruning stopped/dangling containers and dangling images'
    & docker container prune -f 2>$null | Out-Null
    & docker image prune -f 2>$null | Out-Null
    & docker builder prune -f 2>$null | Out-Null
    Write-Ok 'Dangling images / build cache pruned.'

    Write-Info 'Volumes and the running stack were left intact.'
    Write-Info 'For a full wipe: .\reset.ps1 -Volumes   | for deep disk cleanup: .\goThrough\prune.ps1'
    exit 0
} catch {
    Write-Err "Clean failed: $($_.Exception.Message)"
    exit 1
}