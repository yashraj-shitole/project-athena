#Requires -Version 7.0
<#
.SYNOPSIS
    Apply the database schema (infra/init.sql) to the running Postgres.

.DESCRIPTION
    The schema is installed automatically on first DB creation by the
    postgres container's /docker-entrypoint-initdb.d hook. This script
    RE-APPLIES it against a running database  -  useful after pulling schema
    changes without wiping the volume.

    init.sql is idempotent (CREATE TABLE IF NOT EXISTS, DROP POLICY IF
    EXISTS before CREATE POLICY, ON CONFLICT DO NOTHING, CREATE OR REPLACE
    FUNCTION), so re-running is safe.

    The file is bind-mounted into the postgres container at
    /docker-entrypoint-initdb.d/00_init.sql, so we exec psql -f on that path
    (no need to copy anything).

.PARAMETER Debug
    Target the debug compose project (same postgres either way).

.EXAMPLE
    .\goThrough\migrate.ps1
#>
[CmdletBinding()]
param()
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    $user = [Environment]::GetEnvironmentVariable('ATHENA_POSTGRES_USER', 'Process'); if (-not $user) { $user = 'athena' }
    $db   = [Environment]::GetEnvironmentVariable('ATHENA_POSTGRES_DB', 'Process');  if (-not $db)   { $db = 'athena' }
    $sqlPath = '/docker-entrypoint-initdb.d/00_init.sql'

    Write-Step "Applying schema (infra/init.sql) as $user to db '$db'"
    # Ensure postgres is up.
    $h = Get-ContainerHealth -Container $script:ContainerFor['postgres']
    if ($h -ne 'healthy') {
        Write-Step 'Starting postgres...'
        if (-not (Invoke-Compose -CommandArgs @('up', '-d') -Services @('postgres') -Debug:$Debug)) { exit 1 }
        if (-not (Wait-Healthy -Services @('postgres') -TimeoutSec 60)) { exit 1 }
    }

    $psql = @('exec', '-T', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1', '-U', $user, '-d', $db, '-f', $sqlPath)
    if (-not (Invoke-Compose -CommandArgs $psql -Debug:$Debug)) { exit 1 }
    Write-Ok 'Schema applied.'
    exit 0
} catch {
    Write-Err "Migrate failed: $($_.Exception.Message)"
    exit 1
}