$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function ConvertTo-IPv4Cidr {
    param([string]$Cidr)

    $parts = $Cidr.Split('/')
    if ($parts.Count -gt 2) { return $null }

    try {
        $bytes = [System.Net.IPAddress]::Parse($parts[0]).GetAddressBytes()
    } catch {
        return $null
    }
    if ($bytes.Length -ne 4) { return $null }

    $prefix = 32
    if ($parts.Count -eq 2) {
        if (-not [int]::TryParse($parts[1], [ref]$prefix)) { return $null }
        if ($prefix -lt 0 -or $prefix -gt 32) { return $null }
    }

    $value = ([double]$bytes[0] * 16777216.0) +
        ([double]$bytes[1] * 65536.0) +
        ([double]$bytes[2] * 256.0) +
        [double]$bytes[3]
    return [pscustomobject]@{ Value = $value; Prefix = $prefix }
}

function Test-IPv4CidrOverlap {
    param([string]$Left, [string]$Right)

    $leftRange = ConvertTo-IPv4Cidr $Left
    $rightRange = ConvertTo-IPv4Cidr $Right
    if ($null -eq $leftRange -or $null -eq $rightRange) { return $false }

    $prefix = [Math]::Min($leftRange.Prefix, $rightRange.Prefix)
    $blockSize = [Math]::Pow(2, 32 - $prefix)
    return [Math]::Floor($leftRange.Value / $blockSize) -eq
        [Math]::Floor($rightRange.Value / $blockSize)
}

