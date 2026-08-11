#Requires -Version 7.0
<#
.SYNOPSIS
    Build only the backend (api) image.

.DESCRIPTION
    Builds the Dockerfile `api` target (the FastAPI runtime image). This is
    the image the `api` compose service uses.

.PARAMETER NoCache
    Pass --no-cache (full rebuild).

.PARAMETER Clean
    Remove the existing athena-api image first.

.PARAMETER Production
    Enforce production-grade env validation.

.EXAMPLE
    .\scripts\goThrough\build-backend.ps1 -NoCache -Verbose
#>
[CmdletBinding()]
param(
    [switch]$NoCache,
    [switch]$Clean,
    [switch]$Production
)
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    if (-not (Test-RequiredEnv -Strict:$Production)) { exit 1 }
    Ensure-NetworksAndVolumes
    if ($Clean) { & docker image rm -f athena-api 2>$null | Out-Null }

    Write-Step 'Building image: api (backend)'
    $buildArgs = @('build')
    if ($NoCache) { $buildArgs += '--no-cache' }
    if ($VerbosePreference -ne 'SilentlyContinue') { $buildArgs += '--progress=plain' }
    if (-not (Invoke-Compose -CommandArgs $buildArgs -Services @('api'))) { exit 1 }
    Write-Ok 'Backend image built.'
    exit 0
} catch {
    Write-Err "Build failed: $($_.Exception.Message)"
    exit 1
}