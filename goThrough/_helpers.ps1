#Requires -Version 7.0
<#
.SYNOPSIS
    Shared helper functions for Project Athena developer scripts.

.DESCRIPTION
    Every script in goThrough/ and at the repo root dot-sources this file.
    It is the single source of truth for:
      - colored console output
      - the docker compose invocation pattern (project name, file order,
        debug profile)
      - service <-> container name mapping
      - prerequisite / environment validation
      - network + volume bootstrapping
      - health-check polling

    Conventions enforced across the whole suite:
      * scripts always run from the repo root
      * COMPOSE_PROJECT_NAME is forced to "athena" so volumes/networks are
        predictable (athena_pgdata, athena_default, ...) regardless of CWD
      * compose is always invoked as:
            docker compose [--profile dev] -f infra/docker-compose.yml
                           [-f infra/docker-compose.debug.yml] <command> ...
      * helpers never call `docker compose` with anything that breaks the
        above; scripts should NEVER shell out to docker compose directly -
        always go through Invoke-Compose

    Service map (matches infra/docker-compose.yml):
      backend  -> service `api`    (container athena-api)
      frontend -> service `nginx`  (container athena-nginx, prod SPA)
                 or service `web-dev` (container athena-web-dev, HMR)
      ai-service / worker -> folded into `api`; their build-* aliases build
        the `api` target (see goThrough/build-ai-service.ps1 / build-worker.ps1)
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Project constants
# ---------------------------------------------------------------------------
$script:PROJECT_NAME = 'athena'
$script:REPO_ROOT    = Split-Path -Parent $PSScriptRoot
$script:COMPOSE_DIR  = Join-Path $script:REPO_ROOT 'infra'
$script:COMPOSE_FILE = Join-Path $script:COMPOSE_DIR 'docker-compose.yml'
$script:DEBUG_FILE   = Join-Path $script:COMPOSE_DIR 'docker-compose.debug.yml'

# service -> container_name (from the compose file's container_name: keys)
$script:ContainerFor = @{
    postgres     = 'athena-postgres'
    redis        = 'athena-redis'
    ollama       = 'athena-ollama'
    'ollama-pull' = 'athena-ollama-pull'
    api          = 'athena-api'
    nginx        = 'athena-nginx'
    'web-dev'    = 'athena-web-dev'
}

# Friendly service -> URL printed by docker-up / status
$script:UrlFor = @{
    api       = 'http://localhost:8000  (direct API; loopback only)'
    nginx     = 'http://localhost:8080  (SPA + reverse proxy)'
    'web-dev' = 'http://localhost:5173  (Vite HMR)'
    postgres  = 'localhost:5432          (loopback only)'
    redis     = 'localhost:6379          (loopback only)'
    ollama    = 'http://ollama:11434     (in-network only; not published)'
}

# All services that are built from the repo Dockerfile (have a `build:` block)
$script:BuildServices = @('api', 'nginx')
# All services, used for status/health sweeps
$script:AllServices = @('postgres', 'redis', 'ollama', 'api', 'nginx')

