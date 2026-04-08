from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import delete, select

from backend.data.historical_fetcher import SymbolConfig
from backend.db.models_investment import OfficialFinancialPeriod, OfficialInvestmentSnapshot
from backend.db.postgres import LynchScore, MinerviniScore, PiotroskiScore, init_postgres, session_scope
from backend.engine.investment_scorer import CombinedInvestmentScore, InvestmentScorer
from backend.engine.lynch_peg_scorer import LynchPegScorer
from backend.engine.minervini_scorer import MinerviniScorer
from backend.engine.piotroski_scorer import PiotroskiScorer


class _FakeHistoricalFetcher:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def load_symbol_map(self) -> dict[str, SymbolConfig]:
        return {
            symbol: SymbolConfig(
                symbol=symbol,
                token=f"TOKEN-{symbol}",
                company_name=f"{symbol} Limited",
                exchange="NSE",
                trading_symbol=f"{symbol}-EQ",
            )
            for symbol in self.frames
        }

    def fetch_symbol_frame(self, symbol_config: SymbolConfig) -> pd.DataFrame:
        return self.frames.get(symbol_config.symbol, pd.DataFrame())


def _make_trending_frame(symbol: str, periods: int = 260) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=periods, freq="B", tz="Asia/Kolkata")
    close = pd.Series([100.0 + (idx * 0.55) for idx in range(periods)], index=index)
    frame = pd.DataFrame(
        {
            "Open": close * 0.995,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=index,
    )
    frame["SMA_50"] = frame["Close"].rolling(50).mean()
    frame["SMA_200"] = frame["Close"].rolling(200).mean()
    return frame


def _make_short_frame(periods: int = 100) -> pd.DataFrame:
    index = pd.date_range("2025-10-01", periods=periods, freq="B", tz="Asia/Kolkata")
    close = pd.Series([100.0 + (idx * 0.05) for idx in range(periods)], index=index)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 100_000,
        },
        index=index,
    )


def test_lynch_peg_scorer_threshold_and_invalid_pe():
    as_of_date = date.fromisoformat("2026-04-07")
    passing = OfficialInvestmentSnapshot(
        symbol="LYNCHPASS",
        as_of_date=as_of_date,
        eps_growth_3y_cagr=24.0,
        dividend_yield=1.2,
        pe_ratio=10.0,
    )
    failing = OfficialInvestmentSnapshot(
        symbol="LYNCHFAIL",
        as_of_date=as_of_date,
        eps_growth_3y_cagr=24.0,
        dividend_yield=1.2,
        pe_ratio=0.0,
    )

    passing_result = LynchPegScorer.score(passing, as_of_date)
    failing_result = LynchPegScorer.score(failing, as_of_date)

    assert round(float(passing_result.lynch_value or 0.0), 2) == 2.52
    assert passing_result.vote_yes is True
    assert failing_result.vote_yes is False
    assert failing_result.data_complete is False
    assert "pe_ratio_non_positive" in failing_result.missing_fields


def test_piotroski_scorer_handles_pass_and_missing_inputs():
    as_of_date = date.fromisoformat("2026-04-07")
    snapshot = OfficialInvestmentSnapshot(symbol="PIOTRO", as_of_date=as_of_date)
    latest = OfficialFinancialPeriod(
        symbol="PIOTRO",
        period_type="ANNUAL",
        period_end=date.fromisoformat("2025-03-31"),
        operating_cash_flow=220.0,
        net_profit=180.0,
        total_debt=100.0,
        current_assets=500.0,
        current_liabilities=200.0,
        gross_margin=0.45,
        asset_turnover=1.4,
        roa=0.12,
        shares_outstanding=100.0,
    )
    prior = OfficialFinancialPeriod(
        symbol="PIOTRO",
        period_type="ANNUAL",
        period_end=date.fromisoformat("2024-03-31"),
        operating_cash_flow=150.0,
        net_profit=160.0,
        total_debt=140.0,
        current_assets=420.0,
        current_liabilities=210.0,
        gross_margin=0.39,
        asset_turnover=1.1,
        roa=0.08,
        shares_outstanding=105.0,
    )

    passing = PiotroskiScorer.score(snapshot, [latest, prior], as_of_date)
    missing = PiotroskiScorer.score(snapshot, [latest], as_of_date)

    assert passing.f_score == 9
    assert passing.vote_yes is True
    assert missing.vote_yes is False
    assert missing.data_complete is False
    assert "prior_annual_period" in missing.missing_fields


def test_piotroski_scorer_derives_roa_and_asset_turnover_from_total_assets():
    as_of_date = date.fromisoformat("2026-04-07")
    snapshot = OfficialInvestmentSnapshot(symbol="PIOTRODERIVED", as_of_date=as_of_date)
    latest = OfficialFinancialPeriod(
        symbol="PIOTRODERIVED",
        period_type="ANNUAL",
        period_end=date.fromisoformat("2025-03-31"),
        revenue=560.0,
        operating_cash_flow=220.0,
        net_profit=180.0,
        total_debt=100.0,
        total_assets=400.0,
        current_assets=500.0,
        current_liabilities=200.0,
        gross_margin=0.45,
        roa=None,
        asset_turnover=None,
        shares_outstanding=100.0,
    )
    prior = OfficialFinancialPeriod(
        symbol="PIOTRODERIVED",
        period_type="ANNUAL",
        period_end=date.fromisoformat("2024-03-31"),
        revenue=330.0,
        operating_cash_flow=150.0,
        net_profit=60.0,
        total_debt=140.0,
        total_assets=300.0,
        current_assets=420.0,
        current_liabilities=210.0,
        gross_margin=0.39,
        roa=None,
        asset_turnover=None,
        shares_outstanding=105.0,
    )

    result = PiotroskiScorer.score(snapshot, [latest, prior], as_of_date)

    assert result.vote_yes is True
    assert result.f_score == 9
    assert result.signals_json["positive_roa"]["pass"] is True
    assert result.signals_json["roa_improved"]["pass"] is True
    assert result.signals_json["higher_asset_turnover"]["pass"] is True


