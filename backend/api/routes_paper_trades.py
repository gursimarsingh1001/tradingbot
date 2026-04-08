from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.config import get_settings, to_camel
from backend.db.postgres import PaperTrade, get_db


router = APIRouter(prefix="/api/paper-trades", tags=["paper-trades"])
settings = get_settings()


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class PaperTradeItem(CamelModel):
    trade_id: str
    stock_symbol: str | None
    strategy_name: str | None
    signal_type: str | None
    direction: str | None
    entry_price: float | None
    current_price: float | None
    exit_price: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    target_3: float | None
    pnl_rupees: float | None
    pnl_pct: float | None
    status: str
    exit_reason: str | None
    targets_hit: dict[str, bool] | None
    entry_date: date | None
    exit_date: date | None
    source_kind: str | None = None
    watchlist_reason: str | None = None
    planned_for_date: date | None = None
    product_type: str | None = None
    leverage_multiplier: float | None = None
    capital_blocked: float | None = None
    remaining_shares: int | None = None
    initial_shares: int | None = None
    plan_status: str | None = None
    max_holding_days: int | None = None
    holding_horizon_label: str | None = None
    days_held: int | None = None
    days_remaining: int | None = None
    carries_forward: bool = False


class EquityCurvePoint(CamelModel):
    date: str
    value: float


class PaperTradeHistory(CamelModel):
    trades: list[PaperTradeItem]
    equity_curve: list[EquityCurvePoint]


class RecommendationDaySummary(CamelModel):
    trade_date: date
    stock_symbol: str
    total_recommendations: int
    worked_recommendations: int
    failed_recommendations: int
    open_recommendations: int
    win_rate: float
    avg_pnl_pct: float


class StrategyUsageSummary(CamelModel):
    strategy_name: str
    trades: int
    wins: int
    losses: int
    open_trades: int
    win_rate: float
    total_pnl_rupees: float
    avg_pnl_pct: float
    last_used_on: date | None


class StockTradeDaySummary(CamelModel):
    trade_date: date
    trades: int
    wins: int
    losses: int
    open_trades: int
    total_pnl_rupees: float
    avg_pnl_pct: float


class StockPaperTradeDetail(CamelModel):
    stock_symbol: str
    days: int
    total_trades: int
    wins: int
    losses: int
    open_trades: int
    win_rate: float
    total_pnl_rupees: float
    avg_pnl_pct: float
    best_strategy: str | None
    strategies: list[StrategyUsageSummary]
    daily_summary: list[StockTradeDaySummary]
    trades: list[PaperTradeItem]


class PaperTradeObservation(CamelModel):
    days: int
    executed_trades: int
    open_trades: int
    planned_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl_rupees: float
    avg_win_pct: float | None
    avg_loss_pct: float | None
    profit_factor: float | None
    current_streak_type: str | None
    current_streak_count: int
    best_strategy: str | None
    best_strategy_win_rate: float | None
    portfolio_value: float
    intraday_base_budget: float
    investment_base_budget: float
    intraday_budget: float
    investment_budget: float
    intraday_book_pnl_rupees: float
    investment_book_pnl_rupees: float
    intraday_open_capital_blocked: float
    intraday_planned_capital_blocked: float
    investment_open_capital_blocked: float
    investment_planned_capital_blocked: float
    intraday_available_capital: float
    investment_available_capital: float