# ---------------------------------------------------------------------------
# Console output (colored). All output goes through these so the suite has a
# consistent look. Errors go to the real stderr stream.
# ---------------------------------------------------------------------------
function Write-Step  { param([Parameter(Mandatory)][string]$Message) Write-Host "=> $Message" -ForegroundColor Cyan }
function Write-Ok    { param([Parameter(Mandatory)][string]$Message) Write-Host "[OK]   $Message" -ForegroundColor Green }
function Write-Warn  { param([Parameter(Mandatory)][string]$Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Err   { param([Parameter(Mandatory)][string]$Message) [Console]::Error.WriteLine("[ERR]  $Message") }
function Write-Info  { param([Parameter(Mandatory)][string]$Message) Write-Host "       $Message" -ForegroundColor DarkGray }
function Write-Dim   { param([Parameter(Mandatory)][string]$Message) Write-Host "       $Message" -ForegroundColor DarkGray }
function Write-Banner { param([Parameter(Mandatory)][string]$Message) Write-Host ("=" * 72) -ForegroundColor DarkGray; Write-Host $Message -ForegroundColor White; Write-Host ("=" * 72) -ForegroundColor DarkGray }

# Verbose echo of a command, only when -Verbose is active.
function Write-Command { param([Parameter(Mandatory)][string[]]$Command) if ($VerbosePreference -ne 'SilentlyContinue') { Write-Host ("+ " + ($Command -join ' ')) -ForegroundColor DarkGray } }

# ---------------------------------------------------------------------------
# CWD: force the script session onto the repo root so paths in compose and
# mounts resolve no matter where the user invoked us from.
# ---------------------------------------------------------------------------
function Enter-ProjectRoot {
    [CmdletBinding()]
    param()
    Set-Location -LiteralPath $script:REPO_ROOT
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
function Test-Prereqs {
    <#
        Verifies docker + the compose plugin are on PATH. Returns $true /
        $false; prints helpful errors. Call at the top of every script.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()
    $ok = $true
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Err "Docker is not installed or not on PATH. Install Docker Desktop / Engine first."
        $ok = $false
    } else {
        # Confirm the compose subcommand works (some installs ship docker but
        # no compose plugin).
        try {
            $null = & docker compose version 2>$null
            if ($LASTEXITCODE -ne 0) { throw "compose missing" }
        } catch {
            Write-Err "The 'docker compose' plugin is not available. Install the Compose v2 plugin."
            $ok = $false
        }
    }
    if (-not $ok) { return $false }
    # Daemon reachable?
    try {
        $null = & docker info 2>$null
        if ($LASTEXITCODE -ne 0) { throw "daemon down" }
    } catch {
        Write-Err "The Docker daemon is not running. Start Docker Desktop / the daemon and retry."
        return $false
    }
    return $true
}

# ---------------------------------------------------------------------------
# Environment variable validation
# ---------------------------------------------------------------------------
function Get-RequiredEnv {
    <#
        Returns the list of env vars the stack needs, with the shipped dev
        defaults (so callers can detect "unset vs set-to-default").
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()
    return [ordered]@{
        ATHENA_POSTGRES_USER     = 'athena'
        ATHENA_POSTGRES_PASSWORD = 'athena-dev-only'
        ATHENA_POSTGRES_DB        = 'athena'
        ATHENA_JWT_SECRET        = 'change-me-in-prod'
        ATHENA_OLLAMA_MODEL      = 'qwen2.5:1.5b-instruct'
        ATHENA_CORS_ORIGINS      = '["http://localhost:8080"]'
        ATHENA_ENV               = 'dev'
        ATHENA_LOG_LEVEL         = 'INFO'
    }
}

function Test-RequiredEnv {
    <#
        Validates environment variables.

        - In dev/test/local (the default) every var has a working default, so
          we only WARN about insecure placeholders and return $true.
        - In any other environment (staging/prod) we fail fast: JWT_SECRET
          must be set and not a known placeholder; CORS_ORIGINS must not
          contain localhost. Returns $false and the caller should exit 1.

        Set -Strict to treat dev as if it were prod (used by reset/build with
        an explicit -Production switch).
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([switch]$Strict)
    $vars = Get-RequiredEnv
    $warnings = [System.Collections.Generic.List[string]]::new()
    foreach ($k in $vars.Keys) {
        if (-not [Environment]::GetEnvironmentVariable($k, 'Process')) {
            $warnings.Add("$k is not set (using shipped dev default '$($vars[$k])')")
        }
    }

    $env = [Environment]::GetEnvironmentVariable('ATHENA_ENV', 'Process')
    if (-not $env) { $env = 'dev' }
    $isDev = $env.ToLower() -in @('dev', 'development', 'test', 'local')

    $jwt = [Environment]::GetEnvironmentVariable('ATHENA_JWT_SECRET', 'Process')
    $knownInsecure = @('change-me-in-prod', '', 'secret', 'changeme')

    if (-not $isDev -or $Strict) {
        # Production-grade enforcement.
        if (-not $jwt -or $jwt -in $knownInsecure) {
            Write-Err "ATHENA_JWT_SECRET is unset or a known placeholder. Refusing to build/start outside dev."
            Write-Err "Set a strong unique secret, e.g.: `$env:ATHENA_JWT_SECRET = (New-Guid).ToString() + (New-Guid).ToString()"
            return $false
        }
        $cors = [Environment]::GetEnvironmentVariable('ATHENA_CORS_ORIGINS', 'Process')
        if ($env.ToLower() -eq 'prod' -and $cors -and $cors -match 'http://localhost') {
            Write-Err "ATHENA_CORS_ORIGINS contains a localhost origin in prod. Set explicit production origins."
            return $false
        }
    }

    if ($jwt -and $jwt -in $knownInsecure) {
        $warnings.Add("ATHENA_JWT_SECRET is the shipped placeholder '$jwt'  -  fine for dev, never for prod.")
    }
    foreach ($w in $warnings) { Write-Warn $w }
    return $true
}

# ---------------------------------------------------------------------------
# Compose invocation
# ---------------------------------------------------------------------------
function Get-ComposeArgs {
    <#
        Builds the docker compose argument prefix: [--profile dev] -f base
        [-f debug]. Returns a string[] that can be splatted before the
        command word and its own args.
    #>
    # NOTE: not [CmdletBinding()] - `Debug` is a reserved common parameter under
    # CmdletBinding, which would make `-Debug` collide ("defined multiple times").
    # This is a plain function so [switch]$Debug is a normal parameter.
    [OutputType([string[]])]
    param([switch]$Debug)
    $args = @()
    if ($Debug) { $args += @('--profile', 'dev') }
    $args += @('-f', $script:COMPOSE_FILE)
    if ($Debug -and (Test-Path $script:DEBUG_FILE)) {
        $args += @('-f', $script:DEBUG_FILE)
    } elseif ($Debug -and -not (Test-Path $script:DEBUG_FILE)) {
        Write-Warn "Debug compose file not found at $($script:DEBUG_FILE); continuing with base file only."
    }
    return [string[]]$args
}

function Invoke-Compose {
    <#
        Runs `docker compose` with the project name and file order enforced.
        Streams output to the console. Returns $true on exit code 0.

        Parameters:
          CommandArgs : the compose subcommand + its flags, e.g. @('up','-d')
          Services    : optional service list appended at the end
          Debug       : use the debug override + dev profile
    #>
    # NOTE: not [CmdletBinding()] and no [Parameter()] attribute - either would
    # make this an advanced function and reserve `Debug` as a common parameter,
    # colliding with our [switch]$Debug ("defined multiple times"). Mandatory is
    # enforced manually below.
    [OutputType([bool])]
    param(
        [string[]]$CommandArgs,
        [string[]]$Services,
        [switch]$Debug
    )
    if (-not $CommandArgs) { Write-Err 'Invoke-Compose: -CommandArgs is required.'; return $false }
    $prefix = Get-ComposeArgs -Debug:$Debug
    $cmd = @('compose') + $prefix + $CommandArgs
    if ($Services -and $Services.Count) { $cmd += [string[]]$Services }
    Write-Command $cmd

    $prevProject = $env:COMPOSE_PROJECT_NAME
    $env:COMPOSE_PROJECT_NAME = $script:PROJECT_NAME
    try {
        # Stream docker output straight to the host display (Out-Host) so it
        # does NOT enter this function's output pipeline. Without this, docker
        # stdout would be emitted BEFORE `return $true/$false`, contaminating
        # the bool every caller relies on (test.ps1 false-pass, watch-mode
        # log suppression, shell/migrate false-success). Native stderr is left
        # to the console (not merged with 2>&1, which trips Stop under PS 7).
        & docker @cmd | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $env:COMPOSE_PROJECT_NAME = $prevProject
    }
    if ($null -eq $code) { $code = 0 }
    if ($code -ne 0) {
        Write-Err "docker compose failed (exit $code): $($cmd -join ' ')"
        return $false
    }
    return $true
}

# ---------------------------------------------------------------------------
# Scripts call Invoke-Compose directly with an explicit CommandArgs array
# (e.g. @('up','-d','--build')). Keeping the call sites explicit is more
# readable than opaque verb-wrappers and avoids strict-mode traps.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Network + volume bootstrapping (best-effort; idempotent)
# ---------------------------------------------------------------------------
function Ensure-NetworksAndVolumes {
    <#
        docker compose creates these on first `up`, but build-only flows
        (build.ps1) never start containers, so we materialize the named
        volumes and the default network explicitly. Errors are swallowed
        (volume/network already exists is not a failure).
    #>
    [CmdletBinding()]
    param()
    $network = "$($script:PROJECT_NAME)_default"
    Write-Step "Ensuring Docker network '$network' and named volumes exist"
    try { & docker network inspect $network 2>$null | Out-Null; if ($LASTEXITCODE -ne 0) { & docker network create $network 2>$null | Out-Null } } catch {}
    foreach ($v in @('pgdata', 'ollama_data', 'api_storage', 'webdev_node_modules')) {
        $name = "$($script:PROJECT_NAME)_$v"
        try {
            & docker volume inspect $name 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $null = & docker volume create $name 2>$null
                Write-Info "created volume $name"
            }
        } catch { }
    }
}

# ---------------------------------------------------------------------------
# Container health polling
# ---------------------------------------------------------------------------
function Get-ContainerState {
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory)][string]$Container)
    & docker inspect --format '{{.State.Status}}' $Container 2>$null
}

