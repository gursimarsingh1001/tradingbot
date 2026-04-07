from __future__ import annotations

from statistics import median

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.backtest.backtester import start_backtest_thread
from backend.backtest.strategy_selector import StrategySelector
from backend.config import to_camel
from backend.db.postgres import BacktestTrade, StockStrategyMap, get_config_value, get_db


router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class StockBacktestRow(CamelModel):
    symbol: str
    best_strategy: str | None
    composite_score: float | None
    sharpe_ratio: float | None
    win_rate: float | None
    max_drawdown: float | None
    total_return: float | None


class StrategyComparison(CamelModel):
    strategy_name: str
    avg_sharpe_ratio: float


class BacktestSummary(CamelModel):
    global_best_strategy: str | None
    global_best_strategy_stock_count: int
    median_sharpe_ratio: float
    stocks: list[StockBacktestRow]
    strategy_comparison: list[StrategyComparison]
    progress: dict | None


class StockStrategyMetric(CamelModel):
    strategy_name: str
    trades: int
    total_return: float
    win_rate: float
    avg_pnl_pct: float


class BacktestStockDetail(CamelModel):
    symbol: str
    strategies: list[StockStrategyMetric]
    walk_forward_curve: list[dict[str, float | str]]


def _compute_trade_metrics(trades: list[BacktestTrade]) -> dict[str, float]:
    if not trades:
        return {
            "composite_score": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "total_return": 0.0,
        }

    returns = [float((trade.pnl_pct or 0.0) / 100.0) for trade in trades]
    avg_return = sum(returns) / len(returns)
    variance = sum((value - avg_return) ** 2 for value in returns) / len(returns) if returns else 0.0
    std = variance ** 0.5
    sharpe_ratio = (avg_return / std) * (252 ** 0.5) if std else 0.0
    win_rate = sum(1 for value in returns if value > 0) / len(returns)

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = (equity - peak) / peak if peak else 0.0
        max_drawdown = min(max_drawdown, drawdown)
    total_return = equity - 1

    composite_score = (
        0.40 * sharpe_ratio
        + 0.30 * win_rate
        + 0.20 * (1 - abs(max_drawdown))
        + 0.10 * total_return
    )
    return {
        "composite_score": float(composite_score),
        "sharpe_ratio": float(sharpe_ratio),
        "win_rate": float(win_rate),
        "max_drawdown": float(max_drawdown),
        "total_return": float(total_return),
    }


def _build_summary_from_trades(trades: list[BacktestTrade]) -> tuple[list[StockBacktestRow], list[StrategyComparison], float]:
    grouped: dict[tuple[str, str], list[BacktestTrade]] = {}
    for trade in trades:
        grouped.setdefault((trade.stock_symbol, trade.strategy_name), []).append(trade)

    per_strategy_rows: list[dict[str, float | str]] = []
    for (symbol, strategy_name), group in grouped.items():
        metrics = _compute_trade_metrics(group)
        per_strategy_rows.append(
            {
                "symbol": symbol,
                "best_strategy": strategy_name,
                "trade_count": float(len(group)),
                "composite_score": metrics["composite_score"],
                "sharpe_ratio": metrics["sharpe_ratio"],
                "win_rate": metrics["win_rate"],
                "max_drawdown": metrics["max_drawdown"],
                "total_return": metrics["total_return"],
            }
        )

    best_rows: list[StockBacktestRow] = []
    strategy_sharpes: dict[str, list[float]] = {}
    by_symbol: dict[str, list[dict[str, float | str]]] = {}
    for row in per_strategy_rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)

    for symbol, rows in by_symbol.items():
        eligible = [row for row in rows if float(row["trade_count"]) >= StrategySelector.MIN_MEANINGFUL_TRADES]
        if not eligible:
            best_rows.append(
                StockBacktestRow(
                    symbol=symbol,
                    best_strategy=None,
                    composite_score=None,
                    sharpe_ratio=None,
                    win_rate=None,
                    max_drawdown=None,
                    total_return=None,
                )
            )
            continue
        for row in eligible:
            strategy_sharpes.setdefault(str(row["best_strategy"]), []).append(float(row["sharpe_ratio"]))
        best = sorted(eligible, key=lambda item: float(item["composite_score"]), reverse=True)[0]
        best_rows.append(
            StockBacktestRow(
                symbol=symbol,
                best_strategy=str(best["best_strategy"]),
                composite_score=float(best["composite_score"]),
                sharpe_ratio=float(best["sharpe_ratio"]),
                win_rate=float(best["win_rate"]),
                max_drawdown=float(best["max_drawdown"]),
                total_return=float(best["total_return"]),
            )
        )
    best_rows.sort(key=lambda item: (item.composite_score if item.composite_score is not None else float("-inf")), reverse=True)

    strategy_comparison = [
        StrategyComparison(
            strategy_name=name,
            avg_sharpe_ratio=(sum(values) / len(values)) if values else 0.0,
        )
        for name, values in strategy_sharpes.items()
    ]
    strategy_comparison.sort(key=lambda item: item.avg_sharpe_ratio, reverse=True)

    median_sharpe = float(median([row.sharpe_ratio for row in best_rows if row.sharpe_ratio is not None])) if best_rows else 0.0
    return best_rows, strategy_comparison, median_sharpe


