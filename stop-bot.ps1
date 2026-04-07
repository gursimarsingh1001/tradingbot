$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $repoRoot "frontend"

$frontendProcesses = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*serve_dist.py*" -and $_.CommandLine -like "*$frontendDir*" }

foreach ($process in $frontendProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Set-Location $repoRoot

Write-Host "Stopping trading bot containers..."
docker compose stop backend redis influxdb postgres | Out-Null
Write-Host "Trading bot stop sequence finished."