function Get-ContainerHealth {
    <#
        Returns 'healthy' | 'unhealthy' | 'starting' | 'no-healthcheck' |
        'missing'. Services without a HEALTHCHECK (nginx, web-dev) are
        considered ready once State.Status == 'running'.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory)][string]$Container)
    $exists = & docker inspect --format '{{.Id}}' $Container 2>$null
    if (-not $exists) { return 'missing' }
    $hasHc = & docker inspect --format '{{if .Config.Healthcheck}}yes{{else}}no{{end}}' $Container 2>$null
    if ($hasHc -eq 'yes') {
        $h = & docker inspect --format '{{.State.Health.Status}}' $Container 2>$null
        if ([string]::IsNullOrWhiteSpace($h)) { return 'starting' }
        return $h
    }
    $st = Get-ContainerState -Container $Container
    if ($st -eq 'running') { return 'healthy' }
    return 'starting'
}

function Wait-Healthy {
    <#
        Polls the given services' containers until each is healthy/running or
        the timeout elapses. Returns $true if all became healthy.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][string[]]$Services,
        [int]$TimeoutSec = 120,
        [int]$IntervalSec = 3
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $pending = [System.Collections.Generic.List[string]]::new($Services)
    $first = $true
    while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline) {
        foreach ($svc in @($pending)) {
            $container = $script:ContainerFor[$svc]
            if (-not $container) {
                Write-Warn "Unknown service '$svc'; skipping health check."
                $pending.Remove($svc) | Out-Null
                continue
            }
            $h = Get-ContainerHealth -Container $container
            if ($h -eq 'healthy') {
                Write-Ok "$svc ($container) is healthy"
                $pending.Remove($svc) | Out-Null
            } elseif ($h -eq 'missing') {
                Write-Warn "$svc ($container) container not found  -  did it start?"
                $pending.Remove($svc) | Out-Null
            } elseif ($first) {
                Write-Info "waiting for $svc ($container): $h"
            }
        }
        $first = $false
        if ($pending.Count -gt 0) { Start-Sleep -Seconds $IntervalSec }
    }
    if ($pending.Count -gt 0) {
        foreach ($svc in $pending) { Write-Err "$svc did not become healthy within ${TimeoutSec}s" }
        return $false
    }
    return $true
}

