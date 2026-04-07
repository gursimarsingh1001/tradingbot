from __future__ import annotations

import argparse
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import delete, select, text

from backend.config import get_settings
from backend.db.postgres import BacktestTrade, Notification, PaperTrade, TomorrowWatchlist, session_scope
from backend.engine.kill_switch import KillSwitch
from backend.engine.market_calendar import get_market_calendar
from backend.engine.paper_trader_v2 import PaperTrader


settings = get_settings()
calendar = get_market_calendar()

PDF_BENCHMARKS = {
    "Bollinger Band Squeeze": {"min_avg_pnl_pct": 3.5, "min_win_rate": 0.35},
    "EMA Crossover": {"min_avg_pnl_pct": 2.5, "min_win_rate": 0.35},
    "Golden Cross": {"min_avg_pnl_pct": 2.0, "min_win_rate": 0.40},
    "MACD Momentum": {"min_avg_pnl_pct": 1.5, "min_win_rate": 0.30},
    "RSI Mean Reversion": {"min_avg_pnl_pct": 1.2, "min_win_rate": 0.32},
    "Combined Regime-Aware": {"min_avg_pnl_pct": 0.7, "min_win_rate": 0.28},
}


def _now() -> datetime:
    return datetime.now(tz=settings.tzinfo)


def _trade_status(trade: PaperTrade) -> str:
    metadata = dict(trade.metadata_json or {})
    if metadata.get("plan_only"):
        return str(metadata.get("plan_status") or "PLANNED").upper()
    if trade.exit_date is None:
        return "OPEN"
    return "WIN" if bool(trade.was_profitable) else "LOSS"


def _is_plan_only(trade: PaperTrade) -> bool:
    return bool((trade.metadata_json or {}).get("plan_only"))


def _closed_trade_day(trade: PaperTrade) -> date | None:
    return trade.exit_date or trade.entry_date


