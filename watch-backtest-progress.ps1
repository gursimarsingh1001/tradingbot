param(
    [int]$RefreshSeconds = 5
)

$ErrorActionPreference = "Stop"
$projectRoot = "D:\trading-bot"
$sql = @"
WITH progress_row AS (
    SELECT value
    FROM bot_config
    WHERE key = 'backtest_progress'
),
combined_symbols AS (
    SELECT stock_symbol AS symbol
    FROM backtest_trades
    WHERE stock_symbol IS NOT NULL
    UNION
    SELECT symbol
    FROM stock_strategy_map
    WHERE symbol IS NOT NULL
)
SELECT
    COALESCE((SELECT COUNT(*) FROM backtest_trades), 0) AS trade_count,
    COALESCE((SELECT COUNT(*) FROM combined_symbols), 0) AS symbol_count,
    COALESCE((SELECT value->>'active' FROM progress_row), 'false') AS active,
    COALESCE((SELECT value->>'progress' FROM progress_row), '0') AS api_progress,
    COALESCE((SELECT value->>'message' FROM progress_row), 'Idle') AS message;
"@

function Get-ProgressSnapshot {
    $raw = docker exec trading-bot-postgres psql -U trading_user -d trading_bot -t -A -F "|" -c $sql
    if (-not $raw) {
        throw "No output returned from PostgreSQL"
    }

    $line = ($raw | Select-Object -Last 1).Trim()
    $parts = $line -split "\|", 5
    if ($parts.Count -lt 5) {
        throw "Unexpected PostgreSQL output: $line"
    }

    $tradeCount = [int]$parts[0]
    $symbolCount = [int]$parts[1]
    $active = ($parts[2].Trim().ToLowerInvariant() -eq "true")
    $apiProgress = [int]$parts[3]
    $message = $parts[4].Trim()

    $totalSymbols = 0
    $workerCount = $null
    if ($message -match "for\s+(\d+)\s+symbols") {
        $totalSymbols = [int]$Matches[1]
    }
    if ($message -match "using\s+(\d+)\s+workers") {
        $workerCount = [int]$Matches[1]
    }

    $confirmedPct = if ($totalSymbols -gt 0) {
        [math]::Round(($symbolCount / $totalSymbols) * 100, 2)
    } else {
        0
    }

    [pscustomobject]@{
        TradeCount = $tradeCount
        SymbolCount = $symbolCount
        TotalSymbols = $totalSymbols
        Active = $active
        ApiProgress = $apiProgress
        ConfirmedProgress = $confirmedPct
        WorkerCount = $workerCount
        Message = $message
    }
}

while ($true) {
    $snapshot = Get-ProgressSnapshot
    Clear-Host
    Write-Host "Backtest Progress Monitor" -ForegroundColor Cyan
    Write-Host ("Time: {0}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"))
    Write-Host ""
    Write-Host ("Active:              {0}" -f $snapshot.Active)
    Write-Host ("Workers:             {0}" -f ($(if ($snapshot.WorkerCount) { $snapshot.WorkerCount } else { "unknown" })))
    Write-Host ("Trades Written:      {0}" -f $snapshot.TradeCount)
    Write-Host ("Symbols Confirmed:   {0}/{1}" -f $snapshot.SymbolCount, $(if ($snapshot.TotalSymbols -gt 0) { $snapshot.TotalSymbols } else { "?" }))
    Write-Host ("Confirmed Progress:  {0}%" -f $snapshot.ConfirmedProgress)
    Write-Host ("API Progress:        {0}%" -f $snapshot.ApiProgress)
    Write-Host ""
    Write-Host ("Status: {0}" -f $snapshot.Message)

    if (-not $snapshot.Active) {
        break
    }

    Start-Sleep -Seconds $RefreshSeconds
}