# ---------------------------------------------------------------------------
# Status / URL display
# ---------------------------------------------------------------------------
function Show-ServiceUrls {
    [CmdletBinding()]
    param([string[]]$Services = @('api', 'nginx', 'web-dev', 'postgres', 'redis', 'ollama'))
    Write-Host ""
    Write-Host "Service URLs:" -ForegroundColor White
    foreach ($svc in $Services) {
        if ($script:UrlFor.ContainsKey($svc)) {
            $line = $script:UrlFor[$svc]
            Write-Host ("  {0,-10} {1}" -f $svc, $line) -ForegroundColor Gray
        }
    }
    Write-Host ""
}

function Show-ComposeStatus {
    <#
        Prints `docker compose ps` in a readable table. Uses --format to get
        Name/Status/Ports so we are not at the mercy of terminal width.
    #>
    # NOTE: not [CmdletBinding()] - `Debug` is a reserved common parameter under
    # CmdletBinding; plain function so [switch]$Debug binds normally.
    param([switch]$Debug)
    Write-Host ""
    Write-Host "Container status:" -ForegroundColor White
    $prefix = Get-ComposeArgs -Debug:$Debug
    $cmd = @('compose') + $prefix + @('ps', '--format', 'table {{.Name}}\t{{.Service}}\t{{.Status}}\t{{.Ports}}')
    $prevProject = $env:COMPOSE_PROJECT_NAME
    $env:COMPOSE_PROJECT_NAME = $script:PROJECT_NAME
    try { & docker @cmd } finally { $env:COMPOSE_PROJECT_NAME = $prevProject }
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Confirm prompt for destructive actions
# ---------------------------------------------------------------------------
function Confirm-Action {
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory)][string]$Message, [switch]$DefaultNo)
    $choices = '&Yes', '&No'
    $def = if ($DefaultNo) { 1 } else { 0 }
    $answer = $host.UI.PromptForChoice('Project Athena', $Message, $choices, $def)
    return $answer -eq 0
}

# ---------------------------------------------------------------------------
# Normalize a -Service argument into a real compose service name, accepting
# the friendly aliases (backend/frontend/ai-service/worker).
# ---------------------------------------------------------------------------
function Resolve-Service {
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory)][string]$Name)
    switch -Regex ($Name.ToLower()) {
        '^(backend|api)$'         { return 'api' }
        '^(frontend|web|nginx)$'  { return 'nginx' }
        '^(web-dev|vite|dev)$'     { return 'web-dev' }
        '^(db|postgres|database)$' { return 'postgres' }
        '^(cache|redis)$'          { return 'redis' }
        '^(llm|ollama)$'           { return 'ollama' }
        '^(ai-service|ai|worker)$' { return 'api' }   # folded into api
        default { return $Name }
    }
}

# Export-ish: dot-sourcing makes these available in the caller's scope.
# (No `return $value` here - a returned value from a dot-sourced file is emitted
# to the caller's output stream, which would print a stray "True" at every run.)