function Get-FreeDockerSubnet {
    $networkIds = @(& docker network ls --quiet 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Docker networks.' }

    $existingSubnets = @()
    foreach ($networkId in $networkIds) {
        $subnetText = & docker network inspect --format `
            '{{range .IPAM.Config}}{{.Subnet}} {{end}}' $networkId 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Docker network ranges.' }
        $existingSubnets += @(([string]$subnetText -split '\s+') |
            Where-Object { $_ -match '/' })
    }

    $start = Get-Random -Minimum 0 -Maximum 256
    for ($offset = 0; $offset -lt 256; $offset++) {
        $octet = ($start + $offset) % 256
        $candidate = "10.216.$octet.0/24"
        $overlap = $false
        foreach ($existingSubnet in $existingSubnets) {
            if (Test-IPv4CidrOverlap $candidate $existingSubnet) {
                $overlap = $true
                break
            }
        }
        if (-not $overlap) { return $candidate }
    }
    throw 'No unused smoke-test Docker subnet was found.'
}

function Test-LocalPortAvailable {
    param([int]$Port)

    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback, $Port
        )
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$projectName = 'order-tracker-copilot-smoke-' +
    [Guid]::NewGuid().ToString('N').Substring(0, 12)
$composeArgs = @(
    '-p', $projectName,
    '-f', 'compose.yaml',
    '-f', 'compose.demo.yaml',
    '-f', 'compose.copilot-smoke.yaml'
)
$portEnvironment = [ordered]@{
    ORDER_TRACKER_PORT = '18080'
    PROMETHEUS_PORT = '19090'
    LOKI_PORT = '13100'
    TEMPO_PORT = '13200'
    GRAFANA_PORT = '13000'
    INCIDENT_RESPONSE_PORT = '18001'
    AGENT_TIMEOUT_SECONDS = '120'
}
$previousEnvironment = @{}
$previousEnvironment['ORDER_TRACKER_SUBNET'] = [Environment]::GetEnvironmentVariable(
    'ORDER_TRACKER_SUBNET', 'Process'
)
$previousCopilotToken = [Environment]::GetEnvironmentVariable(
    'COPILOT_GITHUB_TOKEN', 'Process'
)
$stackAttempted = $false
$incidentVolume = ''
$incidentId = ''
$phase = 'preflight'
$failureMessage = 'Review the listed prerequisites; no credential values are displayed.'
$exitCode = 0

foreach ($name in $portEnvironment.Keys) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name, 'Process'
    )
}

Push-Location $repoRoot
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        $failureMessage = 'Docker CLI is not installed or is not on PATH.'
        throw 'Docker CLI unavailable.'
    }
    & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $failureMessage = 'Docker is unavailable. Start Docker Desktop and retry.'
        throw 'Docker daemon unavailable.'
    }

    foreach ($file in @(
        'compose.yaml', 'compose.demo.yaml', 'compose.copilot-smoke.yaml', '.env'
    )) {
        if (-not (Test-Path $file -PathType Leaf)) {
            $failureMessage = "Required file is missing: $file"
            throw 'Required file missing.'
        }
    }

    & git check-ignore --quiet .env 2>$null
    if ($LASTEXITCODE -ne 0) {
        $failureMessage = '.env must be ignored by Git before using a PAT.'
        throw '.env is not ignored.'
    }

    [Environment]::SetEnvironmentVariable('COPILOT_GITHUB_TOKEN', $null, 'Process')
    foreach ($name in $portEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable(
            $name, $portEnvironment[$name], 'Process'
        )
    }

    $ports = @(
        [int]$portEnvironment.ORDER_TRACKER_PORT,
        [int]$portEnvironment.PROMETHEUS_PORT,
        [int]$portEnvironment.LOKI_PORT,
        [int]$portEnvironment.TEMPO_PORT,
        [int]$portEnvironment.GRAFANA_PORT,
        [int]$portEnvironment.INCIDENT_RESPONSE_PORT
    )
    foreach ($port in $ports) {
        if (-not (Test-LocalPortAvailable $port)) {
            $failureMessage = "Smoke-test host port $port is already in use; no services were started."
            throw 'Smoke port conflict.'
        }
    }

    $subnet = Get-FreeDockerSubnet
    $previousEnvironment.ORDER_TRACKER_SUBNET = [Environment]::GetEnvironmentVariable(
        'ORDER_TRACKER_SUBNET', 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'ORDER_TRACKER_SUBNET', $subnet, 'Process'
    )

    $projectLabel = "label=com.docker.compose.project=$projectName"
    foreach ($resourceType in @('container', 'network', 'volume')) {
        if ($resourceType -eq 'container') {
            $existingResources = @(
                & docker ps --all --quiet --filter $projectLabel 2>$null
            )
        } elseif ($resourceType -eq 'network') {
            $existingResources = @(
                & docker network ls --quiet --filter $projectLabel 2>$null
            )
        } else {
            $existingResources = @(
                & docker volume ls --quiet --filter $projectLabel 2>$null
            )
        }
        if ($LASTEXITCODE -ne 0) {
            $failureMessage = 'Could not confirm the unique Compose project name is unused.'
            throw 'Compose project check failed.'
        }
        if ($existingResources.Count -gt 0) {
            $failureMessage = 'A resource already uses the generated smoke-test project name.'
            throw 'Compose project name collision.'
        }
    }

    $configJson = & docker compose @composeArgs config --format json 2>$null
    if ($LASTEXITCODE -ne 0) {
        $failureMessage = 'Compose configuration failed. Check .env without sharing its contents.'
        throw 'Compose config failed.'
    }
    try {
        $resolvedConfig = ($configJson -join [Environment]::NewLine) |
            ConvertFrom-Json
    } catch {
        $failureMessage = 'Compose configuration could not be validated.'
        throw 'Compose config could not be parsed.'
    }

    $configuredToken = [string]$resolvedConfig.services.'incident-response'.environment.COPILOT_GITHUB_TOKEN
    $hasConfiguredToken = -not [string]::IsNullOrWhiteSpace($configuredToken)
    $resolvedConfig.services.'incident-response'.environment.COPILOT_GITHUB_TOKEN = ''
    $configuredToken = $null
    $configJson = $null
    if (-not $hasConfiguredToken) {
        $failureMessage = '.env must define a non-empty COPILOT_GITHUB_TOKEN.'
        throw 'Copilot token missing.'
    }

    $incidentMounts = @(
        $resolvedConfig.services.'incident-response'.volumes |
            Where-Object { $_.target -eq '/incidents' }
    )
    if ($incidentMounts.Count -ne 1) {
        $failureMessage = 'Compose does not isolate /incidents in a named volume.'
        throw 'Incident storage is not isolated.'
    }
    if (
        $incidentMounts[0].type -ne 'volume' -or
        $incidentMounts[0].source -ne 'demo-incidents'
    ) {
        $failureMessage = 'Compose does not isolate /incidents in a named volume.'
        throw 'Incident storage is not isolated.'
    }

    $incidentVolume = [string]$resolvedConfig.volumes.'demo-incidents'.name
    if (-not $incidentVolume.StartsWith($projectName + '_')) {
        $failureMessage = 'The incident volume is not scoped to the unique project.'
        throw 'Incident volume is not project-scoped.'
    }
    foreach ($volume in $resolvedConfig.volumes.PSObject.Properties) {
        if (-not ([string]$volume.Value.name).StartsWith($projectName + '_')) {
            $failureMessage = 'A Compose data volume is not scoped to the unique project.'
            throw 'Volume is not project-scoped.'
        }
    }

    $resolvedConfig = $null
    Write-Host 'Preflight passed: Docker, ignored .env, token configuration, free host ports, and isolated volumes.'

    $phase = 'startup'
    $failureMessage = 'The isolated Compose stack could not start. No alert was sent.'
    $stackAttempted = $true
    & docker compose @composeArgs up --build --detach --wait
    if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed.' }

    & docker compose @composeArgs exec -T incident-response python -c `
        'import os,sys; sys.exit(0 if os.getenv("COPILOT_GITHUB_TOKEN","").strip() else 1)' 2>$null
    if ($LASTEXITCODE -ne 0) {
        $failureMessage = 'The responder container does not have a non-empty Copilot token. No alert was sent.'
        throw 'Responder token check failed.'
    }

    $responderUrl = 'http://127.0.0.1:' +
        $portEnvironment.INCIDENT_RESPONSE_PORT
    $healthy = $false
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$responderUrl/healthz" -TimeoutSec 5
            if ($health.status -eq 'ok') {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $healthy) {
        $failureMessage = 'The isolated responder health check failed. No alert was sent.'
        throw 'Responder health check failed.'
    }

    $phase = 'confirmation'
    Write-Host ''
    Write-Warning 'The next step sends one real Copilot CLI request and may consume your Copilot allowance.'
    Write-Host 'No alert or incident has been created yet. Type RUN to continue; any other input (including Enter) cancels.'
    $confirmation = Read-Host 'Confirm real Copilot request'
    if ($confirmation -cne 'RUN') {
        Write-Host 'Cancelled. No alert was sent and no incident was created.'
        $exitCode = 2
    } else {
        $phase = 'request'
        $failureMessage = 'The bounded request failed or timed out. It may have reached the responder; the evidence volume is retained.'
        $smokeId = [Guid]::NewGuid().ToString('N')
        $payload = @{
            status = 'firing'
            alertname = "Copilot CLI real smoke test $smokeId"
            endpoint = '/api/orders/{order_id}'
            description = "Explicitly confirmed local Copilot CLI smoke test $smokeId"
        } | ConvertTo-Json -Depth 5 -Compress

        $httpResponse = Invoke-WebRequest -UseBasicParsing `
            -Uri "$responderUrl/alerts" `
            -Method Post `
            -ContentType 'application/json' `
            -Body $payload `
            -TimeoutSec 180
        if ([int]$httpResponse.StatusCode -ne 202) {
            throw 'Responder did not return HTTP 202.'
        }
        $alertResult = $httpResponse.Content | ConvertFrom-Json
        $incidentId = [string]$alertResult.incident_id
        if ([string]::IsNullOrWhiteSpace($incidentId)) {
            throw 'Responder response did not contain an incident ID.'
        }

        Write-Host 'Alert accepted with HTTP 202.'
        Write-Host "Incident ID: $incidentId"
        Write-Host "Diagnosis status: $($alertResult.agent_status)"
        Write-Host "Isolated incident volume: $incidentVolume"
        Write-Host 'No /verify call was made; no patch was applied.'

        switch ([string]$alertResult.agent_status) {
            'completed' {
                Write-Host 'REAL COPILOT DIAGNOSIS SUCCEEDED.'
            }
            'timeout' {
                Write-Warning 'COPILOT DIAGNOSIS TIMED OUT.'
                $exitCode = 3
            }
            'failed' {
                Write-Warning 'COPILOT AUTHENTICATION, PROVIDER, OR CLI FAILURE.'
                $exitCode = 3
            }
            'unavailable' {
                Write-Warning 'COPILOT PROVIDER IS UNAVAILABLE.'
                $exitCode = 3
            }
            'malformed_output' {
                Write-Warning 'COPILOT RETURNED MALFORMED DIAGNOSIS OUTPUT.'
                $exitCode = 3
            }
            default {
                Write-Warning 'COPILOT DIAGNOSIS DID NOT COMPLETE SUCCESSFULLY.'
                $exitCode = 3
            }
        }

        try {
            $incident = Invoke-RestMethod `
                -Uri "$responderUrl/incidents/$incidentId" -TimeoutSec 10
            if ($incident.resolution_status -ne 'investigating') {
                Write-Warning "Unexpected incident resolution status: $($incident.resolution_status)"
                $exitCode = 3
            } else {
                Write-Host 'Incident remains resolution_status=investigating.'
            }
        } catch {
            Write-Warning 'Could not confirm incident state; inspect the retained evidence volume.'
            $exitCode = 3
        }
    }
} catch {
    Write-Host "Smoke launcher stopped during $phase."
    Write-Host $failureMessage
    if ($phase -eq 'request') { $exitCode = 3 } else { $exitCode = 1 }
} finally {
    try {
        if ($stackAttempted) {
            & docker compose @composeArgs down --remove-orphans 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'Could not remove all containers/networks from the unique smoke project; its volume was not removed.'
                $exitCode = 3
            }
        }
    } catch {
        Write-Warning 'Could not clean up the unique smoke project; its volume was not removed.'
        $exitCode = 3
    } finally {
        foreach ($name in $portEnvironment.Keys) {
            if ($previousEnvironment.ContainsKey($name)) {
                [Environment]::SetEnvironmentVariable(
                    $name, $previousEnvironment[$name], 'Process'
                )
            }
        }
        [Environment]::SetEnvironmentVariable(
            'ORDER_TRACKER_SUBNET', $previousEnvironment.ORDER_TRACKER_SUBNET, 'Process'
        )
        [Environment]::SetEnvironmentVariable(
            'COPILOT_GITHUB_TOKEN', $previousCopilotToken, 'Process'
        )
        Pop-Location

        if ($incidentVolume) {
            Write-Host "Named incident volume retained (never deleted): $incidentVolume"
        }
    }
}

exit $exitCode
