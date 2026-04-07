param(
    [switch]$VerboseOutput,
    [switch]$LocalOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\\python.exe"
$pytestArgs = @("-m", "pytest")
$dockerName = "trading-bot-backend"
$dockerRunId = [guid]::NewGuid().ToString("N")
$dockerTestsPath = "/tmp/codex-pytests-$dockerRunId"
$dockerPytestArgs = @("$dockerTestsPath/tests", "-q", "--import-mode=importlib", "-p", "no:cacheprovider")

if ($VerboseOutput) {
    $pytestArgs += "-vv"
    $dockerPytestArgs = @("$dockerTestsPath/tests", "-vv", "--import-mode=importlib", "-p", "no:cacheprovider")
}

if (-not $LocalOnly) {
    try {
        $containerState = docker inspect -f "{{.State.Running}}" $dockerName 2>$null
    }
    catch {
        $containerState = $null
    }

    if ($containerState -eq "true") {
        Write-Host "Running pytest inside Docker container $dockerName" -ForegroundColor Cyan
        docker exec $dockerName sh -lc "mkdir -p $dockerTestsPath"
        docker cp (Join-Path $projectRoot "tests") "$dockerName`:$dockerTestsPath" | Out-Null
        docker exec $dockerName python -m pytest @dockerPytestArgs
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path $pythonExe)) {
    Write-Host "Python test environment not found at $venvPath" -ForegroundColor Yellow
    Write-Host "Either start Docker and rerun .\\run-tests.ps1, or create the local .venv and install requirements-dev.txt." -ForegroundColor Yellow
    exit 1
}

Push-Location $projectRoot
try {
    Write-Host "Running pytest in local .venv" -ForegroundColor Cyan
    & $pythonExe @pytestArgs
}
finally {
    Pop-Location
}
