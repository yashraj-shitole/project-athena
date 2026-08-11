#Requires -Version 7.0
<#
.SYNOPSIS
    Run every testing/ suite in sequence.
#>
[CmdletBinding()]
param(
    [switch]$SkipPerf,
    [switch]$SkipEvals
)
. (Join-Path $PSScriptRoot '..\..\scripts\goThrough\_helpers.ps1')
Enter-ProjectRoot

$ok = $true
Write-Banner 'Athena testing: all suites'

if (-not (Test-Prereqs)) { exit 1 }

# Smoke (fast, <60s)
& (Join-Path $PSScriptRoot 'run_smoke.ps1'); if ($LASTEXITCODE -ne 0) { $ok = $false }

# Security
& (Join-Path $PSScriptRoot 'run_security.ps1'); if ($LASTEXITCODE -ne 0) { $ok = $false }

if (-not $SkipEvals) {
    & (Join-Path $PSScriptRoot 'run_evals.ps1'); if ($LASTEXITCODE -ne 0) { $ok = $false }
}
if (-not $SkipPerf) {
    & (Join-Path $PSScriptRoot 'run_perf.ps1'); if ($LASTEXITCODE -ne 0) { $ok = $false }
}

if ($ok) { Write-Ok 'All testing/ suites passed'; exit 0 } else { exit 1 }
