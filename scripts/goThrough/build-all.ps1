#Requires -Version 7.0
<#
.SYNOPSIS
    Build every Docker image in the stack in dependency order.

.DESCRIPTION
    Builds the api (backend) and nginx (frontend) images from the repo
    Dockerfile, and pre-pulls the third-party infra images (postgres, redis,
    ollama). Optionally also builds web-dev (the Vite HMR image).

    Dependency order:
      1. api    (Dockerfile target `api`)    -  backend runtime
      2. nginx  (Dockerfile target `nginx`)   -  needs the `web` SPA stage
      3. web-dev (optional, with -IncludeDev)  -  Vite HMR

    Idempotent: re-running only rebuilds layers whose inputs changed (unless
    -NoCache). Use -Clean to delete the existing tagged images first.

.PARAMETER Clean
    Remove the existing athena-* images before building.

.PARAMETER NoCache
    Pass --no-cache to docker build (full rebuild, no layer cache).

.PARAMETER IncludeDev
    Also build the web-dev (Vite) image.

.PARAMETER Production
    Enforce production-grade env validation (rejects placeholder secrets).

.EXAMPLE
    .\scripts\goThrough\build-all.ps1
    .\scripts\goThrough\build-all.ps1 -NoCache -Verbose
    .\scripts\goThrough\build-all.ps1 -Clean -IncludeDev
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$NoCache,
    [switch]$IncludeDev,
    [switch]$Production
)
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

function Invoke-BuildSet {
    param([string[]]$Services)
    $buildArgs = @('build')
    if ($NoCache) { $buildArgs += '--no-cache' }
    if ($VerbosePreference -ne 'SilentlyContinue') { $buildArgs += '--progress=plain' }
    foreach ($svc in $Services) { Write-Step "Building image: $svc" }
    if (-not (Invoke-Compose -CommandArgs $buildArgs -Services $Services)) { return $false }
    return $true
}

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if (-not (Test-RequiredEnv -Strict:$Production)) { exit 1 }
    Ensure-NetworksAndVolumes

    # Pre-pull third-party images so a later `up` is fast and offline-friendly.
    Write-Step 'Pulling infra images (postgres, redis, ollama, ollama-pull)'
    $null = Invoke-Compose -CommandArgs @('pull', '--ignore-pull-failures') -Services @('postgres', 'redis', 'ollama', 'ollama-pull')

    if ($Clean) {
        Write-Step 'Clean: removing existing athena-* images'
        foreach ($img in @('athena-api', 'athena-nginx', 'athena-web-dev')) {
            & docker image rm -f $img 2>$null | Out-Null
        }
    }

    Write-Banner 'Building Project Athena images'
    if (-not (Invoke-BuildSet -Services @('api'))) { exit 1 }
    if (-not (Invoke-BuildSet -Services @('nginx'))) { exit 1 }
    if ($IncludeDev) {
        if (-not (Invoke-BuildSet -Services @('web-dev'))) { exit 1 }
    }

    Write-Ok 'All images built.'
    Write-Info 'Next: ./docker-up.ps1            (run the stack)'
    Write-Info '     ./docker-up.ps1 -Debug      (hot reload + debugger)'
    exit 0
} catch {
    Write-Err "Build failed: $($_.Exception.Message)"
    exit 1
}