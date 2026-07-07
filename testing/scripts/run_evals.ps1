#Requires -Version 7.0
<#
.SYNOPSIS
    Run the LLM evaluation suite.
#>
[CmdletBinding()]
param(
    [ValidateSet("ollama", "openai", "heuristic")]
    [string]$Judge = "ollama",
    [switch]$Baseline,
    [switch]$Check
)
. (Join-Path $PSScriptRoot '..\goThrough\_helpers.ps1')
Enter-ProjectRoot

$script:Runner = Join-Path $REPO_ROOT 'testing/llm_evals/runners/run_eval.py'
$script:Reports = Join-Path $REPO_ROOT 'testing/llm_evals/reports'

$args = @('--judge', $Judge)
if ($Baseline) { $args += '--baseline' }
if ($Check)    { $args += '--check'    }

Write-Step "Running eval suite (judge=$Judge baseline=$Baseline check=$Check)"
& python $script:Runner @args
exit $LASTEXITCODE
