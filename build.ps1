#Requires -Version 7.0
<#
.SYNOPSIS
    Root build script: builds all Athena Docker images in dependency order.

.DESCRIPTION
    One-command build for the whole stack. Validates prerequisites and
    environment, ensures Docker networks/volumes exist, then builds the
    images in dependency order (api -> nginx [, web-dev]).

    Delegates the per-service work to goThrough/build-*.ps1.

.PARAMETER Clean
    Remove existing athena-* images before building.

.PARAMETER NoCache
    Pass --no-cache to docker build (ignore the layer cache).

.PARAMETER Service
    Build only one service. Accepts friendly aliases:
      backend | api          -> api
      frontend | web | nginx -> nginx
      web-dev | vite | dev   -> web-dev
      ai-service | worker    -> api (folded)
      db | postgres | redis | llm | ollama -> pulls the third-party image

.PARAMETER IncludeDev
    Also build the web-dev (Vite) image (only meaningful without -Service, or
    with -Service frontend).

.PARAMETER Production
    Enforce production-grade env validation (reject placeholder secrets).

.PARAMETER Verbose
    Stream build output with --progress=plain.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Clean -NoCache -Verbose
    .\build.ps1 -Service backend
    .\build.ps1 -Service frontend -IncludeDev
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$NoCache,
    [string]$Service,
    [switch]$IncludeDev,
    [switch]$Production
)
. (Join-Path $PSScriptRoot 'goThrough\_helpers.ps1')
Enter-ProjectRoot

function Invoke-BuildImages {
    param([string[]]$Services)
    $buildArgs = @('build')
    if ($NoCache) { $buildArgs += '--no-cache' }
    if ($VerbosePreference -ne 'SilentlyContinue') { $buildArgs += '--progress=plain' }
    foreach ($svc in $Services) { Write-Step "Building image: $svc" }
    return (Invoke-Compose -CommandArgs $buildArgs -Services $Services)
}

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if (-not (Test-RequiredEnv -Strict:$Production)) { exit 1 }
    Ensure-NetworksAndVolumes

    # Pre-pull third-party infra images (best-effort; keeps a later `up` fast).
    Write-Step 'Pulling infra images (postgres, redis, ollama, ollama-pull)'
    $null = Invoke-Compose -CommandArgs @('pull', '--ignore-pull-failures') -Services @('postgres', 'redis', 'ollama', 'ollama-pull')

    if ($Clean) {
        Write-Step 'Clean: removing existing athena-* images'
        foreach ($img in @('athena-api', 'athena-nginx', 'athena-web-dev')) {
            & docker image rm -f $img 2>$null | Out-Null
        }
    }

    Write-Banner 'Project Athena  -  build'

    if ($Service) {
        $svc = Resolve-Service -Name $Service
        Write-Info "Target service: $svc"
        switch ($svc) {
            'api'      { if (-not (Invoke-BuildImages -Services @('api')))     { exit 1 } }
            'nginx'    { if (-not (Invoke-BuildImages -Services @('nginx')))   { exit 1 }
                         if ($IncludeDev -and -not (Invoke-BuildImages -Services @('web-dev'))) { exit 1 } }
            'web-dev'  { if (-not (Invoke-BuildImages -Services @('web-dev'))) { exit 1 } }
            { $_ -in @('postgres', 'redis', 'ollama', 'ollama-pull') } {
                Write-Info "$svc uses a prebuilt image; pulling."
                if (-not (Invoke-Compose -CommandArgs @('pull') -Services @($svc))) { exit 1 }
            }
            default { Write-Err "Unknown service '$Service' (resolved '$svc')."; exit 1 }
        }
        Write-Ok "Build complete: $svc"
        exit 0
    }

    # Default: build everything in dependency order.
    if (-not (Invoke-BuildImages -Services @('api')))   { exit 1 }
    if (-not (Invoke-BuildImages -Services @('nginx'))) { exit 1 }
    if ($IncludeDev -and -not (Invoke-BuildImages -Services @('web-dev'))) { exit 1 }

    Write-Ok 'All images built.'
    Write-Info 'Next: .\docker-up.ps1            (run the stack)'
    Write-Info '     .\docker-up.ps1 -Debug      (hot reload + debugger)'
    exit 0
} catch {
    Write-Err "Build failed: $($_.Exception.Message)"
    exit 1
}