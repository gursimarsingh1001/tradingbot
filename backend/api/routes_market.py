from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.config import to_camel
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.postgres import BacktestTrade, Notification, PaperTrade, StockStrategyMap, TomorrowWatchlist, get_db
from backend.db.redis_client import get_cache
from backend.engine.kill_switch import KillSwitch
from backend.engine.market_data_service import get_market_data_service


router = APIRouter(prefix="/api", tags=["market"])
historical_fetcher = HistoricalFetcher()


def _pivot_indexes(series, *, is_high: bool, window: int = 3) -> list[int]:
    indexes: list[int] = []
    if len(series) < (window * 2) + 1:
        return indexes
    for idx in range(window, len(series) - window):
        center = float(series.iloc[idx])
        sample = series.iloc[idx - window : idx + window + 1]
        if is_high and center >= float(sample.max()):
            indexes.append(idx)
        if not is_high and center <= float(sample.min()):
            indexes.append(idx)
    return indexes


def _trendline_annotation(frame, *, resistance: bool) -> WatchlistAnnotation | None:
    series = frame["High"] if resistance else frame["Low"]
    pivots = _pivot_indexes(series, is_high=resistance)
    if len(pivots) < 2:
        return None

    selected: tuple[int, int] | None = None
    for left, right in zip(reversed(pivots[:-1]), reversed(pivots[1:])):
        left_value = float(series.iloc[left])
        right_value = float(series.iloc[right])
        if resistance and right_value < left_value:
            selected = (left, right)
            break
        if not resistance and right_value > left_value:
            selected = (left, right)
            break
    if selected is None:
        selected = (pivots[-2], pivots[-1])

    start_idx, end_idx = selected
    start_value = float(series.iloc[start_idx])
    end_value = float(series.iloc[end_idx])
    slope = 0.0 if end_idx == start_idx else (end_value - start_value) / (end_idx - start_idx)

    points: list[WatchlistAnnotationPoint] = []
    for idx in range(start_idx, len(frame)):
        value = start_value + (slope * (idx - start_idx))
        points.append(WatchlistAnnotationPoint(date=frame.index[idx].date().isoformat(), value=round(value, 2)))

    breakout_price = points[-1].value if points else None
    return WatchlistAnnotation(
        kind="trendline",
        label="Resistance trendline" if resistance else "Support trendline",
        color="#ff8b5e" if resistance else "#4be1c3",
        points=points,
        breakout_price=breakout_price,
    )


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class IndexValue(CamelModel):
    value: float
    change: float
    change_pct: float
    label: str | None = None
    source: str | None = None
    updated_at: datetime | None = None
    status: str | None = None
    is_delayed: bool = False


class Recommendation(CamelModel):
    stock_symbol: str
    strategy_name: str
    signal_type: str
    direction: str | None = None
    confidence_score: float
    entry_zone_low: float | None
    entry_zone_high: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    target_3: float | None
    paper_trade_status: str
    pnl_rupees: float | None
    pnl_pct: float | None
    pattern_name: str | None
    regime_at_entry: str | None
    recommendation_reason: str | None
    basis_points: list[str] | None
    explanation_sections: dict[str, list[str]] | None = None
    product_type: str | None = None
    leverage_multiplier: float | None = None
    capital_blocked: float | None = None
    remaining_shares: int | None = None
    max_holding_days: int | None = None
    sector: str | None = None
    sector_score: float | None = None
    days_to_earnings: int | None = None
    event_flags: list[str] | None = None
    fundamental_quality_score: float | None = None
    fundamental_has_snapshot: bool | None = None
    fundamental_confidence: float | None = None
    financial_data_source: str | None = None


class WatchlistItem(CamelModel):
    id: int
    symbol: str | None
    reason: str | None
    watch_price: float | None
    signal_type: str | None
    strategy: str | None
    direction: str | None = None
    planned_trade_id: str | None = None
    plan_status: str | None = None
    planned_for_date: date | None = None
    recommendation_count_30d: int = 0
    worked_count_30d: int = 0
    win_rate_30d: float = 0.0
    confidence_score: float | None = None
    news_perspective: str | None = None
    news_score: float | None = None
    event_flags: list[str] | None = None
    basis_points: list[str] | None = None
    explanation_sections: dict[str, list[str]] | None = None
    sector: str | None = None
    sector_score: float | None = None
    fundamental_quality_score: float | None = None
    fundamental_has_snapshot: bool | None = None
    fundamental_confidence: float | None = None
    financial_data_source: str | None = None


