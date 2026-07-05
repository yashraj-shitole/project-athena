#Requires -Version 7.0
<#
.SYNOPSIS
    Restart Athena services (docker compose restart).

.DESCRIPTION
    Restarts the given services (or all) without removing containers or
    volumes. Useful after changing env vars in your shell without a full
    down/up cycle. To pick up source changes, use docker-up -Debug (hot
    reload) instead.

.PARAMETER Services
    Comma-separated services to restart (default: all).

.PARAMETER Debug
    Target the debug compose project.

.EXAMPLE
    .\goThrough\docker-restart.ps1
    .\goThrough\docker-restart.ps1 -Services api
#>
[CmdletBinding()]
param(
    [string]$Services
)
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    $svcList = if ($Services) { $Services -split ',' | ForEach-Object { (Resolve-Service -Name $_.Trim()) } } else { @() }
    Write-Step "Restarting: $(if ($svcList) { $svcList -join ', ' } else { 'all services' })"
    if (-not (Invoke-Compose -CommandArgs @('restart') -Services $svcList -Debug:$Debug)) { exit 1 }
    Write-Ok 'Restart complete.'
    exit 0
} catch {
    Write-Err "Restart failed: $($_.Exception.Message)"
    exit 1
}