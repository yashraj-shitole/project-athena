#Requires -Version 7.0
<#
.SYNOPSIS
    Open an interactive shell inside the api (backend) container.

.DESCRIPTION
    Runs `docker compose exec api bash` so you get a shell as the athena user
    inside the running backend container  -  handy for poking at the Python
    REPL, running alembic, inspecting /app/storage, etc.

    Pass -Command to run a one-off command instead of an interactive shell.

.PARAMETER Command
    Optional command to run non-interactively (e.g. 'python -c "import app"').

.PARAMETER Debug
    Target the debug compose project.

.EXAMPLE
    .\scripts\goThrough\shell-backend.ps1
    .\scripts\goThrough\shell-backend.ps1 -Command 'python -c "import this"'
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
    $running = Get-ContainerState -Container $script:ContainerFor['api']
    if ($running -ne 'running') {
        Write-Err "api container is not running (state: $running). Start it with ./docker-up.ps1 first."
        exit 1
    }
    if ($Command) {
        Write-Step "Running in api: $Command"
        $execArgs = @('exec', 'api', 'bash', '-lc', $Command)
    } else {
        Write-Step 'Opening shell in api container (Ctrl-D to exit)'
        $execArgs = @('exec', 'api', 'bash')
    }
    # Interactive exec must stream to the TTY; do not capture.
    if (-not (Invoke-Compose -CommandArgs $execArgs -Debug:$Debug)) { exit 1 }
    exit 0
} catch {
    Write-Err "Shell failed: $($_.Exception.Message)"
    exit 1
}