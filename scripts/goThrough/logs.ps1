#Requires -Version 7.0
<#
.SYNOPSIS
    Tail Athena container logs, optionally filtered by service.

.DESCRIPTION
    Equivalent of `docker compose logs -f` with a friendlier interface and
    service alias resolution. Defaults to following all services.

.PARAMETER Services
    Comma-separated services to show (default: all).

.PARAMETER Follow
    Follow log output (default: true). Use -Follow:$false for a one-shot dump.

.PARAMETER Tail
    Number of lines to show from the end (default 200).

.PARAMETER Debug
    Target the debug compose project.

.EXAMPLE
    .\scripts\goThrough\logs.ps1
    .\scripts\goThrough\logs.ps1 -Services api,nginx
    .\scripts\goThrough\logs.ps1 -Services backend -Follow:$false -Tail 50
#>
[CmdletBinding()]
param(
    [string]$Services,
    [switch]$Follow = $true,
    [int]$Tail = 200
)
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    $svcList = if ($Services) { $Services -split ',' | ForEach-Object { (Resolve-Service -Name $_.Trim()) } } else { @() }
    $logArgs = @('logs', '--tail', [string]$Tail)
    if ($Follow) { $logArgs += '-f' }
    Write-Step "Logs: $(if ($svcList) { $svcList -join ', ' } else { 'all' })$($(if ($Follow) { ' (following)' } else { '' }))"
    # logs -f runs until Ctrl-C; a non-zero exit on interrupt is expected.
    $null = Invoke-Compose -CommandArgs $logArgs -Services $svcList -Debug:$Debug
    exit 0
} catch {
    Write-Err "Logs failed: $($_.Exception.Message)"
    exit 1
}