def _safe_avg(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _fmt_pct(value: float | None, *, signed: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"{value:+.3f}%" if signed else f"{value:.3f}%"


def _fmt_ratio(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"{value:.3f}"


def _fmt_money(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"Rs {value:,.2f}"


def _render_table(headers: list[str], rows: list[list[Any]]) -> str:
    normalized = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in normalized:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    header_line = " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    divider = "-+-".join("-" * width for width in widths)
    body = [" | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in normalized]
    return "\n".join([header_line, divider, *body]) if body else "\n".join([header_line, divider, "(no rows)"])


def _recent_trading_days(limit: int) -> list[date]:
    days: list[date] = []
    cursor = _now().date()
    while len(days) < limit:
        if calendar.is_trading_day(cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def _paper_trade_openings_summary(trades: list[PaperTrade], window_start: datetime) -> list[list[Any]]:
    grouped: dict[date, dict[str, int]] = {}
    for trade in trades:
        if trade.created_at is None or trade.created_at < window_start:
            continue
        day = trade.created_at.astimezone(settings.tzinfo).date()
        bucket = grouped.setdefault(day, {"total": 0, "intraday": 0, "investment": 0})
        bucket["total"] += 1
        if (trade.signal_type or "").upper() == "INVESTMENT":
            bucket["investment"] += 1
        else:
            bucket["intraday"] += 1
    rows: list[list[Any]] = []
    for day in sorted(grouped.keys(), reverse=True):
        bucket = grouped[day]
        rows.append([day.isoformat(), bucket["total"], bucket["intraday"], bucket["investment"]])
    return rows


def _daily_report() -> str:
    now = _now()
    window_start = now - timedelta(days=1)
    with session_scope() as session:
        trades = session.scalars(select(PaperTrade).order_by(PaperTrade.created_at.asc())).all()

    openings_rows = _paper_trade_openings_summary(trades, window_start)
    today = now.date()
    today_trades = [
        trade
        for trade in trades
        if (
            trade.entry_date == today
            or trade.exit_date == today
            or ((trade.signal_type or "").upper() == "INVESTMENT" and trade.exit_date is None)
        )
    ]
    open_trades = [trade for trade in today_trades if _trade_status(trade) == "OPEN"]
    planned_trades = [trade for trade in today_trades if _is_plan_only(trade)]
    closed_trades = [trade for trade in today_trades if trade.exit_date == today]
    recent_days = _recent_trading_days(5)
    trade_open_days = {
        trade.created_at.astimezone(settings.tzinfo).date()
        for trade in trades
        if trade.created_at is not None and trade.created_at >= now - timedelta(days=10)
    }
    zero_streak = 0
    for day in reversed(recent_days):
        if day in trade_open_days:
            break
        zero_streak += 1

    lines = [
        "# Phase 2 Daily Check",
        "",
        "## Paper Trades Opened (Last 1 Day)",
        _render_table(["Day", "Trades Opened", "Intraday", "Investment"], openings_rows),
        "",
        "## Current State",
        f"- Open trades: {len(open_trades)}",
        f"- Planned trades: {len(planned_trades)}",
        f"- Closed today: {len(closed_trades)}",
        f"- Consecutive trading days with zero new trades: {zero_streak}",
    ]
    if zero_streak >= 2:
        lines.append("- Warning: zero trades for 2+ consecutive trading days. Check auth, scheduler, or signal generation.")
    return "\n".join(lines) + "\n"


def _backtest_strategy_metrics(trades: list[BacktestTrade]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[BacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.strategy_name].append(trade)
    metrics: dict[str, dict[str, float]] = {}
    for strategy_name, strategy_trades in grouped.items():
        pnl_values = [float(trade.pnl_pct or 0.0) for trade in strategy_trades]
        win_rate = sum(1 for value in pnl_values if value > 0) / len(pnl_values) if pnl_values else 0.0
        metrics[strategy_name] = {
            "avg_pnl_pct": _safe_avg(pnl_values) or 0.0,
            "win_rate": win_rate,
        }
    return metrics


def _weekly_pnl_rows(closed_trades: list[PaperTrade], start_day: date) -> list[list[Any]]:
    grouped: dict[date, list[PaperTrade]] = defaultdict(list)
    for trade in closed_trades:
        if trade.exit_date and trade.exit_date >= start_day:
            grouped[trade.exit_date].append(trade)
    rows: list[list[Any]] = []
    for day in sorted(grouped.keys()):
        day_trades = grouped[day]
        wins = sum(1 for trade in day_trades if float(trade.pnl_pct or 0.0) > 0)
        losses = len(day_trades) - wins
        avg_pnl = _safe_avg([float(trade.pnl_pct or 0.0) for trade in day_trades])
        total_pnl = sum(float(trade.pnl_rupees or 0.0) for trade in day_trades)
        rows.append([day.isoformat(), len(day_trades), wins, losses, _fmt_pct(avg_pnl, signed=True), _fmt_money(total_pnl)])
    return rows


def _strategy_live_rows(closed_trades: list[PaperTrade], backtest_metrics: dict[str, dict[str, float]], start_day: date) -> tuple[list[list[Any]], dict[str, dict[str, float]]]:
    grouped: dict[str, list[PaperTrade]] = defaultdict(list)
    for trade in closed_trades:
        if trade.exit_date and trade.exit_date >= start_day:
            grouped[str(trade.strategy_name or "UNKNOWN")].append(trade)

    live_metrics: dict[str, dict[str, float]] = {}
    rows: list[list[Any]] = []
    for strategy_name, strategy_trades in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True):
        pnl_values = [float(trade.pnl_pct or 0.0) for trade in strategy_trades]
        live_avg = _safe_avg(pnl_values) or 0.0
        live_win = sum(1 for value in pnl_values if value > 0) / len(pnl_values) if pnl_values else 0.0
        backtest_avg = backtest_metrics.get(strategy_name, {}).get("avg_pnl_pct")
        backtest_win = backtest_metrics.get(strategy_name, {}).get("win_rate")
        benchmark = PDF_BENCHMARKS.get(strategy_name)
        meets = "N/A"
        if benchmark is not None:
            meets = "PASS" if (live_avg >= benchmark["min_avg_pnl_pct"] and live_win >= benchmark["min_win_rate"]) else "FAIL"
        rows.append(
            [
                strategy_name,
                len(strategy_trades),
                _fmt_pct(live_avg, signed=True),
                _fmt_ratio(live_win),
                _fmt_pct(backtest_avg, signed=True),
                _fmt_ratio(backtest_win),
                meets,
            ]
        )
        live_metrics[strategy_name] = {"avg_pnl_pct": live_avg, "win_rate": live_win, "trades": len(strategy_trades)}
    return rows, live_metrics


def _confidence_rows(closed_trades: list[PaperTrade]) -> list[list[Any]]:
    buckets = {
        "HIGH (85+)": [],
        "MEDIUM (70-84)": [],
        "LOW (<70)": [],
    }
    for trade in closed_trades:
        score = float(trade.confidence_score or 0.0)
        if score >= 85:
            buckets["HIGH (85+)"].append(float(trade.pnl_pct or 0.0))
        elif score >= 70:
            buckets["MEDIUM (70-84)"].append(float(trade.pnl_pct or 0.0))
        else:
            buckets["LOW (<70)"].append(float(trade.pnl_pct or 0.0))
    rows: list[list[Any]] = []
    for bucket_name, values in buckets.items():
        rows.append([bucket_name, len(values), _fmt_pct(_safe_avg(values), signed=True)])
    return rows


def _regime_rows(closed_trades: list[PaperTrade]) -> list[list[Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in closed_trades:
        grouped[str(trade.regime_at_entry or "UNKNOWN")].append(float(trade.pnl_pct or 0.0))
    rows: list[list[Any]] = []
    for regime, values in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True):
        win_rate = sum(1 for value in values if value > 0) / len(values) if values else 0.0
        rows.append([regime, len(values), _fmt_pct(_safe_avg(values), signed=True), _fmt_ratio(win_rate)])
    return rows


def _compute_drawdown(closed_trades: list[PaperTrade]) -> float | None:
    if not closed_trades:
        return None
    ordered = sorted(
        closed_trades,
        key=lambda trade: (
            trade.exit_date or trade.entry_date or date.min,
            trade.created_at or datetime.min.replace(tzinfo=settings.tzinfo),
        ),
    )
    equity = float(settings.paper_portfolio_value)
    peak = equity
    max_drawdown = 0.0
    for trade in ordered:
        equity += float(trade.pnl_rupees or 0.0)
        peak = max(peak, equity)
        if peak > 0:
            drawdown = (equity - peak) / peak
            max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown


def _four_week_evaluation(
    closed_trades: list[PaperTrade],
    live_metrics: dict[str, dict[str, float]],
    backtest_metrics: dict[str, dict[str, float]],
    notifications: list[Notification],
) -> list[list[Any]]:
    cutoff = _now().date() - timedelta(days=28)
    eval_trades = [trade for trade in closed_trades if trade.exit_date and trade.exit_date >= cutoff]
    pnl_values = [float(trade.pnl_rupees or 0.0) for trade in eval_trades]
    gross_profit = sum(value for value in pnl_values if value > 0)
    gross_loss = abs(sum(value for value in pnl_values if value < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else None)
    win_rate = (sum(1 for value in pnl_values if value > 0) / len(pnl_values)) if pnl_values else None
    drawdown = _compute_drawdown(eval_trades)

    comparable_degradations: list[float] = []
    for strategy_name, live_metric in live_metrics.items():
        if int(live_metric.get("trades", 0)) < 3:
            continue
        backtest_avg = float(backtest_metrics.get(strategy_name, {}).get("avg_pnl_pct") or 0.0)
        live_avg = float(live_metric.get("avg_pnl_pct") or 0.0)
        if backtest_avg > 0:
            comparable_degradations.append(max((backtest_avg - live_avg) / backtest_avg, 0.0))
    degradation_gap = median(comparable_degradations) if comparable_degradations else None

    recent_notifications = [
        notification
        for notification in notifications
        if notification.created_at and notification.created_at.astimezone(settings.tzinfo).date() >= cutoff
    ]
    kill_switch_activations = sum(
        1
        for notification in recent_notifications
        if (notification.type or "").upper() == "KILL_SWITCH" and "triggered" in str(notification.title or "").lower()
    )
    trading_days = [
        cutoff + timedelta(days=offset)
        for offset in range((_now().date() - cutoff).days + 1)
        if calendar.is_trading_day(cutoff + timedelta(days=offset))
    ]
    market_prep_days = {
        notification.created_at.astimezone(settings.tzinfo).date()
        for notification in recent_notifications
        if (notification.type or "").upper() == "MARKET_PREP"
    }
    missed_sessions = max(len(trading_days) - len(market_prep_days), 0)

    golden_cross = [trade for trade in eval_trades if (trade.strategy_name or "") == "Golden Cross"]
    golden_cross_win_rate = (
        sum(1 for trade in golden_cross if float(trade.pnl_rupees or 0.0) > 0) / len(golden_cross)
        if golden_cross
        else None
    )
    intraday_by_strategy: dict[str, list[float]] = defaultdict(list)
    for trade in eval_trades:
        if (trade.signal_type or "").upper() == "INTRADAY":
            intraday_by_strategy[str(trade.strategy_name or "UNKNOWN")].append(float(trade.pnl_pct or 0.0))
    best_intraday_avg = max((_safe_avg(values) or float("-inf")) for values in intraday_by_strategy.values()) if intraday_by_strategy else None

    def _criterion(label: str, value: str, passed: bool | None) -> list[Any]:
        status = "PASS" if passed is True else "FAIL" if passed is False else "WATCH"
        return [label, value, status]

    rows = [
        _criterion("Overall Profit Factor", _fmt_ratio(profit_factor), None if profit_factor is None else profit_factor > 1.15 if profit_factor >= 1.0 else False),
        _criterion("Overall Win Rate", _fmt_ratio(win_rate), None if win_rate is None else win_rate > 0.30 if win_rate >= 0.25 else False),
        _criterion("Backtest vs Live Gap", _fmt_ratio(degradation_gap), None if degradation_gap is None else degradation_gap < 0.35 if degradation_gap <= 0.50 else False),
        _criterion("Max Drawdown", _fmt_ratio(drawdown), None if drawdown is None else drawdown > KillSwitch.PORTFOLIO_DD_LIMIT),
        _criterion("Kill Switch Activations", str(kill_switch_activations), kill_switch_activations <= 1),
        _criterion("System Uptime", f"missed {missed_sessions} of {len(trading_days)} sessions", missed_sessions < 3),
        _criterion("Golden Cross Win Rate", _fmt_ratio(golden_cross_win_rate), None if golden_cross_win_rate is None else golden_cross_win_rate > 0.50 if golden_cross_win_rate >= 0.35 else False),
        _criterion("Best Intraday Strategy Avg PnL", _fmt_pct(best_intraday_avg, signed=True), None if best_intraday_avg is None else best_intraday_avg > 1.5 if best_intraday_avg >= 0.5 else False),
    ]
    return rows


def _weekly_report() -> str:
    start_day = _now().date() - timedelta(days=7)
    with session_scope() as session:
        paper_trades = session.scalars(select(PaperTrade).order_by(PaperTrade.created_at.asc())).all()
        backtest_trades = session.scalars(select(BacktestTrade)).all()
        notifications = session.scalars(select(Notification).order_by(Notification.created_at.asc())).all()

    closed_trades = [trade for trade in paper_trades if not _is_plan_only(trade) and trade.exit_date is not None]
    weekly_rows = _weekly_pnl_rows(closed_trades, start_day)
    backtest_metrics = _backtest_strategy_metrics(backtest_trades)
    strategy_rows, live_metrics = _strategy_live_rows(closed_trades, backtest_metrics, start_day)
    confidence_rows = _confidence_rows(closed_trades)
    regime_rows = _regime_rows(closed_trades)
    evaluation_rows = _four_week_evaluation(closed_trades, live_metrics, backtest_metrics, notifications)

    lines = [
        "# Phase 2 Weekly Review",
        "",
        "## Weekly P&L Summary",
        _render_table(["Day", "Trades", "Wins", "Losses", "Avg PnL %", "Total PnL"], weekly_rows),
        "",
        "## Strategy Performance (Live vs Backtest)",
        _render_table(
            ["Strategy", "Trades", "Live Avg PnL %", "Live Win Rate", "Backtest Avg PnL %", "Backtest Win Rate", "PDF Threshold"],
            strategy_rows,
        ),
        "",
        "## Intelligence Engine Verification",
        _render_table(["Confidence Bucket", "Trades", "Avg PnL %"], confidence_rows),
        "",
        "## Regime Detection Accuracy",
        _render_table(["Regime", "Trades", "Avg PnL %", "Win Rate"], regime_rows),
        "",
        "## Four-Week Pass / Fail Snapshot",
        _render_table(["Metric", "Value", "Status"], evaluation_rows),
    ]
    return "\n".join(lines) + "\n"


def _soft_reset() -> str:
    now = _now()
    closed_open = 0
    cancelled_plans = 0
    with session_scope() as session:
        trades = session.scalars(select(PaperTrade)).all()
        for trade in trades:
            metadata = dict(trade.metadata_json or {})
            if metadata.get("plan_only"):
                if str(metadata.get("plan_status") or "").upper() != "CANCELLED":
                    metadata["plan_status"] = "CANCELLED"
                    trade.metadata_json = metadata
                    cancelled_plans += 1
                continue
            if trade.exit_date is not None:
                continue
            direction = str(metadata.get("direction") or "BUY").upper()
            exit_price = float(trade.current_price or trade.entry_price or 0.0)
            shares = int(trade.shares or 0)
            entry_price = float(trade.entry_price or 0.0)
            if direction == "SELL":
                gross_pnl = (entry_price - exit_price) * shares
            else:
                gross_pnl = (exit_price - entry_price) * shares
            exit_costs = PaperTrader._exit_costs(exit_price, shares, direction)
            remaining_entry_costs = float(metadata.get("remaining_entry_costs") or 0.0)
            pnl_rupees = gross_pnl - exit_costs - remaining_entry_costs
            notional = entry_price * max(shares, 1)
            trade.exit_price = exit_price
            trade.current_price = exit_price
            trade.exit_date = now.date()
            trade.exit_time = now.time().replace(microsecond=0)
            trade.exit_reason = "PORTFOLIO_RESET"
            trade.pnl_rupees = pnl_rupees
            trade.pnl_pct = (pnl_rupees / notional) * 100 if notional else 0.0
            trade.was_profitable = pnl_rupees > 0
            metadata["remaining_shares"] = 0
            trade.metadata_json = metadata
            closed_open += 1
        session.execute(delete(TomorrowWatchlist))
    return (
        "Soft reset complete:\n"
        f"- Open executed trades closed: {closed_open}\n"
        f"- Planned trades cancelled: {cancelled_plans}\n"
        "- Tomorrow watchlist cleared: yes\n"
    )


def _truncate_reset() -> str:
    with session_scope() as session:
        session.execute(text("TRUNCATE TABLE paper_trades RESTART IDENTITY CASCADE"))
        session.execute(text("TRUNCATE TABLE tomorrow_watchlist RESTART IDENTITY CASCADE"))
    return "Hard reset complete:\n- paper_trades truncated\n- tomorrow_watchlist truncated\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 paper-trading operations checks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("daily", help="Run daily paper-trading checks")
    subparsers.add_parser("weekly", help="Run weekly review and pass/fail snapshot")
    reset_parser = subparsers.add_parser("reset", help="Reset the paper portfolio")
    reset_parser.add_argument("--truncate", action="store_true", help="Truncate paper trades instead of soft-closing open positions")

    args = parser.parse_args()
    if args.command == "daily":
        print(_daily_report())
        return
    if args.command == "weekly":
        print(_weekly_report())
        return
    if args.command == "reset":
        print(_truncate_reset() if args.truncate else _soft_reset())


if __name__ == "__main__":
    main()
