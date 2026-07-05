#Requires -Version 7.0
<#
.SYNOPSIS
    Start the frontend with hot reload (Vite HMR), backed by a live API.

.DESCRIPTION
    Brings up the dependencies + API (so the frontend has a backend to proxy
    to) and the web-dev Vite server with HMR on http://localhost:5173. Host
    frontend/ source is bind-mounted; Vite sourcemaps are on in dev.

    The API is started in debug mode too (so edits to either end hot-reload).
    If you only want the frontend and are running the backend locally outside
    Docker, set VITE_API_TARGET accordingly and start just web-dev.

.PARAMETER Watch
    Follow web-dev + api logs after start (Ctrl-C keeps the stack running).

.PARAMETER Timeout
    Health-check timeout in seconds.

.EXAMPLE
    .\debug-frontend.ps1
    .\debug-frontend.ps1 -Watch
#>
[CmdletBinding()]
param(
    [switch]$Watch,
    [int]$Timeout = 120
)
. (Join-Path $PSScriptRoot 'goThrough\_helpers.ps1')
Enter-ProjectRoot

$svcList = @('postgres', 'redis', 'ollama', 'ollama-pull', 'api', 'web-dev')
try {
    if (-not (Test-Prereqs)) { exit 1 }
    if (-not (Test-RequiredEnv)) { exit 1 }
    Ensure-NetworksAndVolumes

    $needBuild = $false
    foreach ($svc in @('api', 'web-dev')) {
        if (-not (& docker images --filter "reference=athena-$svc" -q 2>$null)) { $needBuild = $true; break }
    }
    if ($needBuild) {
        Write-Step 'Building images'
        if (-not (Invoke-Compose -CommandArgs @('build') -Services $svcList -Debug)) { exit 1 }
    }

    Write-Banner 'Project Athena  -  DEBUG (frontend HMR)'
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
    Write-Host 'Frontend HMR:' -ForegroundColor White
    Write-Host '  Open http://localhost:5173  (Vite, sourcemaps on, edit frontend/src and save)' -ForegroundColor Gray
    Write-Host '  Backend proxied at /api -> http://localhost:8000' -ForegroundColor Gray
    Write-Host '  Tail logs:  .\goThrough\logs.ps1 -Services web-dev,api' -ForegroundColor Gray
    Write-Host ''
    Write-Ok 'Frontend debug session ready.'

    if ($Watch) {
        Write-Step 'Following logs (Ctrl-C to detach  -  containers keep running)'
        $null = Invoke-Compose -CommandArgs @('logs', '-f', '--tail', '100') -Services $healthSvcs -Debug
    }
    exit 0
} catch {
    Write-Err "Debug-frontend failed: $($_.Exception.Message)"
    exit 1
}