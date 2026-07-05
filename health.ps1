#Requires -Version 7.0
<#
.SYNOPSIS
    Verify every service: container health, DB/Redis/Ollama, and API endpoints.

.DESCRIPTION
    Runs a layered health check:
      1. Container health (docker healthcheck status)
      2. Postgres connectivity (pg_isready)
      3. Redis connectivity (redis-cli ping)
      4. Ollama model availability (ollama list)
      5. API endpoints (/health, /model, /metrics) via the published port
      6. nginx SPA reachability (if nginx is running)

    Prints a summary table and exits 1 if any check FAILS.

.PARAMETER Debug
    Target the debug compose project.

.EXAMPLE
    .\health.ps1
    .\health.ps1 -Debug
#>
[CmdletBinding()]
param()
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
. (Join-Path $PSScriptRoot 'goThrough\_helpers.ps1')
Enter-ProjectRoot

$script:Checks = [System.Collections.Generic.List[object]]::new()
function Add-Check { param([string]$Name,[string]$Status,[string]$Detail)
    $script:Checks.Add([pscustomobject]@{ Check = $Name; Status = $Status; Detail = $Detail })
    switch ($Status) {
        'PASS' { Write-Ok "$Name : PASS - $Detail" }
        'FAIL' { Write-Err "$Name : FAIL - $Detail" }
        'SKIP' { Write-Info "$Name : SKIP - $Detail" }
    }
}

function Test-Exec {
    # Run a one-off command in a service; returns $true on exit 0.
    param([string]$Service, [string[]]$Cmd)
    $execArgs = @('exec', '-T', $Service) + $Cmd
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $prefix = Get-ComposeArgs -Debug:$Debug
        $prevProject = $env:COMPOSE_PROJECT_NAME
        $env:COMPOSE_PROJECT_NAME = $script:PROJECT_NAME
        try {
            $cmd = @('compose') + $prefix + $execArgs
            & docker @cmd 2>$null | Out-Null
            return ($LASTEXITCODE -eq 0)
        } finally { $env:COMPOSE_PROJECT_NAME = $prevProject }
    } finally { $ErrorActionPreference = $prev }
}

function Test-Http {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -SkipHttpErrorCheck -UseBasicParsing
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300), $r.StatusCode
    } catch { return $false, 0 }
}

try {
    if (-not (Test-Prereqs)) { exit 1 }
    Write-Banner 'Project Athena  -  health check'

    # 1. Container health
    foreach ($svc in @('postgres', 'redis', 'ollama', 'api')) {
        $container = $script:ContainerFor[$svc]
        $state = & docker inspect --format '{{.State.Status}}' $container 2>$null
        if (-not $state) { Add-Check "container:$svc" 'FAIL' "container $container not found"; continue }
        $h = Get-ContainerHealth -Container $container
        if ($h -eq 'healthy') { Add-Check "container:$svc" 'PASS' "$state / healthy" }
        elseif ($state -eq 'running' -and $h -eq 'starting') { Add-Check "container:$svc" 'WARN' "running, health starting" }
        else { Add-Check "container:$svc" 'FAIL' "state=$state health=$h" }
    }

    # 2. Postgres
    $user = [Environment]::GetEnvironmentVariable('ATHENA_POSTGRES_USER', 'Process'); if (-not $user) { $user = 'athena' }
    if (Test-Exec -Service 'postgres' -Cmd @('pg_isready', '-U', $user)) {
        Add-Check 'postgres:pg_isready' 'PASS' "accepting connections as $user"
    } else { Add-Check 'postgres:pg_isready' 'FAIL' 'not accepting connections' }

    # 3. Redis
    if (Test-Exec -Service 'redis' -Cmd @('redis-cli', 'ping')) {
        Add-Check 'redis:ping' 'PASS' 'PONG'
    } else { Add-Check 'redis:ping' 'FAIL' 'no PONG' }

    # 4. Ollama
    if (Test-Exec -Service 'ollama' -Cmd @('ollama', 'list')) {
        Add-Check 'ollama:list' 'PASS' 'models listed'
    } else { Add-Check 'ollama:list' 'FAIL' 'ollama list failed (model not pulled?)' }

    # 5. API endpoints (direct, loopback 8000)
    $apiState = & docker inspect --format '{{.State.Status}}' athena-api 2>$null
    if ($apiState -eq 'running') {
        foreach ($ep in @('/health', '/model', '/metrics')) {
            $ok, $code = Test-Http -Url "http://localhost:8000$ep"
            if ($ok) { Add-Check "api:$ep" 'PASS' "HTTP $code" }
            else { Add-Check "api:$ep" 'FAIL' "HTTP $code (or unreachable)" }
        }
    } else {
        Add-Check 'api:endpoints' 'SKIP' "api container not running (state=$apiState)"
    }

    # 6. nginx SPA
    $nginxState = & docker inspect --format '{{.State.Status}}' athena-nginx 2>$null
    if ($nginxState -eq 'running') {
        $ok, $code = Test-Http -Url 'http://localhost:8080/'
        if ($ok) { Add-Check 'nginx:spa' 'PASS' "HTTP $code" }
        else { Add-Check 'nginx:spa' 'FAIL' "HTTP $code" }
    } else {
        Add-Check 'nginx:spa' 'SKIP' "nginx not running (state=$nginxState)"
    }

    Write-Banner 'Health summary'
    $script:Checks | Format-Table -AutoSize | Out-String | Write-Host
    $failed = @($script:Checks | Where-Object { $_.Status -eq 'FAIL' })
    if ($failed.Count -gt 0) { Write-Err "$($failed.Count) check(s) failed."; exit 1 }
    Write-Ok 'All health checks passed.'
    exit 0
} catch {
    Write-Err "Health check failed: $($_.Exception.Message)"
    exit 1
}