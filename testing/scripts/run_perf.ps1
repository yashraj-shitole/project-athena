#Requires -Version 7.0
<#
.SYNOPSIS
    Run the performance test suite (latency + throughput benchmarks).
#>
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot '..\goThrough\_helpers.ps1')
Enter-ProjectRoot

Write-Step 'Performance benchmarks'
$cmd = @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'pytest',
         '-ra', '--tb=short', '-m', 'perf and not integration',
         'testing/workflows/performance')
if (-not (Invoke-Compose -CommandArgs $cmd)) { exit 1 }
Write-Ok 'Performance benchmarks passed'
exit 0
