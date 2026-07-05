#Requires -Version 7.0
<#
.SYNOPSIS
    Tail Athena container logs (root wrapper around goThrough/logs.ps1).

.DESCRIPTION
    Defaults to following all services. Filter with -Services; one-shot dump
    with -Follow:$false.

.PARAMETER Services
    Comma-separated services to show (default: all). Aliases accepted:
    backend, frontend, web-dev, db, redis, llm.

.PARAMETER Follow
    Follow log output (default: true). Use -Follow:$false for a one-shot dump.

.PARAMETER Tail
    Lines to show from the end (default 200).

.PARAMETER Debug
    Target the debug compose project.

.EXAMPLE
    .\logs.ps1
    .\logs.ps1 -Services api,nginx
    .\logs.ps1 -Services backend -Follow:$false -Tail 50
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
$inner = Join-Path $PSScriptRoot 'goThrough\logs.ps1'
$params = @{ Tail = $Tail }
if ($Services) { $params['Services'] = $Services }
if (-not $Follow) { $params['Follow'] = $false } else { $params['Follow'] = $true }
if ($Debug) { $params['Debug'] = $true }
if ($VerbosePreference -ne 'SilentlyContinue') { $params['Verbose'] = $true }
& $inner @params
exit $LASTEXITCODE