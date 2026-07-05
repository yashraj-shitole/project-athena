#Requires -Version 7.0
<#
.SYNOPSIS
    Build the "AI service"  -  in this project, the LLM-orchestrating API.

.DESCRIPTION
    Project Athena does not run a separate "AI service" container: the LLM
    client, orchestrator, tool-calling, and Ollama integration all live inside
    the single `api` service (see backend/app/services/llm and
    backend/app/services/orchestrator). The local LLM runtime itself (Ollama)
    is a prebuilt third-party image, not something we build.

    So this script is a functional alias: it builds the `api` target, which IS
    the AI service. It exists so a generic workflow / CI matrix that references
    `build-ai-service` works unchanged against this repo.

.PARAMETER NoCache
    Pass --no-cache (full rebuild).

.PARAMETER Clean
    Remove the existing athena-api image first.

.EXAMPLE
    .\goThrough\build-ai-service.ps1 -NoCache
#>
[CmdletBinding()]
param(
    [switch]$NoCache,
    [switch]$Clean
)
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

Write-Info 'This project folds the AI service into the `api` container (orchestrator + LLM client live there). Building `api`.'
$inner = Join-Path $PSScriptRoot 'build-backend.ps1'
$params = @()
if ($NoCache)  { $params += '-NoCache' }
if ($Clean)    { $params += '-Clean' }
if ($VerbosePreference -ne 'SilentlyContinue') { $params += '-Verbose' }
& $inner @params
exit $LASTEXITCODE