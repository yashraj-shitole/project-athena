#Requires -Version 7.0
<#
.SYNOPSIS
    Deep disk cleanup: remove athena-* images, build cache, and (optionally) volumes.

.DESCRIPTION
    More aggressive than clean.ps1. Removes the built athena-api/athena-nginx/
    athena-web-dev images and all Docker build cache to reclaim disk. With
    -Volumes also removes the project's named volumes (DESTRUCTIVE).

    The running stack is left untouched unless -Volumes is set (which also
    stops it, since you can't remove a volume in use).

.PARAMETER Volumes
    Also remove named volumes (DESTRUCTIVE  -  stops the stack first).

.EXAMPLE
    .\goThrough\prune.ps1
    .\goThrough\prune.ps1 -Volumes
#>
[CmdletBinding()]
param([switch]$Volumes)
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if ($Volumes) {
        Write-Warn '-Volumes will stop the stack and delete pgdata, ollama_data, api_storage.'
        if (-not (Confirm-Action 'Remove images, cache, AND volumes?' -DefaultNo)) { Write-Info 'Aborted.'; exit 0 }
        Write-Step 'Stopping the stack (required to release volumes)'
        $null = Invoke-Compose -CommandArgs @('down', '-v')
    }

    Write-Step 'Removing athena-* images'
    foreach ($img in @('athena-api', 'athena-nginx', 'athena-web-dev')) {
        & docker image rm -f $img 2>$null | Out-Null
    }
    Write-Step 'Pruning build cache and unused images'
    & docker builder prune -a -f 2>$null | Out-Null
    & docker image prune -a -f --filter "until=1h" 2>$null | Out-Null

    if ($Volumes) {
        Write-Step 'Removing project volumes'
        foreach ($v in @('pgdata', 'ollama_data', 'api_storage', 'webdev_node_modules')) {
            & docker volume rm "$($script:PROJECT_NAME)_$v" 2>$null | Out-Null
        }
    }
    Write-Ok 'Prune complete.'
    exit 0
} catch {
    Write-Err "Prune failed: $($_.Exception.Message)"
    exit 1
}