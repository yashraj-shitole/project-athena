#Requires -Version 7.0
<#
.SYNOPSIS
    Run the security test suite.
#>
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot '..\..\scripts\goThrough\_helpers.ps1')
Enter-ProjectRoot

Write-Step 'Security tests'
$cmd = @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'pytest',
         '-ra', '--tb=short', '-m', 'security',
         'testing/workflows/security')
if (-not (Invoke-Compose -CommandArgs $cmd)) { exit 1 }
Write-Ok 'Security tests passed'
exit 0
