from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from functools import lru_cache
from statistics import mean
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.api.routes_paper_trades import (
    get_paper_trade_history,
    get_paper_trade_observation,
    serialize_trade,
)
from backend.config import get_settings
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.postgres import (
    BotConfig,
    GlobalRiskSnapshot,
    LynchScore,
    MinerviniScore,
    OfficialInvestmentSnapshot,
    OfficialMarketContextSnapshot,
    PaperTrade,
    PiotroskiScore,
    get_config_value,
    get_db,
)
from backend.engine.investment_gate_runner import InvestmentGateRunner
from backend.engine.scheduler_runtime import get_embedded_scheduler_service


router = APIRouter(prefix="/api", tags=["investment"])
settings = get_settings()
gate_runner = InvestmentGateRunner()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return jsonable_encoder(asdict(value))
    return jsonable_encoder(value)


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _today_local() -> date:
    return datetime.now(tz=settings.tzinfo).date()


@lru_cache(maxsize=1)
def _known_dashboard_symbols() -> set[str]:
    try:
        return set(HistoricalFetcher().load_symbol_map().keys())
    except Exception:
        return set()


def _is_known_dashboard_symbol(symbol: str | None) -> bool:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return False
    known_symbols = _known_dashboard_symbols()
    if not known_symbols:
        return True
    return normalized in known_symbols


def _resolve_snapshot_date(db: Session, as_of_date: date | None) -> date | None:
    if as_of_date is not None:
        return as_of_date
    return db.scalar(
        select(OfficialInvestmentSnapshot.as_of_date)
        .where(OfficialInvestmentSnapshot.as_of_date <= _today_local())
        .order_by(OfficialInvestmentSnapshot.as_of_date.desc())
    )


def _resolve_symbol_snapshot_date(db: Session, symbol: str, as_of_date: date | None) -> date | None:
    if as_of_date is not None:
        return as_of_date
    return db.scalar(
        select(OfficialInvestmentSnapshot.as_of_date)
        .where(
            OfficialInvestmentSnapshot.symbol == symbol,
            OfficialInvestmentSnapshot.as_of_date <= _today_local(),
        )
        .order_by(OfficialInvestmentSnapshot.as_of_date.desc())
    )


def _label_from_votes(lynch_vote: bool, piotroski_vote: bool, minervini_vote: bool) -> tuple[int, str]:
    votes_yes = sum(1 for vote in (lynch_vote, piotroski_vote, minervini_vote) if vote)
    if votes_yes == 3:
        return votes_yes, "STRONG_BUY"
    if votes_yes == 2:
        return votes_yes, "WATCHLIST"
    return votes_yes, "NO_ACTION"


def _score_maps(
    db: Session,
    as_of_date: date,
    symbols: list[str] | None = None,
) -> tuple[dict[str, LynchScore], dict[str, PiotroskiScore], dict[str, MinerviniScore]]:
    lynch_stmt = select(LynchScore).where(LynchScore.as_of_date == as_of_date)
    piotroski_stmt = select(PiotroskiScore).where(PiotroskiScore.as_of_date == as_of_date)
    minervini_stmt = select(MinerviniScore).where(MinerviniScore.as_of_date == as_of_date)
    if symbols:
        lynch_stmt = lynch_stmt.where(LynchScore.symbol.in_(symbols))
        piotroski_stmt = piotroski_stmt.where(PiotroskiScore.symbol.in_(symbols))
        minervini_stmt = minervini_stmt.where(MinerviniScore.symbol.in_(symbols))
    lynch_rows = db.scalars(lynch_stmt).all()
    piotroski_rows = db.scalars(piotroski_stmt).all()
    minervini_rows = db.scalars(minervini_stmt).all()
    return (
        {row.symbol: row for row in lynch_rows if row.symbol},
        {row.symbol: row for row in piotroski_rows if row.symbol},
        {row.symbol: row for row in minervini_rows if row.symbol},
    )


