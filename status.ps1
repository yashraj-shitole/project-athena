#Requires -Version 7.0
<#
.SYNOPSIS
    Show container health, ports, and running services.

.DESCRIPTION
    Prints a per-service table (container, state, health, ports) and the
    `docker compose ps` view. Works whether you're in normal or debug mode.

.PARAMETER Debug
    Target the debug compose project (includes web-dev).

.EXAMPLE
    .\status.ps1
    .\status.ps1 -Debug
#>
[CmdletBinding()]
param()
# -Debug is a reserved common parameter under [CmdletBinding()], so it is NOT
# declared above; recover the user's flag from the bound common parameter.
$Debug = $PSBoundParameters.ContainsKey('Debug') -and [bool]$PSBoundParameters['Debug']
. (Join-Path $PSScriptRoot 'goThrough\_helpers.ps1')
Enter-ProjectRoot

try {
    if (-not (Test-Prereqs)) { exit 1 }
    Write-Banner 'Project Athena  -  status'

    $services = @('postgres', 'redis', 'ollama', 'api', 'nginx')
    if ($Debug) { $services += 'web-dev' }

    $rows = [System.Collections.Generic.List[object]]::new()
    foreach ($svc in $services) {
        $container = $script:ContainerFor[$svc]
        $state = & docker inspect --format '{{.State.Status}}' $container 2>$null
        if (-not $state) { $state = 'absent' }
        $health = Get-ContainerHealth -Container $container
        $ports = & docker inspect --format '{{range $p, $conf := .NetworkSettings.Ports}}{{$p}}->{{(index $conf 0).HostPort}} {{end}}' $container 2>$null
        if (-not $ports) { $ports = '-' }
        $rows.Add([pscustomobject]@{
            Service = $svc; Container = $container; State = $state
            Health = $health; Ports = $ports.Trim()
        })
    }
    $rows | Format-Table -AutoSize | Out-String | Write-Host

    Show-ComposeStatus -Debug:$Debug
    Write-Info 'Legend: Health = healthy | starting | unhealthy | no-healthcheck | missing | absent'
    exit 0
} catch {
    Write-Err "Status failed: $($_.Exception.Message)"
    exit 1
}