def serialize_trade(trade: PaperTrade) -> PaperTradeItem:
    metadata = trade.metadata_json or {}
    is_plan_only = bool(metadata.get("plan_only"))
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
    if is_plan_only:
        status = str(metadata.get("plan_status") or "PLANNED")
    else:
        status = "OPEN" if trade.exit_date is None else "WIN" if trade.was_profitable else "LOSS"
    max_holding_days = int(metadata.get("max_holding_days")) if metadata.get("max_holding_days") is not None else None
    days_held = None
    days_remaining = None
    holding_horizon_label = "Same-day square-off" if effective_signal_type == "INTRADAY" else None
    carries_forward = effective_signal_type == "INVESTMENT"
    if carries_forward and max_holding_days:
        if trade.entry_date and not metadata.get("plan_only"):
            days_held = max((date.today() - trade.entry_date).days, 0)
            days_remaining = max(max_holding_days - days_held, 0)
        holding_horizon_label = (
            f"{max_holding_days} day carry-forward horizon"
            if max_holding_days > 1
            else "1 day carry-forward horizon"
        )
    return PaperTradeItem(
        trade_id=trade.trade_id,
        stock_symbol=trade.stock_symbol,
        strategy_name=trade.strategy_name,
        signal_type=trade.signal_type,
        direction=str(metadata.get("direction")).upper() if metadata.get("direction") else None,
        entry_price=trade.entry_price,
        current_price=trade.current_price,
        exit_price=trade.exit_price,
        stop_loss=trade.stop_loss,
        target_1=trade.target_1,
        target_2=trade.target_2,
        target_3=trade.target_3,
        pnl_rupees=trade.pnl_rupees,
        pnl_pct=trade.pnl_pct,
        status=status,
        exit_reason=trade.exit_reason,
        targets_hit=trade.targets_hit,
        entry_date=trade.entry_date,
        exit_date=trade.exit_date,
        source_kind=(
            str(metadata.get("source_kind") or metadata.get("opened_from") or "WATCHLIST_PLAN")
            if is_plan_only
            else str(metadata.get("source_kind") or metadata.get("opened_from") or "signal")
        ),
        watchlist_reason=metadata.get("watchlist_reason"),
        planned_for_date=trade.entry_date if is_plan_only else None,
        product_type=product_type,
        leverage_multiplier=leverage_multiplier,
        capital_blocked=capital_blocked,
        remaining_shares=int(metadata.get("remaining_shares")) if metadata.get("remaining_shares") is not None else trade.shares,
        initial_shares=int(metadata.get("initial_shares")) if metadata.get("initial_shares") is not None else trade.shares,
        plan_status=metadata.get("plan_status") if is_plan_only else None,
        max_holding_days=max_holding_days,
        holding_horizon_label=holding_horizon_label,
        days_held=days_held,
        days_remaining=days_remaining,
        carries_forward=carries_forward,
    )


@router.get("/today", response_model=list[PaperTradeItem])
def get_today_paper_trades(db: Session = Depends(get_db)) -> list[PaperTradeItem]:
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
    return [serialize_trade(trade) for trade in trades]


@router.get("/history", response_model=PaperTradeHistory)
def get_paper_trade_history(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db)) -> PaperTradeHistory:
    start_date = date.today() - timedelta(days=days)
    trades = db.scalars(
        select(PaperTrade)
        .where(
            or_(
                PaperTrade.entry_date >= start_date,
                PaperTrade.exit_date.is_(None),
            )
        )
        .order_by(PaperTrade.created_at.asc())
    ).all()
    serialized = [serialize_trade(trade) for trade in trades]
    running_value = 1_000_000.0
    curve: list[EquityCurvePoint] = []
    for trade in trades:
        if (trade.metadata_json or {}).get("plan_only"):
            continue
        running_value += float(trade.pnl_rupees or 0.0)
        curve.append(EquityCurvePoint(date=(trade.exit_date or trade.entry_date).isoformat(), value=running_value))
    return PaperTradeHistory(trades=serialized, equity_curve=curve)


@router.get("/effectiveness", response_model=list[RecommendationDaySummary])
def get_paper_trade_effectiveness(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db)) -> list[RecommendationDaySummary]:
    start_date = date.today() - timedelta(days=days)
    trades = db.scalars(
        select(PaperTrade)
        .where(PaperTrade.entry_date >= start_date, PaperTrade.entry_date <= date.today())
        .order_by(PaperTrade.entry_date.desc(), PaperTrade.created_at.desc())
    ).all()

    grouped: dict[tuple[date, str], dict[str, float | int]] = {}
    for trade in trades:
        if (trade.metadata_json or {}).get("plan_only"):
            continue
        if not trade.entry_date or not trade.stock_symbol:
            continue
        key = (trade.entry_date, trade.stock_symbol)
        bucket = grouped.setdefault(
            key,
            {
                "total": 0,
                "worked": 0,
                "failed": 0,
                "open": 0,
                "pnl_sum": 0.0,
                "pnl_count": 0,
            },
        )
        bucket["total"] += 1
        if trade.exit_date is None:
            bucket["open"] += 1
        elif trade.was_profitable:
            bucket["worked"] += 1
        else:
            bucket["failed"] += 1
        if trade.pnl_pct is not None:
            bucket["pnl_sum"] += float(trade.pnl_pct)
            bucket["pnl_count"] += 1

    rows: list[RecommendationDaySummary] = []
    for (trade_date, stock_symbol), bucket in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]), reverse=True):
        total = int(bucket["total"])
        worked = int(bucket["worked"])
        pnl_count = int(bucket["pnl_count"])
        rows.append(
            RecommendationDaySummary(
                trade_date=trade_date,
                stock_symbol=stock_symbol,
                total_recommendations=total,
                worked_recommendations=worked,
                failed_recommendations=int(bucket["failed"]),
                open_recommendations=int(bucket["open"]),
                win_rate=(worked / total) if total else 0.0,
                avg_pnl_pct=(float(bucket["pnl_sum"]) / pnl_count) if pnl_count else 0.0,
            )
        )
    return rows