def _data_fill_rate(snapshot: OfficialInvestmentSnapshot | None) -> float | None:
    if snapshot is None:
        return None
    data_sources = dict(snapshot.data_sources or {})
    fill_rate = _float(data_sources.get("fill_rate"))
    if fill_rate is not None:
        return fill_rate
    source_coverage = dict(snapshot.source_coverage or {})
    return _float(source_coverage.get("fill_rate"))


def _score_row(
    snapshot: OfficialInvestmentSnapshot,
    lynch: LynchScore | None,
    piotroski: PiotroskiScore | None,
    minervini: MinerviniScore | None,
) -> dict[str, Any]:
    lynch_vote = bool(lynch.vote_yes) if lynch is not None else False
    piotroski_vote = bool(piotroski.vote_yes) if piotroski is not None else False
    minervini_vote = bool(minervini.vote_yes) if minervini is not None else False
    votes_yes, label = _label_from_votes(lynch_vote, piotroski_vote, minervini_vote)
    return {
        "symbol": snapshot.symbol,
        "companyName": snapshot.company_name,
        "sector": snapshot.sector,
        "asOfDate": snapshot.as_of_date,
        "label": label,
        "votesYes": votes_yes,
        "lynchVote": lynch_vote,
        "piotroskiVote": piotroski_vote,
        "minerviniVote": minervini_vote,
        "lynchValue": _float(lynch.lynch_value) if lynch is not None else None,
        "piotroskiFScore": int(piotroski.f_score or 0) if piotroski is not None else 0,
        "minerviniPassedChecks": int(minervini.passed_checks or 0) if minervini is not None else 0,
        "minerviniRsPercentile": _float(minervini.rs_percentile) if minervini is not None else None,
        "dataComplete": bool(
            (lynch.data_complete if lynch is not None else False)
            and (piotroski.data_complete if piotroski is not None else False)
            and (minervini.data_complete if minervini is not None else False)
        ),
        "fillRate": _data_fill_rate(snapshot),
        "peRatio": _float(snapshot.pe_ratio),
        "pbRatio": _float(snapshot.pb_ratio),
        "marketCap": _float(snapshot.market_cap),
        "dividendYield": _float(snapshot.dividend_yield),
        "epsGrowth3yCagr": _float(snapshot.eps_growth_3y_cagr),
        "earningsDate": snapshot.earnings_date,
    }


def _scoring_universe(db: Session, as_of_date: date) -> list[dict[str, Any]]:
    snapshots = db.scalars(
        select(OfficialInvestmentSnapshot)
        .where(OfficialInvestmentSnapshot.as_of_date == as_of_date)
        .order_by(OfficialInvestmentSnapshot.symbol.asc())
    ).all()
    snapshots = [snapshot for snapshot in snapshots if _is_known_dashboard_symbol(snapshot.symbol)]
    lynch_map, piotroski_map, minervini_map = _score_maps(db, as_of_date, [row.symbol for row in snapshots if row.symbol])
    return [
        _score_row(snapshot, lynch_map.get(snapshot.symbol), piotroski_map.get(snapshot.symbol), minervini_map.get(snapshot.symbol))
        for snapshot in snapshots
        if snapshot.symbol
    ]


def _risk_signal_card(row: GlobalRiskSnapshot, signal_name: str, label: str) -> dict[str, Any]:
    details = dict((row.signal_details or {}).get(signal_name) or {})
    return {
        "name": signal_name,
        "label": label,
        "severity": details.get("severity") or "NONE",
        "value": details.get("value"),
        "threshold": details.get("threshold"),
        "message": details.get("message"),
        "details": details.get("details") or {},
    }


def _risk_row_payload(row: GlobalRiskSnapshot) -> dict[str, Any]:
    signal_cards = [
        _risk_signal_card(row, "vix_velocity", "VIX Velocity"),
        _risk_signal_card(row, "nifty_gap", "Nifty Gap"),
        _risk_signal_card(row, "fii_flow", "FII Flow"),
        _risk_signal_card(row, "sp500_overnight", "S&P 500 Overnight"),
        _risk_signal_card(row, "crude_oil", "Brent Crude"),
        _risk_signal_card(row, "currency_stress", "USD/INR Stress"),
    ]
    return {
        "asOfDate": row.as_of_date,
        "scanType": row.scan_type,
        "riskLevel": row.risk_level,
        "positionSizeMultiplier": _float(row.position_size_multiplier),
        "activeSignals": list(row.active_signals or []),
        "activeCautionCount": sum(1 for card in signal_cards if card["severity"] == "CAUTION"),
        "activeBlockCount": sum(1 for card in signal_cards if card["severity"] == "BLOCK"),
        "signals": signal_cards,
    }


