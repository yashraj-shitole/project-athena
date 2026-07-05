#Requires -Version 7.0
<#
.SYNOPSIS
    Start the entire stack in debug mode (hot reload + debugpy + HMR).

.DESCRIPTION
    Canonical debug launcher. Brings up postgres, redis, ollama, the API
    under uvicorn --reload + debugpy (attach on 127.0.0.1:5678), and the Vite
    web-dev server (HMR on :5173). Source is bind-mounted so host edits hot
    reload without a rebuild.

    The script starts detached and prints attach instructions; it does NOT
    block on logs. Run logs.ps1 separately to tail, or use the root
    debug-all.ps1 (which adds -Watch).

.PARAMETER Watch
    After a healthy start, follow logs in the foreground (Ctrl-C keeps the
    stack running).

.PARAMETER Timeout
    Health-check timeout in seconds.

.EXAMPLE
    .\goThrough\debug.ps1
    .\goThrough\debug.ps1 -Watch
#>
[CmdletBinding()]
param(
    [switch]$Watch,
    [int]$Timeout = 120
)
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

$svcList = @('postgres', 'redis', 'ollama', 'ollama-pull', 'api', 'web-dev')

function Show-AttachInfo {
    Write-Host ''
    Write-Host 'Attach a Python debugger to the backend:' -ForegroundColor White
    Write-Host '  debugpy is listening on 127.0.0.1:5678 (attach-anytime; no wait).' -ForegroundColor Gray
    Write-Host '  VS Code launch.json snippet:' -ForegroundColor Gray
    Write-Host @'
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
    Write-Host '  Frontend HMR: http://localhost:5173  (Vite, sourcemaps on)' -ForegroundColor Gray
    Write-Host '  Tail logs:    .\goThrough\logs.ps1 -Services api,web-dev' -ForegroundColor Gray
    Write-Host ''
}

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if (-not (Test-RequiredEnv)) { exit 1 }
    Ensure-NetworksAndVolumes

    # Auto-build if images missing.
    $needBuild = $false
    foreach ($svc in @('api', 'web-dev')) {
        if (-not (& docker images --filter "reference=athena-$svc" -q 2>$null)) { $needBuild = $true; break }
    }
    if ($needBuild) {
        Write-Step 'Building debug images'
        if (-not (Invoke-Compose -CommandArgs @('build') -Services $svcList -Debug)) { exit 1 }
    }

    Write-Banner 'Project Athena  -  DEBUG'
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
    Show-AttachInfo
    Write-Ok 'Debug stack is up.'

    if ($Watch) {
        Write-Step 'Following logs (Ctrl-C to detach  -  containers keep running)'
        $null = Invoke-Compose -CommandArgs @('logs', '-f', '--tail', '100') -Services $healthSvcs -Debug
    }
    exit 0
} catch {
    Write-Err "Debug start failed: $($_.Exception.Message)"
    exit 1
}