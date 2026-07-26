#Requires -Version 7.0
<#
.SYNOPSIS
    Build the "worker"  -  in this project, ingestion runs inside the API.

.DESCRIPTION
    Project Athena does not run a separate ingestion worker container: the
    ingestion pipeline (extract -> clean -> chunk -> embed -> keywords ->
    index) runs inline in the `api` service on document upload (see
    backend/app/services/ingestion/pipeline.py). There is no separate worker
    image to build.

    So this script is a functional alias: it builds the `api` target, which
    contains the ingestion pipeline. It exists so a generic workflow / CI
    matrix that references `build-worker` works unchanged against this repo.

.PARAMETER NoCache
    Pass --no-cache (full rebuild).

.PARAMETER Clean
    Remove the existing athena-api image first.

.EXAMPLE
    .\scripts\goThrough\build-worker.ps1 -NoCache
#>
[CmdletBinding()]
param(
    [switch]$NoCache,
    [switch]$Clean
)
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

Write-Info 'This project runs ingestion inline in the `api` container (no separate worker). Building `api`.'
$inner = Join-Path $PSScriptRoot 'build-backend.ps1'
$params = @()
if ($NoCache)  { $params += '-NoCache' }
if ($Clean)    { $params += '-Clean' }
if ($VerbosePreference -ne 'SilentlyContinue') { $params += '-Verbose' }
& $inner @params
exit $LASTEXITCODE