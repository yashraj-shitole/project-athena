#Requires -Version 7.0
<#
.SYNOPSIS
    Root entry point for starting the Athena stack (wraps goThrough/docker-up.ps1).

.DESCRIPTION
    Thin wrapper around goThrough/docker-up.ps1 so the common commands work
    from the repo root. All flags are forwarded.

.EXAMPLE
    .\scripts\docker-up.ps1
    .\scripts\docker-up.ps1 -Debug
    .\scripts\docker-up.ps1 -Debug -Watch
    .\scripts\docker-up.ps1 -Services backend,frontend
    .\scripts\docker-up.ps1 -Build
#>
[CmdletBinding()]
param(
    [switch]$Watch,
    [string]$Services,
    [switch]$Build,
    [switch]$NoCache,
    [switch]$Detached = $true,
    [int]$Timeout = 120
)
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
$inner = Join-Path $PSScriptRoot 'goThrough\docker-up.ps1'
$params = @{}
if ($Debug)    { $params['Debug']    = $true }
if ($Watch)    { $params['Watch']    = $true }
if ($Services) { $params['Services'] = $Services }
if ($Build)    { $params['Build']    = $true }
if ($NoCache)  { $params['NoCache']  = $true }
if (-not $Detached) { $params['Detached'] = $false } else { $params['Detached'] = $true }
$params['Timeout'] = $Timeout
if ($VerbosePreference -ne 'SilentlyContinue') { $params['Verbose'] = $true }
& $inner @params
exit $LASTEXITCODE