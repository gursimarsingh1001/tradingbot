from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select

from backend.config import get_settings
from backend.db.postgres import (
    BotConfig,
    Notification,
    PaperTrade,
    StockStrategyMap,
    TomorrowWatchlist,
    get_config_value,
    session_scope,
)


settings = get_settings()


class DailyReportService:
    REPORT_STATE_KEY = "daily_report_state"

    def __init__(self) -> None:
        self.reports_dir = Path(settings.reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def _state(self) -> dict[str, Any]:
        with session_scope() as session:
            record = session.get(BotConfig, self.REPORT_STATE_KEY)
            if record is None:
                return {
                    "lastGeneratedDate": None,
                    "lastReportPath": None,
                    "lastSummary": None,
                }
            return dict(record.value or {})

    def _store_state(self, payload: dict[str, Any]) -> None:
        with session_scope() as session:
            record = session.get(BotConfig, self.REPORT_STATE_KEY)
            if record is None:
                session.add(BotConfig(key=self.REPORT_STATE_KEY, value=payload))
            else:
                record.value = payload

    @staticmethod
    def _sum_pnl(trades: list[PaperTrade]) -> float:
        return round(sum(float(trade.pnl_rupees or 0.0) for trade in trades), 2)

    def generate_daily_report(self, *, force: bool = False) -> dict[str, Any]:
        today = datetime.now(tz=settings.tzinfo).date()
        state = self._state()
        report_path = self.reports_dir / f"{today.isoformat()}-daily-report.md"
        if not force and state.get("lastGeneratedDate") == today.isoformat() and report_path.exists():
            return {
                "generated": False,
                "path": str(report_path),
                "summary": state.get("lastSummary"),
            }

        with session_scope() as session:
            today_trades = session.scalars(
                select(PaperTrade).where(
                    or_(
                        PaperTrade.entry_date == today,
                        PaperTrade.exit_date == today,
                    )
                )
            ).all()
            open_trades = session.scalars(
                select(PaperTrade).where(PaperTrade.exit_date.is_(None))
            ).all()
            watchlist_count = session.scalar(
                select(func.count()).select_from(TomorrowWatchlist).where(TomorrowWatchlist.created_date >= today)
            ) or 0
            open_investment = [trade for trade in open_trades if (trade.signal_type or "").upper() == "INVESTMENT"]
            open_intraday = [trade for trade in open_trades if (trade.signal_type or "").upper() == "INTRADAY"]
            closed_today = [trade for trade in today_trades if trade.exit_date == today]
            wins = [trade for trade in closed_today if float(trade.pnl_rupees or 0.0) > 0]
            losses = [trade for trade in closed_today if float(trade.pnl_rupees or 0.0) <= 0]
            best_trade = max(closed_today, key=lambda trade: float(trade.pnl_rupees or 0.0), default=None)
            worst_trade = min(closed_today, key=lambda trade: float(trade.pnl_rupees or 0.0), default=None)
            notifications_count = session.scalar(
                select(func.count()).select_from(Notification).where(func.date(Notification.created_at) == today)
            ) or 0
            best_strategy_name = get_config_value(session, "global_best_strategy", {}).get("name")
            mapped_stock_count = session.scalar(select(func.count()).select_from(StockStrategyMap)) or 0

        summary = {
            "closedTrades": len(closed_today),
            "wins": len(wins),
            "losses": len(losses),
            "closedPnl": self._sum_pnl(closed_today),
            "openIntraday": len(open_intraday),
            "openInvestment": len(open_investment),
            "watchlistCount": int(watchlist_count),
            "notificationsCount": int(notifications_count),
            "mappedStocks": int(mapped_stock_count),
            "globalBestStrategy": best_strategy_name,
        }

        lines = [
            f"# Trading Bot Daily Report - {today.isoformat()}",
            "",
            "## Summary",
            f"- Closed trades today: {summary['closedTrades']}",
            f"- Wins / Losses: {summary['wins']} / {summary['losses']}",
            f"- Closed P&L: Rs {summary['closedPnl']:.2f}",
            f"- Open intraday trades: {summary['openIntraday']}",
            f"- Open investment trades: {summary['openInvestment']}",
            f"- Tomorrow watchlist rows: {summary['watchlistCount']}",
            f"- Notifications created today: {summary['notificationsCount']}",
            f"- Stocks mapped in strategy table: {summary['mappedStocks']}",
            f"- Current global best strategy: {summary['globalBestStrategy'] or 'N/A'}",
            "",
            "## Best / Worst Trade",
            (
                f"- Best trade: {best_trade.stock_symbol} ({best_trade.strategy_name}) "
                f"Rs {float(best_trade.pnl_rupees or 0.0):.2f}"
                if best_trade is not None
                else "- Best trade: N/A"
            ),
            (
                f"- Worst trade: {worst_trade.stock_symbol} ({worst_trade.strategy_name}) "
                f"Rs {float(worst_trade.pnl_rupees or 0.0):.2f}"
                if worst_trade is not None
                else "- Worst trade: N/A"
            ),
            "",
            "## Open Positions",
        ]

        if open_trades:
            for trade in open_trades[:25]:
                lines.append(
                    f"- {trade.stock_symbol} | {trade.signal_type} | {trade.strategy_name} | "
                    f"current Rs {float(trade.current_price or trade.entry_price or 0.0):.2f} | "
                    f"P&L Rs {float(trade.pnl_rupees or 0.0):.2f}"
                )
        else:
            lines.append("- No open trades.")

        lines.extend(["", "## Closed Trades Today"])
        if closed_today:
            for trade in closed_today[:50]:
                lines.append(
                    f"- {trade.stock_symbol} | {trade.signal_type} | {trade.strategy_name} | "
                    f"{trade.exit_reason or 'CLOSED'} | P&L Rs {float(trade.pnl_rupees or 0.0):.2f}"
                )
        else:
            lines.append("- No trades closed today.")

        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        state = {
            "lastGeneratedDate": today.isoformat(),
            "lastReportPath": str(report_path),
            "lastSummary": summary,
        }
        self._store_state(state)

        return {"generated": True, "path": str(report_path), "summary": summary}