def test_minervini_scorer_requires_all_checks_and_rs_threshold():
    as_of_date = date.fromisoformat("2026-04-07")
    frame = _make_trending_frame("MINERVINI")

    passing = MinerviniScorer.score("MINERVINI", frame, 88.0, as_of_date)
    failing = MinerviniScorer.score("MINERVINI", _make_short_frame(), None, as_of_date)

    assert passing.passed_checks == 8
    assert passing.vote_yes is True
    assert failing.vote_yes is False
    assert failing.data_complete is False
    assert "lookback_252_sessions" in failing.missing_fields


def test_investment_scorer_writes_component_rows_and_is_idempotent():
    init_postgres()
    as_of_date = date.fromisoformat("2026-04-07")
    strong_symbol = "ZZPHASE2STRONG"
    weak_symbol = "ZZPHASE2WEAK"

    with session_scope() as session:
        session.execute(delete(LynchScore).where(LynchScore.symbol.in_([strong_symbol, weak_symbol])))
        session.execute(delete(PiotroskiScore).where(PiotroskiScore.symbol.in_([strong_symbol, weak_symbol])))
        session.execute(delete(MinerviniScore).where(MinerviniScore.symbol.in_([strong_symbol, weak_symbol])))
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol.in_([strong_symbol, weak_symbol])))
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol.in_([strong_symbol, weak_symbol])))

        session.add(
            OfficialInvestmentSnapshot(
                symbol=strong_symbol,
                as_of_date=as_of_date,
                company_name="Strong Co",
                eps_growth_3y_cagr=24.0,
                dividend_yield=1.2,
                pe_ratio=10.0,
            )
        )
        session.add(
            OfficialInvestmentSnapshot(
                symbol=weak_symbol,
                as_of_date=as_of_date,
                company_name="Weak Co",
                eps_growth_3y_cagr=None,
                dividend_yield=None,
                pe_ratio=None,
            )
        )
        session.add_all(
            [
                OfficialFinancialPeriod(
                    symbol=strong_symbol,
                    period_type="ANNUAL",
                    period_end=date.fromisoformat("2025-03-31"),
                    operating_cash_flow=220.0,
                    net_profit=180.0,
                    total_debt=100.0,
                    current_assets=500.0,
                    current_liabilities=200.0,
                    gross_margin=0.45,
                    asset_turnover=1.4,
                    roa=0.12,
                    shares_outstanding=100.0,
                ),
                OfficialFinancialPeriod(
                    symbol=strong_symbol,
                    period_type="ANNUAL",
                    period_end=date.fromisoformat("2024-03-31"),
                    operating_cash_flow=150.0,
                    net_profit=160.0,
                    total_debt=140.0,
                    current_assets=420.0,
                    current_liabilities=210.0,
                    gross_margin=0.39,
                    asset_turnover=1.1,
                    roa=0.08,
                    shares_outstanding=105.0,
                ),
            ]
        )

    scorer = InvestmentScorer(
        historical_fetcher=_FakeHistoricalFetcher(
            {
                strong_symbol: _make_trending_frame(strong_symbol),
                weak_symbol: _make_short_frame(),
            }
        )
    )

    first = scorer.score_universe(symbols=[strong_symbol, weak_symbol], as_of_date=as_of_date)
    second = scorer.score_universe(symbols=[strong_symbol, weak_symbol], as_of_date=as_of_date)

    strong_result = next(result for result in first["results"] if result.symbol == strong_symbol)
    weak_result = next(result for result in first["results"] if result.symbol == weak_symbol)

    assert isinstance(strong_result, CombinedInvestmentScore)
    assert strong_result.label == "STRONG_BUY"
    assert weak_result.label == "NO_ACTION"
    assert first["strong_buy"] == 1
    assert second["processed"] == 2

    with session_scope() as session:
        lynch_rows = session.scalars(select(LynchScore).where(LynchScore.symbol.in_([strong_symbol, weak_symbol]))).all()
        piotroski_rows = session.scalars(select(PiotroskiScore).where(PiotroskiScore.symbol.in_([strong_symbol, weak_symbol]))).all()
        minervini_rows = session.scalars(select(MinerviniScore).where(MinerviniScore.symbol.in_([strong_symbol, weak_symbol]))).all()

    assert len(lynch_rows) == 2
    assert len(piotroski_rows) == 2
    assert len(minervini_rows) == 2
    assert next(row for row in lynch_rows if row.symbol == weak_symbol).data_complete is False

    with session_scope() as session:
        session.execute(delete(LynchScore).where(LynchScore.symbol.in_([strong_symbol, weak_symbol])))
        session.execute(delete(PiotroskiScore).where(PiotroskiScore.symbol.in_([strong_symbol, weak_symbol])))
        session.execute(delete(MinerviniScore).where(MinerviniScore.symbol.in_([strong_symbol, weak_symbol])))
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol.in_([strong_symbol, weak_symbol])))
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol.in_([strong_symbol, weak_symbol])))
