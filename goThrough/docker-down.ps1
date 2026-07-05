#Requires -Version 7.0
<#
.SYNOPSIS
    Stop and remove the Athena containers (docker compose down).

.DESCRIPTION
    By default removes containers and the project network but KEEPS named
    volumes (pgdata, ollama_data, api_storage) so your DB, pulled model, and
    uploads survive. Use -Volumes to also remove volumes (data loss).

.PARAMETER Volumes
    Also remove named volumes (DESTRUCTIVE  -  wipes DB, model, uploads).

.PARAMETER Debug
    Target the debug compose project.

.PARAMETER Services
    Optional comma-separated services to stop/remove (instead of everything).

.EXAMPLE
    .\goThrough\docker-down.ps1
    .\goThrough\docker-down.ps1 -Volumes
#>
[CmdletBinding()]
param(
    [switch]$Volumes,
    [string]$Services
)
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if ($Volumes) {
        Write-Warn '-Volumes will delete pgdata, ollama_data, api_storage (DB, model, uploads).'
        if (-not (Confirm-Action 'Remove volumes too?' -DefaultNo)) { Write-Info 'Aborted.'; exit 0 }
    }
    $downArgs = @('down')
    if ($Volumes) { $downArgs += '-v' }
    $svcList = if ($Services) { $Services -split ',' | ForEach-Object { (Resolve-Service -Name $_.Trim()) } } else { @() }
    Write-Step "Bringing the stack down$($(if ($Volumes) { ' (with volumes)' } else { '' }))"
    if (-not (Invoke-Compose -CommandArgs $downArgs -Services $svcList -Debug:$Debug)) { exit 1 }
    Write-Ok 'Stack down.'
    exit 0
} catch {
    Write-Err "Down failed: $($_.Exception.Message)"
    exit 1
}