@router.get("/observation", response_model=PaperTradeObservation)
def get_paper_trade_observation(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> PaperTradeObservation:
    start_date = date.today() - timedelta(days=days)
    trades = db.scalars(
        select(PaperTrade)
        .where(PaperTrade.entry_date >= start_date)
        .order_by(PaperTrade.exit_date.asc(), PaperTrade.created_at.asc())
    ).all()

    planned = [trade for trade in trades if (trade.metadata_json or {}).get("plan_only")]
    executed = [trade for trade in trades if not (trade.metadata_json or {}).get("plan_only")]
    closed = [trade for trade in executed if trade.exit_date is not None]
    wins = [trade for trade in closed if trade.was_profitable]
    losses = [trade for trade in closed if not trade.was_profitable]

    active_trades = db.scalars(select(PaperTrade).where(PaperTrade.exit_date.is_(None))).all()
    intraday_base_budget = settings.paper_portfolio_value * settings.paper_intraday_allocation_pct
    investment_base_budget = settings.paper_portfolio_value * settings.paper_investment_allocation_pct
    intraday_book_pnl = sum(
        float(trade.pnl_rupees or 0.0)
        for trade in executed
        if str(trade.signal_type or "INTRADAY").upper() == "INTRADAY"
    )
    investment_book_pnl = sum(
        float(trade.pnl_rupees or 0.0)
        for trade in executed
        if str(trade.signal_type or "").upper() == "INVESTMENT"
    )
    intraday_budget = max(intraday_base_budget + intraday_book_pnl, 0.0)
    investment_budget = max(investment_base_budget + investment_book_pnl, 0.0)
    intraday_open_capital_blocked = 0.0
    intraday_planned_capital_blocked = 0.0
    investment_open_capital_blocked = 0.0
    investment_planned_capital_blocked = 0.0
    for trade in active_trades:
        metadata = trade.metadata_json or {}
        capital_blocked = float(metadata.get("capital_blocked") or 0.0)
        signal_type = str(trade.signal_type or "INTRADAY").upper()
        plan_only = bool(metadata.get("plan_only"))
        if signal_type == "INVESTMENT":
            if plan_only:
                investment_planned_capital_blocked += capital_blocked
            else:
                investment_open_capital_blocked += capital_blocked
        else:
            if plan_only:
                intraday_planned_capital_blocked += capital_blocked
            else:
                intraday_open_capital_blocked += capital_blocked
    intraday_planned_capital_blocked = min(intraday_planned_capital_blocked, max(intraday_budget - intraday_open_capital_blocked, 0.0))
    investment_planned_capital_blocked = min(
        investment_planned_capital_blocked,
        max(investment_budget - investment_open_capital_blocked, 0.0),
    )

    strategy_buckets: dict[str, dict[str, float | int]] = {}
    for trade in closed:
        strategy_name = trade.strategy_name or "Unknown"
        bucket = strategy_buckets.setdefault(strategy_name, {"trades": 0, "wins": 0, "total_pnl": 0.0})
        bucket["trades"] += 1
        if trade.was_profitable:
            bucket["wins"] += 1
        bucket["total_pnl"] += float(trade.pnl_rupees or 0.0)

    best_strategy = None
    best_strategy_win_rate = None
    if strategy_buckets:
        ranked = sorted(
            strategy_buckets.items(),
            key=lambda item: (float(item[1]["total_pnl"]), float(item[1]["wins"]) / max(int(item[1]["trades"]), 1)),
            reverse=True,
        )
        best_strategy, best_bucket = ranked[0]
        best_strategy_win_rate = float(best_bucket["wins"]) / max(int(best_bucket["trades"]), 1)

    current_streak_type = None
    current_streak_count = 0
    if closed:
        ordered_closed = sorted(closed, key=lambda trade: ((trade.exit_date or trade.entry_date), trade.created_at))
        last_result = bool(ordered_closed[-1].was_profitable)
        current_streak_type = "WIN" if last_result else "LOSS"
        for trade in reversed(ordered_closed):
            if bool(trade.was_profitable) != last_result:
                break
            current_streak_count += 1

    avg_win_pct = (
        sum(float(trade.pnl_pct or 0.0) for trade in wins) / len(wins)
        if wins
        else None
    )
    avg_loss_pct = (
        sum(float(trade.pnl_pct or 0.0) for trade in losses) / len(losses)
        if losses
        else None
    )
    total_wins_rupees = sum(max(float(trade.pnl_rupees or 0.0), 0.0) for trade in wins)
    total_losses_rupees = abs(sum(min(float(trade.pnl_rupees or 0.0), 0.0) for trade in losses))
    profit_factor = (total_wins_rupees / total_losses_rupees) if total_losses_rupees > 0 else (None if total_wins_rupees == 0 else total_wins_rupees)

    return PaperTradeObservation(
        days=days,
        executed_trades=len(executed),
        open_trades=sum(1 for trade in executed if trade.exit_date is None),
        planned_trades=len(planned),
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / len(closed)) if closed else 0.0,
        total_pnl_rupees=float(sum(float(trade.pnl_rupees or 0.0) for trade in executed)),
        avg_win_pct=float(avg_win_pct) if avg_win_pct is not None else None,
        avg_loss_pct=float(avg_loss_pct) if avg_loss_pct is not None else None,
        profit_factor=float(profit_factor) if profit_factor is not None else None,
        current_streak_type=current_streak_type,
        current_streak_count=current_streak_count,
        best_strategy=best_strategy,
        best_strategy_win_rate=float(best_strategy_win_rate) if best_strategy_win_rate is not None else None,
        portfolio_value=float(intraday_budget + investment_budget),
        intraday_base_budget=float(intraday_base_budget),
        investment_base_budget=float(investment_base_budget),
        intraday_budget=float(intraday_budget),
        investment_budget=float(investment_budget),
        intraday_book_pnl_rupees=float(intraday_book_pnl),
        investment_book_pnl_rupees=float(investment_book_pnl),
        intraday_open_capital_blocked=float(intraday_open_capital_blocked),
        intraday_planned_capital_blocked=float(intraday_planned_capital_blocked),
        investment_open_capital_blocked=float(investment_open_capital_blocked),
        investment_planned_capital_blocked=float(investment_planned_capital_blocked),
        intraday_available_capital=float(max(intraday_budget - intraday_open_capital_blocked, 0.0)),
        investment_available_capital=float(max(investment_budget - investment_open_capital_blocked, 0.0)),
    )


