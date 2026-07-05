#Requires -Version 7.0
<#
.SYNOPSIS
    Build the frontend image(s).

.DESCRIPTION
    Builds the `nginx` target (the production SPA + reverse proxy image). The
    nginx image internally builds the `web` stage (Vite production bundle), so
    a single `docker compose build nginx` produces the static SPA.

    With -IncludeDev, also builds the `web-dev` target (Vite HMR image) used by
    the debug profile.

.PARAMETER NoCache
    Pass --no-cache (full rebuild).

.PARAMETER Clean
    Remove the existing athena-nginx (and athena-web-dev) images first.

.PARAMETER IncludeDev
    Also build the web-dev (Vite) image.

.PARAMETER Production
    Enforce production-grade env validation.

.EXAMPLE
    .\goThrough\build-frontend.ps1 -Clean -IncludeDev
#>
[CmdletBinding()]
param(
    [switch]$NoCache,
    [switch]$Clean,
    [switch]$IncludeDev,
    [switch]$Production
)
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if (-not (Test-RequiredEnv -Strict:$Production)) { exit 1 }
    Ensure-NetworksAndVolumes
    if ($Clean) {
        foreach ($img in @('athena-nginx', 'athena-web-dev')) {
            & docker image rm -f $img 2>$null | Out-Null
        }
    }

    Write-Step 'Building image: nginx (production SPA + reverse proxy)'
    $buildArgs = @('build')
    if ($NoCache) { $buildArgs += '--no-cache' }
    if ($VerbosePreference -ne 'SilentlyContinue') { $buildArgs += '--progress=plain' }
    if (-not (Invoke-Compose -CommandArgs $buildArgs -Services @('nginx'))) { exit 1 }
    Write-Ok 'Frontend (nginx) image built.'

    if ($IncludeDev) {
        Write-Step 'Building image: web-dev (Vite HMR)'
        # Reuse the same build-args accumulator as the nginx build (do NOT use
        # the automatic $args variable  -  it is empty in a [CmdletBinding()]
        # script with only named params and would invoke compose with no
        # subcommand).
        $wdArgs = @('build')
        if ($NoCache) { $wdArgs += '--no-cache' }
        if ($VerbosePreference -ne 'SilentlyContinue') { $wdArgs += '--progress=plain' }
        if (-not (Invoke-Compose -CommandArgs $wdArgs -Services @('web-dev'))) { exit 1 }
        Write-Ok 'web-dev image built.'
    }
    exit 0
} catch {
    Write-Err "Build failed: $($_.Exception.Message)"
    exit 1
}