from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.config import get_settings
from backend.db.postgres import (
    BacktestTrade,
    BotConfig,
    MistakeLog,
    Notification,
    PaperTrade,
    StockFundamentalSnapshot,
    StockStrategyMap,
    TomorrowWatchlist,
    session_scope,
)


settings = get_settings()


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _serialize_model(instance: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for attribute in instance.__mapper__.column_attrs:
        column = attribute.columns[0]
        payload[column.name] = _serialize_value(getattr(instance, attribute.key))
    return payload


@dataclass(slots=True)
class BackupResult:
    created: bool
    backup_dir: str
    tables: dict[str, int]
    retained_days: int
    skipped: bool = False


class BackupService:
    BACKUP_STATE_KEY = "backup_state"

    def __init__(self) -> None:
        self.backups_dir = Path(settings.backups_dir)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def _state(self) -> dict[str, Any]:
        with session_scope() as session:
            record = session.get(BotConfig, self.BACKUP_STATE_KEY)
            if record is None:
                return {
                    "lastRunAt": None,
                    "lastBackupDate": None,
                    "lastBackupDir": None,
                    "retainedDays": settings.backup_retention_days,
                }
            return dict(record.value or {})

    def _store_state(self, payload: dict[str, Any]) -> None:
        with session_scope() as session:
            record = session.get(BotConfig, self.BACKUP_STATE_KEY)
            if record is None:
                session.add(BotConfig(key=self.BACKUP_STATE_KEY, value=payload))
            else:
                record.value = payload

    def _copy_if_present(self, source: Path, destination: Path) -> None:
        if source.exists():
            destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _cleanup_old_backups(self, *, today: date) -> None:
        cutoff = today - timedelta(days=max(settings.backup_retention_days, 1))
        for backup_dir in self.backups_dir.iterdir():
            if not backup_dir.is_dir():
                continue
            try:
                folder_day = date.fromisoformat(backup_dir.name[:10])
            except ValueError:
                continue
            if folder_day < cutoff:
                shutil.rmtree(backup_dir, ignore_errors=True)

    def create_daily_backup(self, *, force: bool = False) -> BackupResult:
        today = datetime.now(tz=settings.tzinfo).date()
        state = self._state()
        if not force and state.get("lastBackupDate") == today.isoformat():
            return BackupResult(
                created=False,
                backup_dir=str(state.get("lastBackupDir") or ""),
                tables={},
                retained_days=int(state.get("retainedDays") or settings.backup_retention_days),
                skipped=True,
            )

        timestamp = datetime.now(tz=settings.tzinfo).strftime("%Y-%m-%d_%H-%M-%S")
        backup_dir = self.backups_dir / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)

        with session_scope() as session:
            paper_trades = session.scalars(select(PaperTrade).order_by(PaperTrade.created_at.desc())).all()
            strategy_map = session.scalars(select(StockStrategyMap).order_by(StockStrategyMap.symbol.asc())).all()
            watchlist = session.scalars(select(TomorrowWatchlist).order_by(TomorrowWatchlist.created_at.desc())).all()
            mistakes = session.scalars(select(MistakeLog).order_by(MistakeLog.created_at.desc())).all()
            notifications = session.scalars(select(Notification).order_by(Notification.created_at.desc()).limit(1000)).all()
            configs = session.scalars(select(BotConfig).order_by(BotConfig.key.asc())).all()
            fundamentals = session.scalars(
                select(StockFundamentalSnapshot).order_by(
                    StockFundamentalSnapshot.as_of_date.desc(),
                    StockFundamentalSnapshot.symbol.asc(),
                )
            ).all()
            backtest_overview = session.scalars(
                select(BacktestTrade).order_by(BacktestTrade.created_at.desc()).limit(5000)
            ).all()

        datasets = {
            "paper_trades.json": [_serialize_model(row) for row in paper_trades],
            "stock_strategy_map.json": [_serialize_model(row) for row in strategy_map],
            "tomorrow_watchlist.json": [_serialize_model(row) for row in watchlist],
            "mistakes_log.json": [_serialize_model(row) for row in mistakes],
            "notifications_recent.json": [_serialize_model(row) for row in notifications],
            "bot_config.json": [_serialize_model(row) for row in configs],
            "fundamental_snapshots.json": [_serialize_model(row) for row in fundamentals],
            "backtest_trades_recent.json": [_serialize_model(row) for row in backtest_overview],
        }

        table_counts = {name.replace(".json", ""): len(items) for name, items in datasets.items()}
        for filename, payload in datasets.items():
            (backup_dir / filename).write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )

        self._copy_if_present(Path(settings.symbols_config_path), backup_dir / "symbols_config.json")
        self._copy_if_present(Path(settings.fundamentals_config_path), backup_dir / "fundamentals_config.json")
        self._copy_if_present(Path(settings.market_holidays_path), backup_dir / "market_holidays.json")

        manifest = {
            "generatedAt": datetime.now(tz=settings.tzinfo).isoformat(),
            "backupDir": str(backup_dir),
            "tables": table_counts,
            "retentionDays": settings.backup_retention_days,
            "notes": [
                "This backup contains critical application state and recent history exports.",
                "Docker volumes still remain the source of truth for Postgres, Redis, and InfluxDB.",
            ],
        }
        (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")

        self._cleanup_old_backups(today=today)
        state = {
            "lastRunAt": datetime.now(tz=settings.tzinfo).isoformat(),
            "lastBackupDate": today.isoformat(),
            "lastBackupDir": str(backup_dir),
            "retainedDays": settings.backup_retention_days,
        }
        self._store_state(state)
        return BackupResult(
            created=True,
            backup_dir=str(backup_dir),
            tables=table_counts,
            retained_days=settings.backup_retention_days,
        )
