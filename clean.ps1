#Requires -Version 7.0
<#
.SYNOPSIS
    Remove build artifacts and temporary files (non-destructive to data/stack).

.DESCRIPTION
    Root wrapper around goThrough/clean.ps1. Removes on-disk build artifacts
    (__pycache__, dist, .pytest_cache, etc.) and prunes dangling Docker images
    and build cache. Named volumes, the running stack, and source are left
    intact. For a full wipe use reset.ps1; for deep disk cleanup use
    goThrough/prune.ps1.

.EXAMPLE
    .\clean.ps1
#>
[CmdletBinding()]
param()
$inner = Join-Path $PSScriptRoot 'goThrough\clean.ps1'
$params = @()
if ($VerbosePreference -ne 'SilentlyContinue') { $params += '-Verbose' }
& $inner @params
exit $LASTEXITCODE