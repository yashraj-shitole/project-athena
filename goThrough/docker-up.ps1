#Requires -Version 7.0
<#
.SYNOPSIS
    Start the Athena stack: build if needed, up, wait for health, show URLs.

.DESCRIPTION
    Canonical startup script. Used directly from goThrough/ and also by the
    root ./docker-up.ps1 wrapper.

    Modes:
      Normal   : postgres redis ollama ollama-pull api nginx   -> http://localhost:8080
      Debug    : + hot reload (uvicorn --reload) + debugpy :5678 + web-dev HMR
      Watch    : Debug + follow logs in foreground (Ctrl-C keeps the stack up)

    Behaviour:
      - validates prerequisites + env
      - builds images when -Build (or when an image is missing)
      - starts containers detached
      - waits for health checks
      - prints service URLs + container status
      - on startup failure, prints the failing service logs and exits 1
      - in -Watch, tails logs after a healthy start

    Data (pgdata, ollama_data, api_storage, uploads) is preserved across all
    modes  -  nothing here uses `down -v`.

.PARAMETER Debug
    Use the debug compose override (hot reload + debugger ports + verbose).

.PARAMETER Watch
    Implies -Debug. After a healthy start, follow logs in the foreground.
    Ctrl-C stops only the log view; containers keep running.

.PARAMETER Services
    Comma-separated list of services to start (overrides the default set).

.PARAMETER Build
    Rebuild images before starting.

.PARAMETER NoCache
    With -Build, ignore the layer cache.

.PARAMETER Detached
    Run detached (default). Clear with -Detached:$false to run in foreground.

.PARAMETER Timeout
    Health-check timeout in seconds (default 120).

.EXAMPLE
    .\goThrough\docker-up.ps1
    .\goThrough\docker-up.ps1 -Debug
    .\goThrough\docker-up.ps1 -Debug -Watch
    .\goThrough\docker-up.ps1 -Services backend,frontend -Build
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
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

if ($Watch) { $Debug = $true }

function Get-DefaultServices {
    param([switch]$Debug)
    if ($Debug) { return @('postgres', 'redis', 'ollama', 'ollama-pull', 'api', 'web-dev') }
    return @('postgres', 'redis', 'ollama', 'ollama-pull', 'api', 'nginx')
}

function Get-HealthServices {
    # one-shot ollama-pull is excluded; it has no long-running health.
    param([string[]]$Started)
    return @($Started | Where-Object { $_ -ne 'ollama-pull' })
}

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if (-not (Test-RequiredEnv)) { exit 1 }
    Ensure-NetworksAndVolumes

    if ($Services) {
        $svcList = $Services -split ',' | ForEach-Object { (Resolve-Service -Name $_.Trim()) }
    } else {
        $svcList = Get-DefaultServices -Debug:$Debug
    }
    $healthSvcs = Get-HealthServices -Started $svcList

    if ($Build) {
        Write-Step 'Building images before start'
        $buildArgs = @('build')
        if ($NoCache) { $buildArgs += '--no-cache' }
        if ($VerbosePreference -ne 'SilentlyContinue') { $buildArgs += '--progress=plain' }
        if (-not (Invoke-Compose -CommandArgs $buildArgs -Services $svcList -Debug:$Debug)) {
            Write-Err 'Build failed; aborting startup.'
            exit 1
        }
    } else {
        # Auto-build if any required image is missing (so a fresh clone "just
        # works" even if the user forgot to run build.ps1 first).
        $needBuild = $false
        foreach ($svc in $svcList) {
            if ($svc -in @('api', 'nginx', 'web-dev')) {
                $img = & docker images --filter "reference=athena-$svc" -q 2>$null
                if (-not $img) { $needBuild = $true; break }
            }
        }
        if ($needBuild) {
            Write-Step 'One or more images are missing  -  building (use -Build to force, build.ps1 to control)'
            if (-not (Invoke-Compose -CommandArgs @('build') -Services $svcList -Debug:$Debug)) {
                Write-Err 'Build failed; aborting startup.'; exit 1
            }
        }
    }

    Write-Banner "Project Athena  -  up$($(if ($Debug) { ' (debug)' } else { '' }))$($(if ($Watch) { ' (watch)' } else { '' }))"
    Write-Step "Starting: $($svcList -join ', ')"
    $upArgs = @('up')
    if ($Build) { $upArgs += '--build' }
    if ($Detached) { $upArgs += '-d' }
    if (-not (Invoke-Compose -CommandArgs $upArgs -Services $svcList -Debug:$Debug)) {
        Write-Err 'docker compose up failed. Dumping recent logs:'
        $null = Invoke-Compose -CommandArgs @('logs', '--tail', '80') -Services $svcList -Debug:$Debug
        exit 1
    }

    Write-Step "Waiting for health (timeout ${Timeout}s)"
    if (-not (Wait-Healthy -Services $healthSvcs -TimeoutSec $Timeout)) {
        Write-Err 'One or more services failed to become healthy. Dumping logs:'
        $null = Invoke-Compose -CommandArgs @('logs', '--tail', '120') -Services $healthSvcs -Debug:$Debug
        exit 1
    }

    Show-ServiceUrls -Services $svcList
    Show-ComposeStatus -Debug:$Debug
    Write-Ok 'Stack is up.'

    if ($Watch) {
        Write-Step 'Watch mode: following logs (Ctrl-C to detach  -  containers keep running)'
        Write-Info 'Backend edits reload via uvicorn --reload; frontend via Vite HMR.'
        try {
            $null = Invoke-Compose -CommandArgs @('logs', '-f', '--tail', '100') -Services $healthSvcs -Debug:$Debug
        } catch {
            Write-Info 'Log stream interrupted.'
        }
        Write-Info 'Containers are still running. Stop with: .\goThrough\docker-down.ps1'
    }
    exit 0
} catch {
    Write-Err "Startup failed: $($_.Exception.Message)"
    exit 1
}