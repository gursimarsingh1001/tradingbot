# Trading Bot

Professional AI-powered Indian market trading bot with a Python backend, React dashboard, local databases, live paper trading, backtesting, after-market watchlists, daily fundamentals refresh, and near-real-time news sync.

## Final Package Features

- Live paper trading for intraday and investment setups
- Walk-forward backtesting with stock-specific strategy mapping
- Local persistent storage using PostgreSQL, InfluxDB, and Redis Docker volumes
- Daily structured fundamentals refresh
- Near-real-time news sync and sentiment scoring
- Daily markdown report generation
- Daily local application backup export
- Optional Telegram phone alerts
- Windows start/stop/autostart scripts

## Main Local URLs

- Dashboard: `http://localhost:4173`
- Backend API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

## Core Environment Variables

- `ANGEL_ONE_API_KEY`
- `ANGEL_ONE_CLIENT_ID`
- `ANGEL_ONE_PASSWORD`
- `ANGEL_ONE_TOTP_SECRET`
- `NEWS_API_KEY`
- `POSTGRES_URL`
- `REDIS_URL`
- `INFLUX_URL`
- `INFLUX_TOKEN`
- `INFLUX_ORG`
- `INFLUX_BUCKET`
- `PAPER_PORTFOLIO_VALUE`

## Final Package Environment Variables

- `ALERTS_ENABLED=true`
- `TELEGRAM_BOT_TOKEN=`
- `TELEGRAM_CHAT_ID=`
- `BACKUPS_DIR=D:\trading-bot\logs\backups`
- `REPORTS_DIR=D:\trading-bot\logs\reports`
- `BACKUP_RETENTION_DAYS=14`

## Daily Operating Flow

1. Keep the PC and Docker running.
2. Start the package with:

```powershell
.\start-bot.ps1 -OpenDashboard
```

3. Stop the package with:

```powershell
.\stop-bot.ps1
```

4. Optional Windows auto-start at logon:

```powershell
.\install-autostart-task.ps1
```

## Scheduler Overview

- `09:00` market-open preparation
- Every `5 minutes` during market hours: intraday scan
- Every `10 minutes` during market hours: priority news refresh
- `15:35` after-market analysis
- Every `30 minutes` after market until evening: after-market news refresh
- `17:15` daily fundamentals refresh
- `18:05` daily report generation
- `18:20` daily local backup
- `Sunday 23:00` weekly retraining

## Manual Ops Commands

Run a daily report manually:

```powershell
docker exec trading-bot-backend python -m backend.scripts.run_daily_report
```

Run a backup manually:

```powershell
docker exec trading-bot-backend python -m backend.scripts.run_daily_backup
```

Dispatch queued phone alerts manually:

```powershell
docker exec trading-bot-backend python -m backend.scripts.run_alert_dispatch

## Testing

Create a local test environment and install dev requirements:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Run the test suite:

```powershell
.\run-tests.ps1
```

The script prefers the running Docker backend container, so tests use the same Python `3.11` runtime as the bot.  
If Docker is not running, it falls back to the local `.venv`.
```

## Local Data and Artifacts

- Docker volume data holds live PostgreSQL, InfluxDB, and Redis state
- Daily reports are written under `logs/reports`
- Daily application backups are written under `logs/backups`

## Notes

- Backtests use walk-forward windows only.
- No future data is used in signal generation or news joins.
- Transaction costs are applied to backtests and paper trades.
- Price data is live; fundamentals are refreshed daily; news is near-real-time.
- The frontend WebSocket reconnects and keeps the dashboard live from the backend snapshot stream.
