from __future__ import annotations

from datetime import date

from sqlalchemy import delete

from backend.api import investment_api
from backend.api.investment_api import (
    get_cutover_latest,
    get_market_indices,
    get_scoring_detail,
    get_scoring_summary,
    get_scoring_universe,
    get_system_status,
)
from backend.db.postgres import (
    LynchScore,
    MinerviniScore,
    OfficialInvestmentSnapshot,
    OfficialMarketContextSnapshot,
    PaperTrade,
    PiotroskiScore,
    init_postgres,
    session_scope,
)


def _cleanup(symbols: list[str], as_of_date: date) -> None:
    with session_scope() as session:
        session.execute(delete(LynchScore).where(LynchScore.symbol.in_(symbols)))
        session.execute(delete(PiotroskiScore).where(PiotroskiScore.symbol.in_(symbols)))
        session.execute(delete(MinerviniScore).where(MinerviniScore.symbol.in_(symbols)))
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol.in_(symbols)))
        session.execute(delete(PaperTrade).where(PaperTrade.stock_symbol.in_(symbols)))
        session.execute(delete(OfficialMarketContextSnapshot).where(OfficialMarketContextSnapshot.as_of_date == as_of_date))


def test_scoring_api_shapes_return_expected_snapshot_and_detail(monkeypatch):
    init_postgres()
    symbol = "ZZAPI01"
    as_of_date = date.fromisoformat("2026-04-08")
    _cleanup([symbol], as_of_date)
    monkeypatch.setattr(investment_api, "_is_known_dashboard_symbol", lambda value: str(value).upper() == symbol)

    try:
        with session_scope() as session:
            session.add(
                OfficialInvestmentSnapshot(
                    symbol=symbol,
                    company_name="API Test Limited",
                    sector="IT",
                    as_of_date=as_of_date,
                    pe_ratio=18.5,
                    pb_ratio=4.2,
                    market_cap=1500000000.0,
                    dividend_yield=1.2,
                    eps_growth_3y_cagr=24.0,
                    data_sources={"fill_rate": 0.91, "reconciled_at": "2026-04-08T18:00:00"},
                )
            )
            session.add(LynchScore(symbol=symbol, as_of_date=as_of_date, lynch_value=1.8, vote_yes=True, data_complete=True))
            session.add(PiotroskiScore(symbol=symbol, as_of_date=as_of_date, f_score=8, vote_yes=True, data_complete=True))
            session.add(MinerviniScore(symbol=symbol, as_of_date=as_of_date, passed_checks=8, rs_percentile=82.0, vote_yes=True, data_complete=True))

        with session_scope() as session:
            summary = get_scoring_summary(as_of_date=as_of_date, db=session)
            universe = get_scoring_universe(as_of_date=as_of_date, db=session)
            detail = get_scoring_detail(symbol=symbol, as_of_date=as_of_date, db=session)

        assert summary["counts"]["strongBuy"] >= 1
        assert universe["rows"][0]["symbol"] == symbol
        assert universe["rows"][0]["label"] == "STRONG_BUY"
        assert detail["symbol"] == symbol
        assert detail["snapshot"]["dataSources"]["fill_rate"] == 0.91
    finally:
        _cleanup([symbol], as_of_date)


def test_cutover_market_and_system_status_expose_official_dashboard_data(monkeypatch):
    init_postgres()
    symbol = "ZZAPI02"
    as_of_date = date.fromisoformat("2026-04-08")
    plan_date = date.fromisoformat("2026-04-09")
    _cleanup([symbol], as_of_date)
    monkeypatch.setattr(investment_api, "_is_known_dashboard_symbol", lambda value: str(value).upper() == symbol)

    try:
        with session_scope() as session:
            session.add(
                OfficialInvestmentSnapshot(
                    symbol=symbol,
                    company_name="Cutover Test Limited",
                    sector="BANKING",
                    as_of_date=as_of_date,
                    data_sources={"fill_rate": 0.87},
                )
            )
            session.add(
                PaperTrade(
                    stock_symbol=symbol,
                    strategy_name="Official Breakout Cutover",
                    signal_type="INVESTMENT",
                    entry_date=plan_date,
                    entry_price=100.0,
                    stop_loss=93.0,
                    target_1=114.0,
                    target_2=128.0,
                    target_3=142.0,
                    shares=10,
                    confidence_score=76.0,
                    metadata_json={
                        "plan_only": True,
                        "plan_status": "PLANNED",
                        "source_kind": "official_investment_cutover",
                        "global_risk_level": "GREEN",
                        "position_size_multiplier": 1.0,
                    },
                )
            )
            session.add(
                OfficialMarketContextSnapshot(
                    as_of_date=as_of_date,
                    nifty50_close=25000.0,
                    nifty50_sma200=23000.0,
                    india_vix=18.0,
                    aaa_bond_yield=7.4,
                    sector_context={"BANKING": {"close": 105.0, "sma50": 100.0}},
                )
            )

        with session_scope() as session:
            cutover = get_cutover_latest(db=session)
            market = get_market_indices(db=session)
            system_status = get_system_status(db=session)

        assert cutover["plannedCount"] >= 1
        assert cutover["signals"][0]["stockSymbol"] == symbol
        assert cutover["latestPlanDate"] == plan_date
        assert market["indices"][0]["key"] == "NIFTY50"
        assert system_status["phases"]["phase4"]["plannedOfficialTrades"] >= 1
        assert system_status["coverage"]["averageFillRate"] is not None
    finally:
        _cleanup([symbol], as_of_date)
