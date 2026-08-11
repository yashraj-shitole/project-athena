#Requires -Version 7.0
<#
.SYNOPSIS
    Open a psql session in the postgres container.

.DESCRIPTION
    Runs `docker compose exec postgres psql -U <user> <db>` using the
    ATHENA_POSTGRES_USER / ATHENA_POSTGRES_DB env vars (defaults: athena).

    Pass -Command to run a one-off SQL statement instead of an interactive
    psql session.

.PARAMETER Command
    Optional SQL to run non-interactively (e.g. "SELECT count(*) FROM users;").

.PARAMETER Debug
    Target the debug compose project.

.EXAMPLE
    .\scripts\goThrough\shell-db.ps1
    .\scripts\goThrough\shell-db.ps1 -Command '\dt'
#>
[CmdletBinding()]
param(
    [string]$Command
)
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    $user = [Environment]::GetEnvironmentVariable('ATHENA_POSTGRES_USER', 'Process'); if (-not $user) { $user = 'athena' }
    $db   = [Environment]::GetEnvironmentVariable('ATHENA_POSTGRES_DB', 'Process');  if (-not $db)   { $db = 'athena' }
    $running = Get-ContainerState -Container $script:ContainerFor['postgres']
    if ($running -ne 'running') {
        Write-Err "postgres container is not running (state: $running). Start it with ./docker-up.ps1 first."
        exit 1
    }
    if ($Command) {
        Write-Step "Running SQL: $Command"
        $execArgs = @('exec', '-T', 'postgres', 'psql', '-U', $user, '-d', $db, '-c', $Command)
    } else {
        Write-Step "Opening psql as $user -> db '$db' (Ctrl-D to exit)"
        $execArgs = @('exec', 'postgres', 'psql', '-U', $user, '-d', $db)
    }
    if (-not (Invoke-Compose -CommandArgs $execArgs -Debug:$Debug)) { exit 1 }
    exit 0
} catch {
    Write-Err "Shell-db failed: $($_.Exception.Message)"
    exit 1
}