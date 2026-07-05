#Requires -Version 7.0
<#
.SYNOPSIS
    Reset the stack: stop + remove containers + network (optionally volumes).

.DESCRIPTION
    `docker compose down` removes containers and the project network but
    KEEPS named volumes by default  -  so your DB, pulled model, and uploads
    survive a reset. Use -Volumes to also wipe the volumes (DESTRUCTIVE).

    Use this to "start over" cleanly. For artifact-only cleanup (no data
    loss) use clean.ps1; for image+cache reclamation use goThrough/prune.ps1.

.PARAMETER Volumes
    Also remove named volumes (pgdata, ollama_data, api_storage)  -  DESTRUCTIVE.

.PARAMETER Debug
    Target the debug compose project.

.PARAMETER Force
    Skip the confirmation prompt for -Volumes (for scripted use).

.EXAMPLE
    .\reset.ps1
    .\reset.ps1 -Volumes
    .\reset.ps1 -Volumes -Force
#>
[CmdletBinding()]
param(
    [switch]$Volumes,
    [switch]$Force
)
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
. (Join-Path $PSScriptRoot 'goThrough\_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if ($Volumes -and -not $Force) {
        Write-Warn '-Volumes will delete pgdata, ollama_data, api_storage (DB, model, uploads).'
        if (-not (Confirm-Action 'Remove volumes too?' -DefaultNo)) { Write-Info 'Aborted.'; exit 0 }
    }
    Write-Banner 'Project Athena  -  reset'
    $downArgs = @('down', '--remove-orphans')
    if ($Volumes) { $downArgs += '-v' }
    Write-Step "Bringing the stack down$($(if ($Volumes) { ' (with volumes)' } else { '' }))"
    if (-not (Invoke-Compose -CommandArgs $downArgs -Debug:$Debug)) { exit 1 }

    # Best-effort: remove any leftover project network that `down` may leave.
    $net = "$($script:PROJECT_NAME)_default"
    & docker network rm $net 2>$null | Out-Null

    Write-Ok 'Reset complete.'
    if ($Volumes) {
        Write-Info 'Volumes wiped. Next start re-creates the DB and re-pulls the model.'
        Write-Info 'Start fresh: .\build.ps1 ; .\docker-up.ps1'
    } else {
        Write-Info 'Volumes preserved. Start again: .\docker-up.ps1'
    }
    exit 0
} catch {
    Write-Err "Reset failed: $($_.Exception.Message)"
    exit 1
}