@router.get("/summary", response_model=BacktestSummary)
def get_backtest_summary(db: Session = Depends(get_db)) -> BacktestSummary:
    rows = db.scalars(select(StockStrategyMap).order_by(StockStrategyMap.composite_score.desc().nullslast())).all()
    global_best = get_config_value(db, "global_best_strategy", {"name": None})
    trades = db.scalars(select(BacktestTrade)).all()
    if trades:
        stock_rows, comparison, median_sharpe = _build_summary_from_trades(trades)
    else:
        stock_rows = [StockBacktestRow.model_validate(row) for row in rows]
        comparison = []
        median_sharpe = 0.0

    strategy_counts: dict[str, int] = {}
    for row in stock_rows:
        if row.best_strategy:
            strategy_counts[row.best_strategy] = strategy_counts.get(row.best_strategy, 0) + 1

    global_best_name = global_best.get("name")
    if not global_best_name and comparison:
        global_best_name = comparison[0].strategy_name
    return BacktestSummary(
        global_best_strategy=global_best_name,
        global_best_strategy_stock_count=int(strategy_counts.get(global_best_name, 0)),
        median_sharpe_ratio=median_sharpe,
        stocks=stock_rows,
        strategy_comparison=comparison,
        progress=get_config_value(db, "backtest_progress", None),
    )


@router.get("/stock/{symbol}", response_model=BacktestStockDetail)
def get_backtest_stock_detail(symbol: str, db: Session = Depends(get_db)) -> BacktestStockDetail:
    trades = db.scalars(
        select(BacktestTrade).where(BacktestTrade.stock_symbol == symbol).order_by(BacktestTrade.entry_date.asc())
    ).all()
    if not trades:
        raise HTTPException(status_code=404, detail="Backtest results not found for symbol")
    metrics = []
    walk_forward_curve = []
    cumulative = 1.0
    grouped: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        grouped.setdefault(trade.strategy_name, []).append(trade)
    for strategy_name, rows in grouped.items():
        pnls = [row.pnl_pct or 0.0 for row in rows]
        cumulative = 1.0
        for pnl in pnls:
            cumulative *= 1 + (pnl / 100.0)
        metrics.append(
            StockStrategyMetric(
                strategy_name=strategy_name,
                trades=len(rows),
                total_return=float((cumulative - 1.0) * 100.0),
                win_rate=float(sum(1 for pnl in pnls if pnl > 0) / len(pnls)),
                avg_pnl_pct=float(sum(pnls) / len(pnls)),
            )
        )
    metrics.sort(key=lambda item: item.total_return, reverse=True)
    for trade in trades:
        cumulative *= 1 + (trade.pnl_pct or 0.0) / 100
        walk_forward_curve.append({"date": trade.exit_date.isoformat(), "equity": cumulative})
    return BacktestStockDetail(symbol=symbol, strategies=metrics, walk_forward_curve=walk_forward_curve)


@router.post("/run", response_model=dict[str, str | int])
def run_backtest(limit: int = Query(default=0, ge=0, le=10000)) -> dict[str, str | int]:
    effective_limit = None if limit == 0 else limit
    started = start_backtest_thread(limit=effective_limit)
    return {
        "status": "started" if started else "already_running",
        "limit": limit,
    }


@router.get("/progress", response_model=dict)
def get_backtest_progress(db: Session = Depends(get_db)) -> dict:
    return get_config_value(db, "backtest_progress", {"active": False, "progress": 0, "message": "Idle"})