@router.get("/stock/{symbol}", response_model=StockPaperTradeDetail)
def get_stock_paper_trade_detail(
    symbol: str,
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
) -> StockPaperTradeDetail:
    normalized_symbol = symbol.upper()
    start_date = date.today() - timedelta(days=days)
    trades = db.scalars(
        select(PaperTrade)
        .where(
            PaperTrade.stock_symbol == normalized_symbol,
            or_(
                PaperTrade.entry_date >= start_date,
                PaperTrade.exit_date.is_(None),
            ),
        )
        .order_by(PaperTrade.entry_date.desc(), PaperTrade.created_at.desc())
    ).all()

    executed = [trade for trade in trades if not (trade.metadata_json or {}).get("plan_only")]
    serialized = [serialize_trade(trade) for trade in executed]

    total_trades = len(executed)
    wins = sum(1 for trade in executed if trade.exit_date is not None and trade.was_profitable)
    losses = sum(1 for trade in executed if trade.exit_date is not None and not trade.was_profitable)
    open_trades = sum(1 for trade in executed if trade.exit_date is None)
    pnl_values = [float(trade.pnl_pct) for trade in executed if trade.pnl_pct is not None]
    total_pnl_rupees = float(sum(float(trade.pnl_rupees or 0.0) for trade in executed))
    avg_pnl_pct = float(sum(pnl_values) / len(pnl_values)) if pnl_values else 0.0

    strategy_buckets: dict[str, dict[str, int | float | date | None]] = {}
    daily_buckets: dict[date, dict[str, int | float]] = {}
    for trade in executed:
        strategy_name = trade.strategy_name or "Unknown"
        strategy_bucket = strategy_buckets.setdefault(
            strategy_name,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "open_trades": 0,
                "total_pnl_rupees": 0.0,
                "pnl_sum": 0.0,
                "pnl_count": 0,
                "last_used_on": None,
            },
        )
        strategy_bucket["trades"] += 1
        if trade.exit_date is None:
            strategy_bucket["open_trades"] += 1
        elif trade.was_profitable:
            strategy_bucket["wins"] += 1
        else:
            strategy_bucket["losses"] += 1
        strategy_bucket["total_pnl_rupees"] += float(trade.pnl_rupees or 0.0)
        if trade.pnl_pct is not None:
            strategy_bucket["pnl_sum"] += float(trade.pnl_pct)
            strategy_bucket["pnl_count"] += 1
        trade_day = trade.entry_date
        previous_used = strategy_bucket["last_used_on"]
        if trade_day is not None and (previous_used is None or trade_day > previous_used):
            strategy_bucket["last_used_on"] = trade_day

        if trade_day is None:
            continue
        daily_bucket = daily_buckets.setdefault(
            trade_day,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "open_trades": 0,
                "total_pnl_rupees": 0.0,
                "pnl_sum": 0.0,
                "pnl_count": 0,
            },
        )
        daily_bucket["trades"] += 1
        if trade.exit_date is None:
            daily_bucket["open_trades"] += 1
        elif trade.was_profitable:
            daily_bucket["wins"] += 1
        else:
            daily_bucket["losses"] += 1
        daily_bucket["total_pnl_rupees"] += float(trade.pnl_rupees or 0.0)
        if trade.pnl_pct is not None:
            daily_bucket["pnl_sum"] += float(trade.pnl_pct)
            daily_bucket["pnl_count"] += 1

    strategies = [
        StrategyUsageSummary(
            strategy_name=name,
            trades=int(bucket["trades"]),
            wins=int(bucket["wins"]),
            losses=int(bucket["losses"]),
            open_trades=int(bucket["open_trades"]),
            win_rate=(float(bucket["wins"]) / float(bucket["trades"])) if float(bucket["trades"]) else 0.0,
            total_pnl_rupees=float(bucket["total_pnl_rupees"]),
            avg_pnl_pct=(float(bucket["pnl_sum"]) / float(bucket["pnl_count"])) if float(bucket["pnl_count"]) else 0.0,
            last_used_on=bucket["last_used_on"],
        )
        for name, bucket in strategy_buckets.items()
    ]
    strategies.sort(key=lambda item: (item.total_pnl_rupees, item.win_rate), reverse=True)

    daily_summary = [
        StockTradeDaySummary(
            trade_date=trade_day,
            trades=int(bucket["trades"]),
            wins=int(bucket["wins"]),
            losses=int(bucket["losses"]),
            open_trades=int(bucket["open_trades"]),
            total_pnl_rupees=float(bucket["total_pnl_rupees"]),
            avg_pnl_pct=(float(bucket["pnl_sum"]) / float(bucket["pnl_count"])) if float(bucket["pnl_count"]) else 0.0,
        )
        for trade_day, bucket in sorted(daily_buckets.items(), key=lambda item: item[0], reverse=True)
    ]

    return StockPaperTradeDetail(
        stock_symbol=normalized_symbol,
        days=days,
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        open_trades=open_trades,
        win_rate=(wins / total_trades) if total_trades else 0.0,
        total_pnl_rupees=total_pnl_rupees,
        avg_pnl_pct=avg_pnl_pct,
        best_strategy=strategies[0].strategy_name if strategies else None,
        strategies=strategies,
        daily_summary=daily_summary,
        trades=serialized,
    )