class WatchlistChartPoint(CamelModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class WatchlistAnnotationPoint(CamelModel):
    date: str
    value: float


class WatchlistAnnotation(CamelModel):
    kind: str
    label: str
    color: str
    points: list[WatchlistAnnotationPoint] | None = None
    value: float | None = None
    breakout_price: float | None = None


class WatchlistDetail(CamelModel):
    symbol: str
    reason: str | None
    strategy: str | None
    signal_type: str | None
    direction: str | None = None
    watch_price: float | None
    current_price: float | None
    plan_status: str | None
    product_type: str | None
    leverage_multiplier: float | None
    capital_blocked: float | None
    max_holding_days: int | None
    support_level: float | None
    resistance_level: float | None
    confidence_score: float | None = None
    news_perspective: str | None = None
    news_score: float | None = None
    event_flags: list[str] | None = None
    basis_points: list[str] | None = None
    explanation_sections: dict[str, list[str]] | None = None
    sector: str | None = None
    sector_score: float | None = None
    fundamental_quality_score: float | None = None
    fundamental_has_snapshot: bool | None = None
    fundamental_confidence: float | None = None
    financial_data_source: str | None = None
    chart: list[WatchlistChartPoint]
    annotations: list[WatchlistAnnotation]


class KillSwitchState(CamelModel):
    active: bool
    reason: str | None


class RestartRequest(CamelModel):
    confirmed: bool


class NotificationModel(CamelModel):
    id: str
    type: str | None
    title: str | None
    body: str | None
    color: str | None
    is_read: bool
    related_stock: str | None
    created_at: datetime | None


def _is_active_watchlist_plan(trade: PaperTrade) -> bool:
    metadata = trade.metadata_json or {}
    return bool(
        trade.stock_symbol
        and trade.exit_date is None
        and metadata.get("plan_only")
        and metadata.get("opened_from") == "after_market_watchlist"
    )


def _get_upcoming_watchlist_plans(db: Session) -> list[PaperTrade]:
    today = date.today()
    future_trades = db.scalars(
        select(PaperTrade)
        .where(
            PaperTrade.exit_date.is_(None),
            PaperTrade.entry_date > today,
        )
        .order_by(PaperTrade.entry_date.asc(), PaperTrade.created_at.desc())
    ).all()

    active_plans = [trade for trade in future_trades if _is_active_watchlist_plan(trade)]
    if not active_plans:
        return []

    next_batch_date = min(trade.entry_date for trade in active_plans if trade.entry_date is not None)
    latest_by_symbol: dict[str, PaperTrade] = {}
    for trade in active_plans:
        if trade.entry_date != next_batch_date or not trade.stock_symbol:
            continue
        latest_by_symbol.setdefault(trade.stock_symbol, trade)
    return list(latest_by_symbol.values())


@router.get("/indices", response_model=dict[str, IndexValue])
def get_indices() -> dict[str, Any]:
    cache = get_cache()
    cached = cache.get_json("live:benchmarks", None)
    if cached:
        return cached
    return get_market_data_service().refresh_live_benchmarks(force=True).get(
        "indices",
        {
            "NIFTY50": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "BANKNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "SENSEX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "FINNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "GIFTNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "MCX_CRUDE": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "BRENT_CRUDE": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "USDINR": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
        },
    )


@router.get("/stocks/prices", response_model=list[dict[str, Any]])
def get_watchlist_prices() -> list[dict[str, Any]]:
    cache = get_cache()
    cached = cache.get_json("live:watchlist_prices", None)
    if cached is not None:
        return cached
    return get_market_data_service().refresh_market_cache(force=True).get("watchlist_prices", [])


@router.get("/recommendations/today", response_model=list[Recommendation])
def get_today_recommendations(db: Session = Depends(get_db)) -> list[Recommendation]:
    trades = db.scalars(
        select(PaperTrade)
        .where(
            or_(
                PaperTrade.entry_date == date.today(),
                and_(
                    PaperTrade.signal_type == "INVESTMENT",
                    PaperTrade.exit_date.is_(None),
                ),
            )
        )
        .order_by(PaperTrade.created_at.desc())
    ).all()
    items = []
    existing_intraday_symbols: set[str] = set()
    for trade in trades:
        metadata = trade.metadata_json or {}
        effective_signal_type = trade.signal_type or "INTRADAY"
        product_type = metadata.get("product_type") or ("INTRADAY_ROBO" if effective_signal_type == "INTRADAY" else "DELIVERY")
        leverage_multiplier = (
            float(metadata.get("leverage_multiplier"))
            if metadata.get("leverage_multiplier") is not None
            else (5.0 if effective_signal_type == "INTRADAY" else 1.0)
        )
        capital_blocked = (
            float(metadata.get("capital_blocked"))
            if metadata.get("capital_blocked") is not None
            else ((float(trade.entry_price or 0.0) * float(trade.shares or 0)) / max(leverage_multiplier, 1.0))
        )
        max_holding_days = (
            int(metadata.get("max_holding_days"))
            if metadata.get("max_holding_days") is not None
            else (0 if effective_signal_type == "INTRADAY" else 45)
        )
        paper_trade_status = (
            str(metadata.get("plan_status") or "PLANNED")
            if metadata.get("plan_only")
            else "OPEN" if trade.exit_date is None else "CLOSED"
        )
        items.append(
            Recommendation(
                stock_symbol=trade.stock_symbol or "",
                strategy_name=trade.strategy_name or "",
                signal_type=trade.signal_type or "",
                direction=str(metadata.get("direction") or "BUY"),
                confidence_score=float(trade.confidence_score or 0.0),
                entry_zone_low=trade.entry_zone_low,
                entry_zone_high=trade.entry_zone_high,
                stop_loss=trade.stop_loss,
                target_1=trade.target_1,
                target_2=trade.target_2,
                target_3=trade.target_3,
                paper_trade_status=paper_trade_status,
                pnl_rupees=trade.pnl_rupees,
                pnl_pct=trade.pnl_pct,
                pattern_name=trade.pattern_name,
                regime_at_entry=trade.regime_at_entry,
                recommendation_reason=metadata.get("recommendation_reason"),
                basis_points=metadata.get("basis_points"),
                explanation_sections=metadata.get("explanation_sections"),
                product_type=product_type,
                leverage_multiplier=leverage_multiplier,
                capital_blocked=capital_blocked,
                remaining_shares=int(metadata.get("remaining_shares")) if metadata.get("remaining_shares") is not None else trade.shares,
                max_holding_days=max_holding_days,
                sector=metadata.get("sector"),
                sector_score=float(metadata.get("sector_score")) if metadata.get("sector_score") is not None else None,
                days_to_earnings=int(metadata.get("days_to_earnings")) if metadata.get("days_to_earnings") is not None else None,
                event_flags=list(metadata.get("event_flags") or []),
                fundamental_quality_score=float(metadata.get("fundamental_quality_score")) if metadata.get("fundamental_quality_score") is not None else None,
                fundamental_has_snapshot=bool(metadata.get("fundamental_has_snapshot")) if metadata.get("fundamental_has_snapshot") is not None else None,
                fundamental_confidence=float(metadata.get("fundamental_confidence")) if metadata.get("fundamental_confidence") is not None else None,
                financial_data_source=metadata.get("financial_data_source"),
            )
        )
        if effective_signal_type == "INTRADAY" and trade.stock_symbol:
            existing_intraday_symbols.add(trade.stock_symbol.upper())

    cache = get_cache()
    live_signals = cache.get_json("live:active_signals", [])
    if isinstance(live_signals, list):
        for signal in live_signals:
            if not isinstance(signal, dict):
                continue
            stock_symbol = str(signal.get("stock_symbol") or "")
            if not stock_symbol or stock_symbol.upper() in existing_intraday_symbols:
                continue
            if str(signal.get("signal_type") or "INTRADAY").upper() != "INTRADAY":
                continue
            items.append(
                Recommendation(
                    stock_symbol=stock_symbol,
                    strategy_name=str(signal.get("strategy_name") or ""),
                    signal_type="INTRADAY",
                    direction=str(signal.get("signal") or signal.get("direction") or "BUY"),
                    confidence_score=float(signal.get("confidence_score") or 0.0),
                    entry_zone_low=float(signal.get("entry_zone_low")) if signal.get("entry_zone_low") is not None else None,
                    entry_zone_high=float(signal.get("entry_zone_high")) if signal.get("entry_zone_high") is not None else None,
                    stop_loss=float(signal.get("stop_loss")) if signal.get("stop_loss") is not None else None,
                    target_1=float(signal.get("target_1")) if signal.get("target_1") is not None else None,
                    target_2=float(signal.get("target_2")) if signal.get("target_2") is not None else None,
                    target_3=float(signal.get("target_3")) if signal.get("target_3") is not None else None,
                    paper_trade_status=str(signal.get("paper_trade_status") or "READY"),
                    pnl_rupees=None,
                    pnl_pct=None,
                    pattern_name=str(signal.get("pattern_name")) if signal.get("pattern_name") is not None else None,
                    regime_at_entry=str(signal.get("regime_at_entry")) if signal.get("regime_at_entry") is not None else None,
                    recommendation_reason=str(signal.get("recommendation_reason")) if signal.get("recommendation_reason") is not None else None,
                    basis_points=list(signal.get("basis_points") or []),
                    explanation_sections=dict(signal.get("explanation_sections") or {}),
                    product_type=str(signal.get("product_type")) if signal.get("product_type") is not None else "INTRADAY_ROBO",
                    leverage_multiplier=float(signal.get("leverage_multiplier")) if signal.get("leverage_multiplier") is not None else 5.0,
                    capital_blocked=float(signal.get("capital_blocked")) if signal.get("capital_blocked") is not None else None,
                    remaining_shares=int(signal.get("remaining_shares")) if signal.get("remaining_shares") is not None else None,
                    max_holding_days=int(signal.get("max_holding_days")) if signal.get("max_holding_days") is not None else 0,
                    sector=str(signal.get("sector")) if signal.get("sector") is not None else None,
                    sector_score=float(signal.get("sector_score")) if signal.get("sector_score") is not None else None,
                    days_to_earnings=int(signal.get("days_to_earnings")) if signal.get("days_to_earnings") is not None else None,
                    event_flags=list(signal.get("event_flags") or []),
                    fundamental_quality_score=float(signal.get("fundamental_quality_score")) if signal.get("fundamental_quality_score") is not None else None,
                    fundamental_has_snapshot=bool(signal.get("fundamental_has_snapshot")) if signal.get("fundamental_has_snapshot") is not None else None,
                    fundamental_confidence=float(signal.get("fundamental_confidence")) if signal.get("fundamental_confidence") is not None else None,
                    financial_data_source=str(signal.get("financial_data_source")) if signal.get("financial_data_source") is not None else None,
                )
            )
    return items


@router.get("/watchlist/tomorrow", response_model=list[WatchlistItem])
def get_tomorrow_watchlist(db: Session = Depends(get_db)) -> list[WatchlistItem]:
    plans = _get_upcoming_watchlist_plans(db)
    if not plans:
        return []

    symbols = [trade.stock_symbol for trade in plans if trade.stock_symbol]
    relevant_trades = db.scalars(
        select(PaperTrade).where(
            PaperTrade.stock_symbol.in_(symbols) if symbols else False,
        )
    ).all() if symbols else []
    strategy_rows = db.scalars(
        select(StockStrategyMap).where(StockStrategyMap.symbol.in_(symbols) if symbols else False)
    ).all() if symbols else []
    historical_backtests = db.scalars(
        select(BacktestTrade).where(BacktestTrade.stock_symbol.in_(symbols) if symbols else False)
    ).all() if symbols else []

    plan_map: dict[str, PaperTrade] = {trade.stock_symbol or "": trade for trade in plans if trade.stock_symbol}
    stats_map: dict[str, dict[str, float | int]] = {}
    backtest_stats_map: dict[str, dict[str, float | int]] = {}
    best_strategy_map = {row.symbol: row.best_strategy for row in strategy_rows if row.symbol}
    for trade in relevant_trades:
        metadata = trade.metadata_json or {}
        if _is_active_watchlist_plan(trade):
            continue
        if not trade.stock_symbol:
            continue
        bucket = stats_map.setdefault(
            trade.stock_symbol,
            {"total": 0, "worked": 0, "closed": 0},
        )
        bucket["total"] += 1
        if trade.exit_date is not None:
            bucket["closed"] += 1
        if trade.was_profitable:
            bucket["worked"] += 1
    for trade in historical_backtests:
        if not trade.stock_symbol:
            continue
        best_strategy = best_strategy_map.get(trade.stock_symbol)
        if best_strategy and trade.strategy_name != best_strategy:
            continue
        bucket = backtest_stats_map.setdefault(
            trade.stock_symbol,
            {"total": 0, "worked": 0, "closed": 0},
        )
        bucket["total"] += 1
        bucket["closed"] += 1
        if (trade.pnl_pct or 0) > 0:
            bucket["worked"] += 1

    response: list[WatchlistItem] = []
    for index, plan in enumerate(plans, start=1):
        symbol = plan.stock_symbol or ""
        metadata = plan.metadata_json or {}
        stats = stats_map.get(symbol, {"total": 0, "worked": 0, "closed": 0})
        if not int(stats["total"]):
            stats = backtest_stats_map.get(symbol, stats)
        total = int(stats["total"])
        worked = int(stats["worked"])
        win_rate = (worked / total) if total else 0.0
        response.append(
            WatchlistItem(
                id=index,
                symbol=symbol,
                reason=metadata.get("watchlist_reason") or metadata.get("recommendation_reason"),
                watch_price=plan.entry_price,
                signal_type=plan.signal_type,
                strategy=plan.strategy_name,
                direction=metadata.get("direction"),
                planned_trade_id=plan.trade_id,
                plan_status=metadata.get("plan_status"),
                planned_for_date=plan.entry_date,
                recommendation_count_30d=total,
                worked_count_30d=worked,
                win_rate_30d=win_rate,
                confidence_score=float(plan.confidence_score) if plan.confidence_score is not None else None,
                news_perspective=metadata.get("news_perspective"),
                news_score=float(plan.news_score_at_entry) if plan.news_score_at_entry is not None else None,
                event_flags=list(metadata.get("event_flags") or []),
                basis_points=list(metadata.get("basis_points") or []),
                explanation_sections=dict(metadata.get("explanation_sections") or {}),
                sector=metadata.get("sector"),
                sector_score=float(metadata.get("sector_score")) if metadata.get("sector_score") is not None else None,
                fundamental_quality_score=float(metadata.get("fundamental_quality_score")) if metadata.get("fundamental_quality_score") is not None else None,
                fundamental_has_snapshot=bool(metadata.get("fundamental_has_snapshot")) if metadata.get("fundamental_has_snapshot") is not None else None,
                fundamental_confidence=float(metadata.get("fundamental_confidence")) if metadata.get("fundamental_confidence") is not None else None,
                financial_data_source=metadata.get("financial_data_source"),
            )
        )
    return sorted(response, key=lambda item: (item.planned_for_date or date.max, item.symbol or ""))


@router.get("/watchlist/tomorrow/{symbol}", response_model=WatchlistDetail)
def get_watchlist_detail(symbol: str, db: Session = Depends(get_db)) -> WatchlistDetail:
    plan_map = {trade.stock_symbol or "": trade for trade in _get_upcoming_watchlist_plans(db) if trade.stock_symbol}
    plan = plan_map.get(symbol.upper())
    if plan is None:
        raise HTTPException(status_code=404, detail="Watchlist stock not found")

    symbol_config = historical_fetcher.load_symbol_map().get(symbol.upper())
    if symbol_config is None:
        raise HTTPException(status_code=404, detail="Symbol config not found")

    frame = historical_fetcher.fetch_symbol_frame(symbol_config).tail(90)
    if frame.empty:
        raise HTTPException(status_code=404, detail="No chart data available")

    metadata = dict(plan.metadata_json or {})
    effective_signal_type = plan.signal_type or "INTRADAY"
    product_type = metadata.get("product_type") or ("INTRADAY_ROBO" if effective_signal_type == "INTRADAY" else "DELIVERY")
    leverage_multiplier = (
        float(metadata.get("leverage_multiplier"))
        if metadata.get("leverage_multiplier") is not None
        else (5.0 if effective_signal_type == "INTRADAY" else 1.0)
    )
    capital_blocked = (
        float(metadata.get("capital_blocked"))
        if metadata.get("capital_blocked") is not None
        else ((float(plan.entry_price or 0.0) * float(plan.shares or 0)) / max(leverage_multiplier, 1.0))
        if plan is not None
        else None
    )
    max_holding_days = (
        int(metadata.get("max_holding_days"))
        if metadata.get("max_holding_days") is not None
        else (0 if effective_signal_type == "INTRADAY" else 45)
    )

    live_quotes = get_market_data_service().fetch_quotes_for_symbols([symbol.upper()])
    current_price = live_quotes.get(symbol.upper()) or float(frame["Close"].iloc[-1])
    latest = frame.iloc[-1]
    support_level = float(latest.get("Low_63") or frame["Low"].tail(63).min())
    resistance_level = float(latest.get("High_63") or frame["High"].tail(63).max())

    annotations: list[WatchlistAnnotation] = [
        WatchlistAnnotation(kind="horizontal", label="Watch trigger", color="#5aa6ff", value=plan.entry_price),
        WatchlistAnnotation(kind="horizontal", label="Support", color="#4be1c3", value=round(support_level, 2)),
        WatchlistAnnotation(kind="horizontal", label="Resistance", color="#ff8b5e", value=round(resistance_level, 2)),
    ]
    resistance_line = _trendline_annotation(frame, resistance=True)
    support_line = _trendline_annotation(frame, resistance=False)
    if resistance_line is not None:
        annotations.append(resistance_line)
    if support_line is not None:
        annotations.append(support_line)

    return WatchlistDetail(
        symbol=symbol.upper(),
        reason=metadata.get("watchlist_reason") or metadata.get("recommendation_reason"),
        strategy=plan.strategy_name,
        signal_type=effective_signal_type,
        direction=str(metadata.get("direction") or "BUY"),
        watch_price=plan.entry_price,
        current_price=current_price,
        plan_status=metadata.get("plan_status"),
        product_type=product_type,
        leverage_multiplier=leverage_multiplier,
        capital_blocked=capital_blocked,
        max_holding_days=max_holding_days,
        support_level=round(support_level, 2),
        resistance_level=round(resistance_level, 2),
        confidence_score=float(plan.confidence_score) if plan and plan.confidence_score is not None else None,
        news_perspective=metadata.get("news_perspective"),
        news_score=float(plan.news_score_at_entry) if plan and plan.news_score_at_entry is not None else None,
        event_flags=list(metadata.get("event_flags") or []),
        basis_points=list(metadata.get("basis_points") or []),
        explanation_sections=dict(metadata.get("explanation_sections") or {}),
        sector=metadata.get("sector"),
        sector_score=float(metadata.get("sector_score")) if metadata.get("sector_score") is not None else None,
        fundamental_quality_score=(
            float(metadata.get("fundamental_quality_score"))
            if metadata.get("fundamental_quality_score") is not None
            else None
        ),
        fundamental_has_snapshot=bool(metadata.get("fundamental_has_snapshot")) if metadata.get("fundamental_has_snapshot") is not None else None,
        fundamental_confidence=float(metadata.get("fundamental_confidence")) if metadata.get("fundamental_confidence") is not None else None,
        financial_data_source=metadata.get("financial_data_source"),
        chart=[
            WatchlistChartPoint(
                date=index.date().isoformat(),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
            )
            for index, row in frame.iterrows()
        ],
        annotations=annotations,
    )


@router.get("/kill-switch/status", response_model=KillSwitchState)
def get_kill_switch_status() -> KillSwitchState:
    state = KillSwitch().current_state()
    return KillSwitchState(active=bool(state.get("active")), reason=state.get("reason"))


@router.post("/kill-switch/restart", response_model=KillSwitchState)
def restart_kill_switch(payload: RestartRequest) -> KillSwitchState:
    state = KillSwitch().restart(confirmed=payload.confirmed)
    if state["active"] and not payload.confirmed:
        raise HTTPException(status_code=400, detail=state["reason"])
    return KillSwitchState(active=bool(state.get("active")), reason=state.get("reason"))


@router.get("/notifications", response_model=list[NotificationModel])
def get_notifications(db: Session = Depends(get_db)) -> list[Notification]:
    return db.scalars(select(Notification).order_by(Notification.created_at.desc()).limit(100)).all()


@router.post("/notifications/{notification_id}/read", response_model=NotificationModel)
def mark_notification_read(notification_id: str, db: Session = Depends(get_db)) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return notification


@router.post("/notifications/read-all", response_model=dict[str, int])
def mark_all_notifications_read(db: Session = Depends(get_db)) -> dict[str, int]:
    notifications = db.scalars(select(Notification).where(Notification.is_read.is_(False))).all()
    for notification in notifications:
        notification.is_read = True
    db.commit()
    return {"updated": len(notifications)}
