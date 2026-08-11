#Requires -Version 7.0
<#
.SYNOPSIS
    Run the /testing/ smoke suite against a running api container.

.DESCRIPTION
    Mirrors the run_smoke.sh POSIX script. The smoke suite is fast
    (~30s) and runs against a live api at $ATHENA_TEST_BASE_URL
    (default http://localhost:8000). Use `-Integration` to include
    the integration-tagged smoke tests.
#>
[CmdletBinding()]
param(
    [switch]$Integration
)
. (Join-Path $PSScriptRoot '..\..\scripts\goThrough\_helpers.ps1')
Enter-ProjectRoot

if (-not (Test-Prereqs)) { exit 1 }

$script:SmokeDir = Join-Path $REPO_ROOT 'testing\workflows\smoke'
$script:RepoTesting = Join-Path $REPO_ROOT 'testing'

Write-Step 'Smoke tests'
$cmdArgs = @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'pytest',
             '-ra', '--tb=short', '-m', 'smoke')
if (-not $Integration) {
    $cmdArgs += @('--ignore=' + (Join-Path $script:RepoTesting 'workflows/integration'))
} else {
    $cmdArgs += @('-m', 'smoke or integration')
}
$cmdArgs += @('workflows/smoke', 'workflows/integration')

if (-not (Invoke-Compose -CommandArgs $cmdArgs)) { exit 1 }
Write-Ok 'Smoke tests passed'
exit 0
