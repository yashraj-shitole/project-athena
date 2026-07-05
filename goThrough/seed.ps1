#Requires -Version 7.0
<#
.SYNOPSIS
    Seed dev data: register a demo user (and optionally upload a sample doc).

.DESCRIPTION
    Exercises the real API to create a demo account so you can log in and
    try the app immediately. Hits the local API (http://localhost:8000 by
    default; override with -ApiBase). The stack must be up (./docker-up.ps1).

    If the user already exists (4xx from register), the script logs in
    instead  -  seeding is idempotent for dev.

.PARAMETER Email
    Demo user email (default: test@example.com).

.PARAMETER Password
    Demo user password (default: hunter222  -  meets the API's strength rules).

.PARAMETER ApiBase
    API base URL (default: http://localhost:8000). The /api prefix is added.

.PARAMETER WithSampleDoc
    After register+login, upload the repo README.md as a sample document.

.EXAMPLE
    .\goThrough\seed.ps1
    .\goThrough\seed.ps1 -Email me@dev.local -WithSampleDoc
#>
[CmdletBinding()]
param(
    [string]$Email = 'test@example.com',
    [string]$Password = 'hunter222',
    [string]$ApiBase = 'http://localhost:8000',
    [switch]$WithSampleDoc
)
. (Join-Path $PSScriptRoot '_helpers.ps1')
Enter-ProjectRoot

function Send-Json {
    param([string]$Method, [string]$Url, [object]$Body)
    $json = $Body | ConvertTo-Json -Compress
    try {
        $r = Invoke-WebRequest -Method $Method -Uri $Url `
            -Headers @{ 'Content-Type' = 'application/json' } `
            -Body $json -SkipHttpErrorCheck -UseBasicParsing
        return $r
    } catch {
        Write-Err "Request to $Url failed: $($_.Exception.Message)"
        return $null
    }
}

function ConvertFrom-ResponseBody {
    param([string]$Content)
    if ([string]::IsNullOrWhiteSpace($Content)) { return $null }
    try { return $Content | ConvertFrom-Json } catch { return $null }
}

try {
    if (-not (Test-Prereqs)) { exit 1 }

    $registerUrl = "$ApiBase/api/auth/register"
    Write-Step "Registering demo user: $Email"
    $r = Send-Json -Method POST -Url $registerUrl -Body @{ email = $Email; password = $Password }
    if (-not $r) { exit 1 }
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) {
        Write-Ok "Registered new user ($($r.StatusCode))."
    } else {
        Write-Info "Register returned $($r.StatusCode)  -  assuming user already exists; will log in."
    }

    # Log in to confirm credentials work and grab a token.
    Write-Step 'Logging in to verify credentials'
    $login = Send-Json -Method POST -Url "$ApiBase/api/auth/login-json" -Body @{ email = $Email; password = $Password }
    if (-not $login) { exit 1 }
    if ($login.StatusCode -ge 300) {
        Write-Err "Login failed ($($login.StatusCode)): $($login.Content)"
        exit 1
    }
    $loginBody = ConvertFrom-ResponseBody -Content $login.Content
    $token = $null
    if ($loginBody) { $token = $loginBody.access_token }
    if ($token) {
        Write-Ok 'Login OK  -  access token acquired.'
    } else {
        Write-Warn 'Login returned no access_token in the body; check API logs / response shape.'
    }

    if ($WithSampleDoc -and $token) {
        $docPath = Join-Path $script:REPO_ROOT 'README.md'
        if (-not (Test-Path $docPath)) {
            Write-Warn 'README.md not found; skipping sample upload.'
        } else {
            Write-Step 'Uploading sample doc: README.md'
            $uri = "$ApiBase/api/documents"
            $headers = @{ Authorization = "Bearer $token" }
            try {
                $up = Invoke-WebRequest -Method POST -Uri $uri -Headers $headers `
                    -Form @{ file = Get-Item -Path $docPath } -SkipHttpErrorCheck -UseBasicParsing
                $upBody = ConvertFrom-ResponseBody -Content $up.Content
                if ($up.StatusCode -lt 300) {
                    $id = if ($upBody) { $upBody.id } else { '(unknown)' }
                    Write-Ok "Uploaded sample doc (id: $id)"
                } else {
                    Write-Warn "Sample upload returned $($up.StatusCode): $($up.Content)"
                }
            } catch {
                Write-Warn "Sample upload failed: $($_.Exception.Message)"
            }
        }
    }

    Write-Info "Login at the SPA with: $Email / $Password"
    exit 0
} catch {
    Write-Err "Seed failed: $($_.Exception.Message)"
    exit 1
}