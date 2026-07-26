#Requires -Version 7.0
<#
.SYNOPSIS
    Run the project test suites (backend unit / integration, frontend, E2E, coverage).

.DESCRIPTION
    Runs tests INSIDE the Docker containers so no local Python/Node venv is
    required  -  deps come from the built images.

      Backend unit        : docker compose run --rm --no-deps api python -m pytest
                            (hermetic; uses in-memory SQLite via conftest.py)
      Backend integration  : docker compose run --rm api python -m pytest --run-integration
                            (starts postgres/redis/ollama via depends_on)
      Frontend            : docker compose run --rm --no-deps web-dev npm test
                            (skipped with a note if no "test" script  -  Phase 2)
      E2E                 : not configured in Phase 2 -> reported as skipped
      Coverage            : backend unit tests with pytest-cov (installed lazily)

    Aggregates results into a summary table and exits 1 if any suite failed.

.PARAMETER Backend
    Run only the backend unit tests.

.PARAMETER Frontend
    Run only the frontend tests (skipped if none configured).

.PARAMETER Integration
    Run backend integration tests (requires / starts the infra services).

.PARAMETER E2E
    Run end-to-end tests (skipped  -  not configured in Phase 2).

.PARAMETER Coverage
    Run backend unit tests with coverage (pytest-cov, installed lazily).

.PARAMETER CI
    CI mode: backend unit tests, fail-fast, terse output, coverage on.

.EXAMPLE
    .\scripts\test.ps1
    .\scripts\test.ps1 -Backend -Verbose
    .\scripts\test.ps1 -Integration
    .\scripts\test.ps1 -Coverage
    .\scripts\test.ps1 -CI
#>
[CmdletBinding()]
param(
    [switch]$Backend,
    [switch]$Frontend,
    [switch]$Integration,
    [switch]$E2E,
    [switch]$Coverage,
    [switch]$CI
)
. (Join-Path $PSScriptRoot 'goThrough\_helpers.ps1')
Enter-ProjectRoot

# A result row: Name, Status (PASS/FAIL/SKIP), Detail
$script:Results = [System.Collections.Generic.List[object]]::new()

function Add-Result { param([string]$Name,[string]$Status,[string]$Detail)
    $script:Results.Add([pscustomobject]@{ Suite = $Name; Status = $Status; Detail = $Detail })
    switch ($Status) {
        'PASS' { Write-Ok "$Name : PASS" }
        'FAIL' { Write-Err "$Name : FAIL - $Detail" }
        'SKIP' { Write-Info "$Name : SKIP - $Detail" }
    }
}

function Run-BackendUnit {
    $cmdArgs = @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'pytest')
    if ($VerbosePreference -ne 'SilentlyContinue') { $cmdArgs += '-v' }
    if ($CI) { $cmdArgs += @('--tb=short', '--maxfail=1', '-q') }
    Write-Step 'Backend unit tests (hermetic, in-memory SQLite)'
    if (-not (Invoke-Compose -CommandArgs $cmdArgs)) { Add-Result 'BackendUnit' 'FAIL' 'pytest exited non-zero'; return $false }
    Add-Result 'BackendUnit' 'PASS' 'unit tests passed'
    return $true
}

function Run-BackendIntegration {
    Write-Step 'Backend integration tests (starts postgres/redis/ollama)'
    $cmdArgs = @('run', '--rm', 'api', 'python', '-m', 'pytest', '--run-integration')
    if ($VerbosePreference -ne 'SilentlyContinue') { $cmdArgs += '-v' }
    if (-not (Invoke-Compose -CommandArgs $cmdArgs)) { Add-Result 'BackendIntegration' 'FAIL' 'pytest exited non-zero'; return $false }
    Add-Result 'BackendIntegration' 'PASS' 'integration tests passed'
    return $true
}