def _cutover_plan_rows(db: Session) -> list[PaperTrade]:
    rows = db.scalars(
        select(PaperTrade)
        .where(
            PaperTrade.signal_type == "INVESTMENT",
            PaperTrade.exit_date.is_(None),
        )
        .order_by(PaperTrade.entry_date.desc(), PaperTrade.created_at.desc())
    ).all()
    plans: list[PaperTrade] = []
    for row in rows:
        metadata = dict(row.metadata_json or {})
        if (
            metadata.get("plan_only")
            and metadata.get("source_kind") == "official_investment_cutover"
            and _is_known_dashboard_symbol(row.stock_symbol)
        ):
            plans.append(row)
    return plans


def _latest_cutover_payload(db: Session) -> dict[str, Any]:
    plans = _cutover_plan_rows(db)
    if not plans:
        return {
            "latestPlanDate": None,
            "plannedCount": 0,
            "globalRiskLevel": None,
            "positionSizeMultiplier": None,
            "activeGlobalSignals": [],
            "signals": [],
        }
    latest_plan_date = max(plan.entry_date for plan in plans if plan.entry_date is not None)
    selected = [plan for plan in plans if plan.entry_date == latest_plan_date]
    first_metadata = dict(selected[0].metadata_json or {})
    return {
        "latestPlanDate": latest_plan_date,
        "plannedCount": len(selected),
        "globalRiskLevel": first_metadata.get("global_risk_level"),
        "globalRiskScanType": first_metadata.get("global_risk_scan_type"),
        "globalRiskAsOfDate": first_metadata.get("global_risk_as_of_date"),
        "positionSizeMultiplier": _float(first_metadata.get("position_size_multiplier")),
        "activeGlobalSignals": list(first_metadata.get("active_global_signals") or []),
        "signals": [_jsonable(serialize_trade(plan)) for plan in selected],
    }


