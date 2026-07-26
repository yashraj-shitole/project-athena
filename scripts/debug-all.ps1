#Requires -Version 7.0
<#
.SYNOPSIS
    Start the entire stack in debug mode (backend + frontend + debugger).

.DESCRIPTION
    Full-stack debug: postgres, redis, ollama, the API under uvicorn --reload
    + debugpy (:5678), and the Vite web-dev HMR server (:5173). Source is
    bind-mounted on both ends; sourcemaps on.

    Delegates to goThrough/debug.ps1 (the canonical launcher) and adds log
    following when -Watch.

.PARAMETER Watch
    Follow logs in the foreground after a healthy start (Ctrl-C keeps the
    stack running). Default: true.

.PARAMETER Timeout
    Health-check timeout in seconds.

.EXAMPLE
    .\scripts\debug-all.ps1
    .\scripts\debug-all.ps1 -Watch:$false
#>
[CmdletBinding()]
param(
    [switch]$Watch = $true,
    [int]$Timeout = 120
)
$inner = Join-Path $PSScriptRoot 'goThrough\debug.ps1'
$params = @{ Timeout = $Timeout }
if ($Watch) { $params['Watch'] = $true }
if ($VerbosePreference -ne 'SilentlyContinue') { $params['Verbose'] = $true }
& $inner @params
exit $LASTEXITCODE