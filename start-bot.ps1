param(
    [switch]$OpenDashboard
)

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $repoRoot "frontend"
$frontendScript = Join-Path $frontendDir "serve_dist.py"
$frontendDistIndex = Join-Path $frontendDir "dist\index.html"

function Test-HttpHealthy {
    param(
        [string]$Url,
        [int]$TimeoutSec = 3
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing $Url -TimeoutSec $TimeoutSec
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-HttpHealthy {
    param(
        [string]$Url,
        [string]$Label,
        [int]$Attempts,
        [int]$SleepSeconds
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        if (Test-HttpHealthy -Url $Url) {
            return $true
        }
        Start-Sleep -Seconds $SleepSeconds
    }

    Write-Warning "$Label did not become healthy in time."
    return $false
}

function Stop-FrontendServer {
    try {
        $frontendProcesses = Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -like "*serve_dist.py*" -and $_.CommandLine -like "*$frontendDir*" }

        foreach ($process in $frontendProcesses) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch {
    }
}

function Start-FrontendServer {
    if (Test-HttpHealthy -Url "http://localhost:4173") {
        return $true
    }

    if (-not (Test-Path $frontendDistIndex)) {
        Write-Warning "Frontend build is missing at $frontendDistIndex"
        return $false
    }

    Stop-FrontendServer
    Write-Host "Starting dashboard server..."
    Start-Process python -ArgumentList "serve_dist.py" -WorkingDirectory $frontendDir -WindowStyle Hidden | Out-Null
    return (Wait-HttpHealthy -Url "http://localhost:4173" -Label "Dashboard server" -Attempts 20 -SleepSeconds 1)
}

Set-Location $repoRoot

Write-Host "Starting trading bot infrastructure..."
docker compose up -d postgres influxdb redis backend | Out-Null

$backendHealthy = Wait-HttpHealthy -Url "http://localhost:8000/health" -Label "Backend" -Attempts 45 -SleepSeconds 2
if (-not $backendHealthy) {
    Write-Warning "Retrying backend once..."
    docker compose restart backend | Out-Null
    $backendHealthy = Wait-HttpHealthy -Url "http://localhost:8000/health" -Label "Backend after retry" -Attempts 45 -SleepSeconds 2
}

$frontendHealthy = Start-FrontendServer

if ($OpenDashboard -and $frontendHealthy) {
    Start-Process "http://localhost:4173" | Out-Null
}

if ($backendHealthy -and $frontendHealthy) {
    Write-Host "Trading bot start sequence finished successfully."
} else {
    Write-Warning "Trading bot start sequence finished with warnings."
}