function Run-Coverage {
    Write-Step 'Backend coverage (pytest-cov installed lazily in the run container)'
    $cmd = 'pip install --user --quiet pytest-cov 2>/dev/null; python -m pytest --cov=app --cov-report=term-missing'
    $cmdArgs = @('run', '--rm', '--no-deps', 'api', 'sh', '-lc', $cmd)
    if (-not (Invoke-Compose -CommandArgs $cmdArgs)) { Add-Result 'Coverage' 'FAIL' 'coverage run failed'; return $false }
    Add-Result 'Coverage' 'PASS' 'coverage report printed above'
    return $true
}

function Test-FrontendHasTests {
    $pkg = Join-Path $script:REPO_ROOT 'frontend\package.json'
    if (-not (Test-Path $pkg)) { return $false }
    try {
        $j = Get-Content $pkg -Raw | ConvertFrom-Json
        return [bool]$j.scripts.test
    } catch { return $false }
}

function Run-Frontend {
    if (-not (Test-FrontendHasTests)) {
        Add-Result 'Frontend' 'SKIP' 'no "test" script in frontend/package.json (Phase 2: Vitest/Playwright)'
        return $true
    }
    Write-Step 'Frontend tests'
    $cmdArgs = @('run', '--rm', '--no-deps', 'web-dev', 'npm', 'test')
    if (-not (Invoke-Compose -CommandArgs $cmdArgs)) { Add-Result 'Frontend' 'FAIL' 'npm test exited non-zero'; return $false }
    Add-Result 'Frontend' 'PASS' 'frontend tests passed'
    return $true
}

function Run-E2E {
    Add-Result 'E2E' 'SKIP' 'Playwright E2E not configured (Phase 2)'
    return $true
}

try {
    if (-not (Test-Prereqs)) { exit 1 }

    # Decide what to run.
    $anyFlag = $Backend -or $Frontend -or $Integration -or $E2E -or $Coverage
    $runAll = -not $anyFlag
    $ok = $true

    # Ensure images exist for the suites we need.
    $needApi   = $runAll -or $Backend -or $Integration -or $Coverage -or $CI
    $needWeb   = $runAll -or $Frontend
    if ($CI) { $needApi = $true; $Coverage = $true }
    if ($needApi -and -not (& docker images --filter "reference=athena-api" -q 2>$null)) {
        Write-Step 'Building api image (missing)'
        if (-not (Invoke-Compose -CommandArgs @('build') -Services @('api'))) { exit 1 }
    }
    if ($needWeb -and -not (& docker images --filter "reference=athena-web-dev" -q 2>$null)) {
        Write-Step 'Building web-dev image (missing)'
        if (-not (Invoke-Compose -CommandArgs @('build') -Services @('web-dev'))) { exit 1 }
    }

    if ($CI) {
        $ok = (Run-BackendUnit) -and $ok
        $ok = (Run-Coverage) -and $ok
    } elseif ($runAll) {
        $ok = (Run-BackendUnit) -and $ok
        $ok = (Run-Frontend) -and $ok
        $ok = (Run-E2E) -and $ok
    } else {
        if ($Coverage)     { $ok = (Run-Coverage) -and $ok }
        if ($Backend)      { $ok = (Run-BackendUnit) -and $ok }
        if ($Integration) { $ok = (Run-BackendIntegration) -and $ok }
        if ($Frontend)     { $ok = (Run-Frontend) -and $ok }
        if ($E2E)          { $ok = (Run-E2E) -and $ok }
    }

    Write-Banner 'Test summary'
    $script:Results | Format-Table -AutoSize | Out-String | Write-Host
    $failed = @($script:Results | Where-Object { $_.Status -eq 'FAIL' })
    if ($failed.Count -gt 0) {
        Write-Err "$($failed.Count) suite(s) failed."
        exit 1
    }
    Write-Ok 'All requested suites passed (or were skipped).'
    exit 0
} catch {
    Write-Err "Test runner failed: $($_.Exception.Message)"
    exit 1
}