param(
    [ValidateSet("Prelaunch", "Daily", "Weekly", "Reset")]
    [string]$Mode = "Prelaunch",
    [switch]$OpenDashboard,
    [switch]$Truncate
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeFile = Join-Path $repoRoot "docker-compose.yml"
$frontendDir = Join-Path $repoRoot "frontend"
$frontendScript = Join-Path $frontendDir "serve_dist.py"

function Invoke-Compose {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )
    & docker compose -f $composeFile @Args
}

function Invoke-BackendRun {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )
    & docker compose -f $composeFile run --rm backend @Args
}

function Get-DockerLogsSafe {
    param(
        [string]$ContainerName,
        [int]$Tail = 200
    )
    return (& cmd.exe /d /c "docker logs $ContainerName --tail $Tail 2>&1" | Out-String -Stream)
}

function Get-HttpCheck {
    param(
        [string]$Url
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
        return [pscustomobject]@{
            Check = $Url
            Result = $response.StatusCode
            Status = "OK"
        }
    } catch {
        return [pscustomobject]@{
            Check = $Url
            Result = $_.Exception.Message
            Status = "FAIL"
        }
    }
}

function Ensure-FrontendServer {
    if (-not (Test-Path (Join-Path $frontendDir "dist\\index.html"))) {
        Push-Location $frontendDir
        try {
            npm run build | Out-Null
        } finally {
            Pop-Location
        }
    }

    $frontendProcess = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like "*serve_dist.py*" -and $_.CommandLine -like "*$frontendDir*" }

    if (-not $frontendProcess) {
        Start-Process python -ArgumentList "serve_dist.py" -WorkingDirectory $frontendDir -WindowStyle Hidden | Out-Null
        Start-Sleep -Seconds 2
    }
}

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "=== $Title ===" -ForegroundColor Cyan
}

switch ($Mode) {
    "Prelaunch" {
        Set-Location $repoRoot
        Write-Section "Starting Services"
        Invoke-Compose up -d postgres influxdb redis backend | Out-Null
        Ensure-FrontendServer

        if ($OpenDashboard) {
            Start-Process "http://localhost:4173" | Out-Null
        }

        Write-Section "Service Health"
        $healthRows = @()
        $healthRows += [pscustomobject]@{
            Check = "PostgreSQL"
            Result = ((Invoke-Compose exec -T postgres pg_isready -U trading_user -d trading_bot) | Out-String).Trim()
            Status = "OK"
        }
        $healthRows += [pscustomobject]@{
            Check = "Redis"
            Result = ((Invoke-Compose exec -T redis redis-cli ping) | Out-String).Trim()
            Status = "OK"
        }
        $influxHealth = Get-HttpCheck "http://localhost:8086/health"
        $backendHealth = Get-HttpCheck "http://localhost:8000/health"
        $dashboardHealth = Get-HttpCheck "http://localhost:4173"
        $healthRows += [pscustomobject]@{ Check = "InfluxDB"; Result = $influxHealth.Result; Status = $influxHealth.Status }
        $healthRows += [pscustomobject]@{ Check = "Backend API"; Result = $backendHealth.Result; Status = $backendHealth.Status }
        $healthRows += [pscustomobject]@{ Check = "Dashboard"; Result = $dashboardHealth.Result; Status = $dashboardHealth.Status }
        $healthRows | Format-Table -AutoSize

        Write-Section "Angel One Authentication"
        Invoke-BackendRun python -m backend.scripts.test_angel_one --symbol RELIANCE --days 5

        Write-Section "FinBERT Warmup"
        Invoke-BackendRun python -c "from backend.data.news_fetcher import NewsFetcher; print({'finbertLoaded': NewsFetcher.preload_sentiment_pipeline(wait=True)})"

        Write-Section "Holiday File"
        $holidayFile = Join-Path $repoRoot ("backend\\config\\nse_trading_holidays_{0}.json" -f (Get-Date).Year)
        $holidayStatus = "FAIL"
        if (Test-Path $holidayFile) {
            $holidayStatus = "OK"
        }
        [pscustomobject]@{
            Check = "Holiday file exists"
            Result = $holidayFile
            Status = $holidayStatus
        } | Format-Table -AutoSize

        Write-Section "Startup Logs"
        $startupLogs = Get-DockerLogsSafe -ContainerName "trading-bot-backend" -Tail 200
        $startupLogs | Select-String -Pattern "Angel One session established|FinBERT model loaded|Scheduler .* started|Market prep completed|Intraday scan completed|After-market analysis completed"
    }

    "Daily" {
        Set-Location $repoRoot
        Write-Section "Scheduler Logs"
        $logs = Get-DockerLogsSafe -ContainerName "trading-bot-backend" -Tail 200
        $logs | Select-String -Pattern "scheduler|job|error|warning|Market prep completed|Intraday scan completed|After-market analysis completed"

        Write-Section "Fallback Logs"
        $logs | Select-String -Pattern "fallback|redis.*fail|influx.*fail|scraper.*health|scraper warning"

        Write-Section "Kill Switch Logs"
        $logs | Select-String -Pattern "kill switch|daily loss|drawdown|India VIX"

        Write-Section "Daily Paper-Trade Checks"
        Invoke-BackendRun python -m backend.scripts.phase2_review daily
    }

    "Weekly" {
        Set-Location $repoRoot
        Write-Section "Weekly Review"
        Invoke-BackendRun python -m backend.scripts.phase2_review weekly
    }

    "Reset" {
        Set-Location $repoRoot
        Write-Section "Paper Portfolio Reset"
        if ($Truncate) {
            Invoke-BackendRun python -m backend.scripts.phase2_review reset --truncate
        } else {
            Invoke-BackendRun python -m backend.scripts.phase2_review reset
        }
    }
}