def _monthly_returns(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for trade in trades:
        exit_date = trade.get("exitDate") or trade.get("entryDate")
        if not exit_date:
            continue
        month_key = str(exit_date)[:7]
        bucket = buckets.setdefault(month_key, {"month": month_key, "pnlPct": 0.0, "pnlRupees": 0.0, "trades": 0})
        bucket["pnlPct"] += float(trade.get("pnlPct") or 0.0)
        bucket["pnlRupees"] += float(trade.get("pnlRupees") or 0.0)
        bucket["trades"] += 1
    return [buckets[key] for key in sorted(buckets)]


def _scheduler_jobs_payload() -> dict[str, Any]:
    service = get_embedded_scheduler_service()
    if service is None:
        return {
            "health": {
                "market": {"status": "not_started", "last_event_at": None, "last_error": None},
                "afterMarket": {"status": "not_started", "last_event_at": None, "last_error": None},
            },
            "nextJobs": [],
        }
    jobs: list[dict[str, Any]] = []
    for scheduler_name, scheduler in (("market", service.market_scheduler), ("afterMarket", service.after_market_scheduler)):
        for job in scheduler.get_jobs():
            jobs.append(
                {
                    "scheduler": scheduler_name,
                    "id": job.id,
                    "name": job.name,
                    "nextRunAt": job.next_run_time,
                }
            )
    jobs.sort(key=lambda item: item["nextRunAt"] or datetime.max)
    return {
        "health": {
            "market": dict(getattr(service, "_scheduler_health", {}).get("market", {})),
            "afterMarket": dict(getattr(service, "_scheduler_health", {}).get("after_market", {})),
        },
        "nextJobs": jobs[:10],
    }


@router.get("/scoring/summary")
def get_scoring_summary(as_of_date: date | None = Query(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    resolved_date = _resolve_snapshot_date(db, as_of_date)
    if resolved_date is None:
        return {
            "asOfDate": None,
            "universeSize": 0,
            "counts": {"strongBuy": 0, "watchlist": 0, "noAction": 0},
            "distribution": [],
            "averages": {"lynchValue": None, "piotroskiFScore": None, "minerviniRsPercentile": None, "fillRate": None},
        }
    rows = _scoring_universe(db, resolved_date)
    distribution = {
        "STRONG_BUY": sum(1 for row in rows if row["label"] == "STRONG_BUY"),
        "WATCHLIST": sum(1 for row in rows if row["label"] == "WATCHLIST"),
        "NO_ACTION": sum(1 for row in rows if row["label"] == "NO_ACTION"),
    }
    lynch_values = [row["lynchValue"] for row in rows if row["lynchValue"] is not None]
    piotroski_values = [row["piotroskiFScore"] for row in rows]
    minervini_values = [row["minerviniRsPercentile"] for row in rows if row["minerviniRsPercentile"] is not None]
    fill_rates = [row["fillRate"] for row in rows if row["fillRate"] is not None]
    return {
        "asOfDate": resolved_date,
        "universeSize": len(rows),
        "counts": {
            "strongBuy": distribution["STRONG_BUY"],
            "watchlist": distribution["WATCHLIST"],
            "noAction": distribution["NO_ACTION"],
        },
        "distribution": [{"label": label, "count": count} for label, count in distribution.items()],
        "averages": {
            "lynchValue": round(mean(lynch_values), 4) if lynch_values else None,
            "piotroskiFScore": round(mean(piotroski_values), 2) if piotroski_values else None,
            "minerviniRsPercentile": round(mean(minervini_values), 2) if minervini_values else None,
            "fillRate": round(mean(fill_rates), 4) if fill_rates else None,
        },
    }


@router.get("/scoring/universe")
def get_scoring_universe(as_of_date: date | None = Query(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    resolved_date = _resolve_snapshot_date(db, as_of_date)
    if resolved_date is None:
        return {"asOfDate": None, "rows": []}
    return {"asOfDate": resolved_date, "rows": _scoring_universe(db, resolved_date)}


@router.get("/scoring/detail/{symbol}")
def get_scoring_detail(symbol: str, as_of_date: date | None = Query(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    normalized_symbol = symbol.upper().strip()
    if not _is_known_dashboard_symbol(normalized_symbol):
        raise HTTPException(status_code=404, detail="No official investment snapshot found for this symbol.")
    resolved_date = _resolve_symbol_snapshot_date(db, normalized_symbol, as_of_date)
    if resolved_date is None:
        raise HTTPException(status_code=404, detail="No official investment snapshot found for this symbol.")
    snapshot = db.scalar(
        select(OfficialInvestmentSnapshot).where(
            OfficialInvestmentSnapshot.symbol == normalized_symbol,
            OfficialInvestmentSnapshot.as_of_date == resolved_date,
        )
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No official investment snapshot found for this symbol.")
    lynch_map, piotroski_map, minervini_map = _score_maps(db, resolved_date, [normalized_symbol])
    lynch = lynch_map.get(normalized_symbol)
    piotroski = piotroski_map.get(normalized_symbol)
    minervini = minervini_map.get(normalized_symbol)
    score_row = _score_row(snapshot, lynch, piotroski, minervini)
    return {
        **score_row,
        "snapshot": _jsonable(
            {
                "companyName": snapshot.company_name,
                "sector": snapshot.sector,
                "earningsDate": snapshot.earnings_date,
                "marketCap": snapshot.market_cap,
                "peRatio": snapshot.pe_ratio,
                "pbRatio": snapshot.pb_ratio,
                "dividendYield": snapshot.dividend_yield,
                "industryPe": snapshot.industry_pe,
                "week52High": snapshot.week_52_high,
                "week52Low": snapshot.week_52_low,
                "epsTtm": snapshot.eps_ttm,
                "epsGrowth3yCagr": snapshot.eps_growth_3y_cagr,
                "revenueGrowthPct": snapshot.revenue_growth_pct,
                "profitGrowthPct": snapshot.profit_growth_pct,
                "operatingMargin": snapshot.operating_margin,
                "netMargin": snapshot.net_margin,
                "roe": snapshot.roe,
                "roce": snapshot.roce,
                "debtToEquity": snapshot.debt_to_equity,
                "currentRatio": snapshot.current_ratio,
                "promoterHolding": snapshot.promoter_holding,
                "promoterPledge": snapshot.promoter_pledge,
                "promoterHoldingChangePct": snapshot.promoter_holding_change_pct,
                "fiiHolding": snapshot.fii_holding,
                "diiHolding": snapshot.dii_holding,
                "sourceCoverage": snapshot.source_coverage or {},
                "dataSources": snapshot.data_sources or {},
                "rawMetrics": snapshot.raw_metrics or {},
            }
        ),
        "lynch": _jsonable(lynch) if lynch is not None else None,
        "piotroski": _jsonable(piotroski) if piotroski is not None else None,
        "minervini": _jsonable(minervini) if minervini is not None else None,
    }


@router.get("/gates/summary")
def get_gates_summary(as_of_date: date | None = Query(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    resolved_date = _resolve_snapshot_date(db, as_of_date)
    if resolved_date is None:
        return {
            "asOfDate": None,
            "eligibleStrongBuy": 0,
            "buy": 0,
            "skip": 0,
            "blockedByMarketHealth": 0,
            "blockedBySectorStrength": 0,
            "blockedByEarningsProximity": 0,
            "blockedByPromoter": 0,
            "blockedByEntryTrigger": 0,
            "funnel": [],
        }
    summary = gate_runner.run_universe(as_of_date=resolved_date)
    return {
        "asOfDate": summary.get("as_of_date"),
        "eligibleStrongBuy": int(summary.get("eligible_strong_buy") or 0),
        "buy": int(summary.get("buy") or 0),
        "skip": int(summary.get("skip") or 0),
        "blockedByMarketHealth": int(summary.get("blocked_by_market_health") or 0),
        "blockedBySectorStrength": int(summary.get("blocked_by_sector_strength") or 0),
        "blockedByEarningsProximity": int(summary.get("blocked_by_earnings_proximity") or 0),
        "blockedByPromoter": int(summary.get("blocked_by_promoter") or 0),
        "blockedByEntryTrigger": int(summary.get("blocked_by_entry_trigger") or 0),
        "funnel": [
            {"stage": "Phase 2 Strong Buy", "count": int(summary.get("eligible_strong_buy") or 0)},
            {"stage": "Phase 3 Approved BUY", "count": int(summary.get("buy") or 0)},
            {"stage": "Skipped", "count": int(summary.get("skip") or 0)},
        ],
    }


@router.get("/gates/universe")
def get_gates_universe(as_of_date: date | None = Query(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    resolved_date = _resolve_snapshot_date(db, as_of_date)
    if resolved_date is None:
        return {"asOfDate": None, "rows": []}
    summary = gate_runner.run_universe(as_of_date=resolved_date)
    rows = [_jsonable(result) for result in summary.get("results") or []]
    return {"asOfDate": summary.get("as_of_date"), "rows": rows, "failedExamples": summary.get("failed_examples") or {}}


@router.get("/gates/detail/{symbol}")
def get_gates_detail(symbol: str, as_of_date: date | None = Query(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    normalized_symbol = symbol.upper().strip()
    resolved_date = _resolve_symbol_snapshot_date(db, normalized_symbol, as_of_date)
    if resolved_date is None:
        raise HTTPException(status_code=404, detail="No official investment snapshot found for this symbol.")
    result = gate_runner.score_symbol(normalized_symbol, as_of_date=resolved_date)
    if result is None:
        raise HTTPException(status_code=404, detail="No gate decision could be produced for this symbol.")
    return _jsonable(result)


@router.get("/cutover/latest")
def get_cutover_latest(db: Session = Depends(get_db)) -> dict[str, Any]:
    return _latest_cutover_payload(db)


@router.get("/risk/latest")
def get_risk_latest(db: Session = Depends(get_db)) -> dict[str, Any]:
    latest = db.scalar(
        select(GlobalRiskSnapshot)
        .where(GlobalRiskSnapshot.as_of_date <= _today_local())
        .order_by(desc(GlobalRiskSnapshot.as_of_date), desc(GlobalRiskSnapshot.created_at))
    )
    if latest is None:
        return {"latest": None}
    return {"latest": _risk_row_payload(latest)}


@router.get("/risk/history")
def get_risk_history(
    days: int = Query(default=30, ge=5, le=180),
    scan_type: str = Query(default="AFTER_MARKET"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.scalars(
        select(GlobalRiskSnapshot)
        .where(GlobalRiskSnapshot.scan_type == scan_type.upper())
        .order_by(GlobalRiskSnapshot.as_of_date.desc())
        .limit(days)
    ).all()
    ordered = list(reversed(rows))
    return {
        "scanType": scan_type.upper(),
        "rows": [
            {
                "asOfDate": row.as_of_date,
                "riskLevel": row.risk_level,
                "positionSizeMultiplier": row.position_size_multiplier,
                "activeSignals": list(row.active_signals or []),
                "activeCautionCount": sum(
                    1
                    for details in (row.signal_details or {}).values()
                    if isinstance(details, dict) and details.get("severity") == "CAUTION"
                ),
                "activeBlockCount": sum(
                    1
                    for details in (row.signal_details or {}).values()
                    if isinstance(details, dict) and details.get("severity") == "BLOCK"
                ),
            }
            for row in ordered
        ],
    }


@router.get("/portfolio/summary")
def get_portfolio_summary(days: int = Query(default=180, ge=30, le=365), db: Session = Depends(get_db)) -> dict[str, Any]:
    observation = get_paper_trade_observation(days=days, db=db)
    cutover = _latest_cutover_payload(db)
    return {
        "days": days,
        "observation": observation.model_dump(by_alias=True),
        "latestOfficialCutoverPlans": int(cutover.get("plannedCount") or 0),
    }


@router.get("/portfolio/performance")
def get_portfolio_performance(days: int = Query(default=180, ge=30, le=365), db: Session = Depends(get_db)) -> dict[str, Any]:
    history = get_paper_trade_history(days=days, db=db)
    history_payload = history.model_dump(by_alias=True)
    closed_trades = [trade for trade in history_payload["trades"] if trade.get("status") in {"WIN", "LOSS"}]
    return {
        "days": days,
        "equityCurve": history_payload["equityCurve"],
        "closedTradeCount": len(closed_trades),
        "monthlyReturns": _monthly_returns(closed_trades),
        "tradeReturnDistribution": [
            {
                "symbol": trade.get("stockSymbol"),
                "strategyName": trade.get("strategyName"),
                "pnlPct": trade.get("pnlPct"),
                "pnlRupees": trade.get("pnlRupees"),
                "exitDate": trade.get("exitDate"),
            }
            for trade in closed_trades
        ],
    }


@router.get("/trades/open")
def get_open_trades(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(
        select(PaperTrade)
        .where(PaperTrade.exit_date.is_(None))
        .order_by(PaperTrade.created_at.desc())
    ).all()
    planned: list[dict[str, Any]] = []
    open_trades: list[dict[str, Any]] = []
    for row in rows:
        payload = _jsonable(serialize_trade(row))
        if payload.get("planStatus"):
            planned.append(payload)
        else:
            open_trades.append(payload)
    return {
        "planned": planned,
        "open": open_trades,
        "plannedCount": len(planned),
        "openCount": len(open_trades),
    }


@router.get("/trades/closed")
def get_closed_trades(days: int = Query(default=180, ge=30, le=365), db: Session = Depends(get_db)) -> dict[str, Any]:
    since = date.fromordinal(date.today().toordinal() - days)
    rows = db.scalars(
        select(PaperTrade)
        .where(PaperTrade.exit_date.is_not(None), PaperTrade.entry_date >= since)
        .order_by(PaperTrade.exit_date.desc(), PaperTrade.created_at.desc())
    ).all()
    trades = [_jsonable(serialize_trade(row)) for row in rows]
    wins = sum(1 for trade in trades if trade.get("status") == "WIN")
    losses = sum(1 for trade in trades if trade.get("status") == "LOSS")
    return {
        "days": days,
        "trades": trades,
        "count": len(trades),
        "wins": wins,
        "losses": losses,
        "winRate": round(wins / len(trades), 4) if trades else 0.0,
    }


@router.get("/market/indices")
def get_market_indices(db: Session = Depends(get_db)) -> dict[str, Any]:
    latest = db.scalar(
        select(OfficialMarketContextSnapshot)
        .where(OfficialMarketContextSnapshot.as_of_date <= _today_local())
        .order_by(OfficialMarketContextSnapshot.as_of_date.desc())
    )
    if latest is None:
        return {"asOfDate": None, "indices": [], "sectorOverview": {"aboveSma50": 0, "total": 0, "leaders": []}}
    sector_context = dict(latest.sector_context or {})
    leaders = []
    above_sma50 = 0
    for sector, details in sector_context.items():
        close = _float((details or {}).get("close"))
        sma50 = _float((details or {}).get("sma50"))
        if close is not None and sma50 is not None and sma50 > 0:
            delta_pct = ((close / sma50) - 1.0) * 100.0
            if close > sma50:
                above_sma50 += 1
            leaders.append({"sector": sector, "close": close, "sma50": sma50, "deltaPctToSma50": round(delta_pct, 2)})
    leaders.sort(key=lambda item: item["deltaPctToSma50"], reverse=True)
    nifty_delta = None
    if latest.nifty50_close and latest.nifty50_sma200:
        nifty_delta = round(((latest.nifty50_close / latest.nifty50_sma200) - 1.0) * 100.0, 2)
    return {
        "asOfDate": latest.as_of_date,
        "indices": [
            {
                "key": "NIFTY50",
                "label": "Nifty 50",
                "value": latest.nifty50_close,
                "reference": latest.nifty50_sma200,
                "deltaPctToReference": nifty_delta,
                "status": "ABOVE_SMA200" if (latest.nifty50_close or 0.0) >= (latest.nifty50_sma200 or 0.0) else "BELOW_SMA200",
            },
            {
                "key": "INDIA_VIX",
                "label": "India VIX",
                "value": latest.india_vix,
                "reference": 25.0,
                "deltaPctToReference": None,
                "status": "PANIC" if (latest.india_vix or 0.0) > 25.0 else "NORMAL",
            },
            {
                "key": "AAA_BOND",
                "label": "AAA Bond Yield",
                "value": latest.aaa_bond_yield,
                "reference": None,
                "deltaPctToReference": None,
                "status": "YIELD_CONTEXT",
            },
        ],
        "sectorOverview": {
            "aboveSma50": above_sma50,
            "total": len(sector_context),
            "leaders": leaders[:5],
        },
    }


@router.get("/system/status")
def get_system_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    kill_switch = get_config_value(db, "kill_switch", {"active": False, "reason": None})
    quote_sync = get_config_value(db, settings.official_shadow_quote_state_key, {})
    weekly_sync = get_config_value(db, settings.official_shadow_weekly_state_key, {})
    shadow_summary = get_config_value(db, settings.official_shadow_summary_key, {})
    news_sync = get_config_value(db, "news_sync_state", {})
    fundamentals_sync = get_config_value(db, "fundamentals_sync_state", {})
    latest_snapshot_date = _resolve_snapshot_date(db, None)
    today = _today_local()
    latest_score_date = db.scalar(
        select(LynchScore.as_of_date)
        .where(LynchScore.as_of_date <= today)
        .order_by(LynchScore.as_of_date.desc())
    )
    latest_risk = db.scalar(
        select(GlobalRiskSnapshot)
        .where(GlobalRiskSnapshot.as_of_date <= today)
        .order_by(desc(GlobalRiskSnapshot.as_of_date), desc(GlobalRiskSnapshot.created_at))
    )
    latest_context = db.scalar(
        select(OfficialMarketContextSnapshot)
        .where(OfficialMarketContextSnapshot.as_of_date <= today)
        .order_by(OfficialMarketContextSnapshot.as_of_date.desc())
    )
    cutover = _latest_cutover_payload(db)

    latest_fill_rates: list[float] = []
    latest_snapshot_count = 0
    if latest_snapshot_date is not None:
        latest_snapshots = db.scalars(
            select(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.as_of_date == latest_snapshot_date)
        ).all()
        latest_snapshot_count = len(latest_snapshots)
        latest_fill_rates = [fill_rate for fill_rate in (_data_fill_rate(snapshot) for snapshot in latest_snapshots) if fill_rate is not None]

    latest_score_rows = _scoring_universe(db, latest_score_date) if latest_score_date is not None else []
    scheduler_payload = _scheduler_jobs_payload()
    return {
        "backend": {"status": "ok", "currentTime": datetime.now(tz=settings.tzinfo)},
        "killSwitch": kill_switch,
        "featureFlags": {
            "officialInvestmentShadowEnabled": settings.official_investment_shadow_enabled,
            "officialInvestmentCutoverEnabled": settings.official_investment_cutover_enabled,
            "hybridDataEnabled": settings.hybrid_data_enabled,
            "screenerEnabled": settings.screener_enabled,
            "moneycontrolEnabled": settings.moneycontrol_enabled,
            "bseBoardMeetingsEnabled": settings.bse_board_meetings_enabled,
            "globalRiskScannerEnabled": settings.global_risk_scanner_enabled,
            "alertsEnabled": settings.alerts_enabled,
        },
        "scheduler": scheduler_payload,
        "phases": {
            "phase1": {
                "latestSnapshotDate": latest_snapshot_date,
                "quoteSyncLastRunAt": quote_sync.get("lastRunAt"),
                "weeklySyncLastRunAt": weekly_sync.get("lastRunAt"),
                "lastMarketContextDate": latest_context.as_of_date if latest_context is not None else None,
            },
            "phase2": {
                "latestScoreDate": latest_score_date,
                "strongBuyCount": sum(1 for row in latest_score_rows if row["label"] == "STRONG_BUY"),
                "watchlistCount": sum(1 for row in latest_score_rows if row["label"] == "WATCHLIST"),
            },
            "phase3": {
                "mode": "on_demand",
                "latestGateBasisDate": latest_score_date,
            },
            "phase4": {
                "cutoverEnabled": settings.official_investment_cutover_enabled,
                "latestPlanDate": cutover.get("latestPlanDate"),
                "plannedOfficialTrades": int(cutover.get("plannedCount") or 0),
            },
            "phase5": {
                "latestRiskDate": latest_risk.as_of_date if latest_risk is not None else None,
                "latestRiskLevel": latest_risk.risk_level if latest_risk is not None else None,
                "latestRiskScanType": latest_risk.scan_type if latest_risk is not None else None,
            },
            "phase6": {
                "hybridDataEnabled": settings.hybrid_data_enabled,
                "latestSummaryGeneratedAt": shadow_summary.get("generatedAt"),
                "averageFillRate": round(mean(latest_fill_rates), 4) if latest_fill_rates else None,
                "symbolsBelow80FillRate": sum(1 for value in latest_fill_rates if value < 0.8),
            },
        },
        "coverage": {
            "latestSnapshotCount": latest_snapshot_count,
            "averageFillRate": round(mean(latest_fill_rates), 4) if latest_fill_rates else None,
            "minFillRate": round(min(latest_fill_rates), 4) if latest_fill_rates else None,
            "maxFillRate": round(max(latest_fill_rates), 4) if latest_fill_rates else None,
            "symbolsBelow80FillRate": sum(1 for value in latest_fill_rates if value < 0.8),
            "shadowCoverage": shadow_summary,
        },
        "syncState": {
            "news": news_sync,
            "fundamentals": fundamentals_sync,
            "botConfigRows": None if db.scalar(select(BotConfig).limit(1)) is None else "available",
        },
    }
