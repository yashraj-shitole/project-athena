#Requires -Version 7.0
<#
.SYNOPSIS
    Start the backend (api) with a debugger attached  -  hot reload on.

.DESCRIPTION
    Brings up the backend's dependencies (postgres, redis, ollama) and the API
    in DEBUG mode: uvicorn --reload (watches backend/app) + debugpy listening
    on 127.0.0.1:5678 (attach-anytime). The host backend/ source is
    bind-mounted, so edits hot-reload without a rebuild.

    No frontend is started (use debug-frontend.ps1 / debug-all.ps1 for that).
    The stack starts detached so you can attach your IDE debugger; the script
    prints the VS Code attach snippet and how to tail logs. Implements its own
    scoped startup (backend + deps) rather than delegating to debug-all.ps1.

.PARAMETER Watch
    Follow backend logs after start (Ctrl-C keeps the stack running).

.PARAMETER Timeout
    Health-check timeout in seconds.

.EXAMPLE
    .\scripts\debug-backend.ps1
    .\scripts\debug-backend.ps1 -Watch
#>
[CmdletBinding()]
param(
    [switch]$Watch,
    [int]$Timeout = 120
)
. (Join-Path $PSScriptRoot 'goThrough\_helpers.ps1')
Enter-ProjectRoot

$svcList = @('postgres', 'redis', 'ollama', 'ollama-pull', 'api')
try {
    if (-not (Test-Prereqs)) { exit 1 }
    if (-not (Test-RequiredEnv)) { exit 1 }
    Ensure-NetworksAndVolumes

    $needBuild = -not (& docker images --filter "reference=athena-api" -q 2>$null)
    if ($needBuild) {
        Write-Step 'Building api image'
        if (-not (Invoke-Compose -CommandArgs @('build') -Services @('api') -Debug)) { exit 1 }
    }

    Write-Banner 'Project Athena  -  DEBUG (backend)'
    Write-Step "Starting: $($svcList -join ', ')"
    if (-not (Invoke-Compose -CommandArgs @('up', '-d') -Services $svcList -Debug)) {
        Write-Err 'docker compose up failed. Logs:'
        $null = Invoke-Compose -CommandArgs @('logs', '--tail', '80') -Services $svcList -Debug
        exit 1
    }
    $healthSvcs = @($svcList | Where-Object { $_ -ne 'ollama-pull' })
    Write-Step "Waiting for health (timeout ${Timeout}s)"
    if (-not (Wait-Healthy -Services $healthSvcs -TimeoutSec $Timeout)) {
        Write-Err 'Services failed to become healthy. Logs:'
        $null = Invoke-Compose -CommandArgs @('logs', '--tail', '120') -Services $healthSvcs -Debug
        exit 1
    }
    Show-ServiceUrls -Services $svcList
    Show-ComposeStatus -Debug

    Write-Host ''
    Write-Host 'Attach a Python debugger to the backend:' -ForegroundColor White
    Write-Host '  debugpy listening on 127.0.0.1:5678 (attach-anytime).' -ForegroundColor Gray
    Write-Host @'
  VS Code launch.json:
  {
    "name": "Attach to Athena API (docker)",
    "type": "debugpy",
    "request": "attach",
    "connect": { "host": "127.0.0.1", "port": 5678 },
    "pathMappings": [
      { "localRoot": "${workspaceFolder}/backend", "remoteRoot": "/app/backend" }
    ],
    "justMyCode": false
  }
'@ -ForegroundColor DarkGray
    Write-Host '  API:        http://localhost:8000  (loopback)' -ForegroundColor Gray
    Write-Host '  Tail logs:  .\scripts\goThrough\logs.ps1 -Services api' -ForegroundColor Gray
    Write-Host ''
    Write-Ok 'Backend debug session ready.'

    if ($Watch) {
        Write-Step 'Following backend logs (Ctrl-C to detach  -  containers keep running)'
        $null = Invoke-Compose -CommandArgs @('logs', '-f', '--tail', '100') -Services $healthSvcs -Debug
    }
    exit 0
} catch {
    Write-Err "Debug-backend failed: $($_.Exception.Message)"
    exit 1
}