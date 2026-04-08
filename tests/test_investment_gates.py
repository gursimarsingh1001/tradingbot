from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete

from backend.data.historical_fetcher import SymbolConfig
from backend.db.models_investment import OfficialInvestmentSnapshot, OfficialMarketContextSnapshot
from backend.db.postgres import LynchScore, MinerviniScore, PiotroskiScore, init_postgres, session_scope
from backend.engine.earnings_proximity_gate import EarningsProximityGate
from backend.engine.entry_trigger import EntryTrigger
from backend.engine.investment_gate_runner import InvestmentGateRunner
from backend.engine.market_health_gate import MarketHealthGate
from backend.engine.promoter_gate import PromoterGate
from backend.engine.sector_strength_gate import SectorStrengthGate
from backend.scheduler import TradingSchedulerService


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


def _make_breakout_frame(*, breakout: bool = True, strong_volume: bool = True, periods: int = 30) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=periods, freq="B", tz="Asia/Kolkata")
    highs = [100.0] * (periods - 1)
    closes = [95.0] * (periods - 1)
    volumes = [1000.0] * (periods - 1)
    highs.append(102.0)
    closes.append(105.0 if breakout else 99.0)
    volumes.append(2000.0 if strong_volume else 1200.0)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs,
            "Low": [90.0] * periods,
            "Close": closes,
            "Volume": volumes,
        },
        index=index,
    )


def test_market_health_gate_blocks_bear_market_and_high_vix():
    result = MarketHealthGate.evaluate(
        OfficialMarketContextSnapshot(
            as_of_date=date.fromisoformat("2026-04-21"),
            nifty50_close=100.0,
            nifty50_sma200=120.0,
            india_vix=28.0,
        )
    )

    assert result.passed is False
    assert result.code == "market_health_blocked"
    assert "nifty50_below_sma200" in result.details["reasons"]
    assert "india_vix_above_25" in result.details["reasons"]


def test_market_health_gate_blocks_vix_velocity_spike():
    current = OfficialMarketContextSnapshot(
        as_of_date=date.fromisoformat("2026-04-21"),
        nifty50_close=25000.0,
        nifty50_sma200=23000.0,
        india_vix=32.0,
    )
    history = [
        current,
        OfficialMarketContextSnapshot(as_of_date=date.fromisoformat("2026-04-18"), india_vix=20.0),
        OfficialMarketContextSnapshot(as_of_date=date.fromisoformat("2026-04-17"), india_vix=20.0),
        OfficialMarketContextSnapshot(as_of_date=date.fromisoformat("2026-04-16"), india_vix=21.0),
        OfficialMarketContextSnapshot(as_of_date=date.fromisoformat("2026-04-15"), india_vix=19.0),
        OfficialMarketContextSnapshot(as_of_date=date.fromisoformat("2026-04-14"), india_vix=20.0),
    ]

    result = MarketHealthGate.evaluate(current, recent_contexts=history)

    assert result.passed is False
    assert "india_vix_velocity_block" in result.details["reasons"]
    assert result.details["vix_velocity_severity"] == "BLOCK"


def test_sector_strength_gate_blocks_when_sector_is_below_sma50():
    snapshot = OfficialInvestmentSnapshot(
        symbol="BANKTEST",
        as_of_date=date.fromisoformat("2026-04-21"),
        sector="BANKING",
    )
    context = OfficialMarketContextSnapshot(
        as_of_date=date.fromisoformat("2026-04-21"),
        sector_context={"BANKING": {"close": 95.0, "sma50": 100.0}},
    )

    result = SectorStrengthGate.evaluate(snapshot, context)

    assert result.passed is False
    assert result.code == "sector_below_sma50"


def test_earnings_proximity_gate_blocks_known_earnings_and_passes_unknown():
    as_of_date = date.fromisoformat("2026-04-21")
    blocked = EarningsProximityGate.evaluate(
        OfficialInvestmentSnapshot(
            symbol="EARNTST",
            as_of_date=as_of_date,
            earnings_date=as_of_date + timedelta(days=3),
        ),
        as_of_date,
    )
    unknown = EarningsProximityGate.evaluate(
        OfficialInvestmentSnapshot(
            symbol="EARNTST2",
            as_of_date=as_of_date,
            earnings_date=None,
        ),
        as_of_date,
    )

    assert blocked.passed is False
    assert blocked.code == "earnings_within_7_days"
    assert unknown.passed is True
    assert unknown.code == "earnings_date_unknown"


def test_promoter_gate_blocks_low_holding_high_pledge_and_large_drop():
    result = PromoterGate.evaluate(
        OfficialInvestmentSnapshot(
            symbol="PROMOTER",
            as_of_date=date.fromisoformat("2026-04-21"),
            promoter_holding=20.0,
            promoter_pledge=30.0,
            promoter_holding_change_pct=-4.0,
        )
    )

    assert result.passed is False
    assert result.code == "promoter_gate_blocked"
    assert "promoter_holding_below_26" in result.details["reasons"]
    assert "promoter_pledge_above_25" in result.details["reasons"]
    assert "promoter_holding_drop_gt_3" in result.details["reasons"]


def test_entry_trigger_requires_breakout_and_volume_confirmation():
    as_of_date = date.fromisoformat("2026-04-21")
    passing = EntryTrigger.evaluate("BREAKOUT", _make_breakout_frame(), as_of_date)
    failing = EntryTrigger.evaluate("NOBREAK", _make_breakout_frame(breakout=False, strong_volume=False), as_of_date)

    assert passing.passed is True
    assert passing.code == "entry_trigger_pass"
    assert failing.passed is False
    assert failing.code == "entry_trigger_not_confirmed"
    assert "close_not_above_prior_20_day_high" in failing.details["reasons"]
    assert "volume_not_above_1.5x_average" in failing.details["reasons"]


def test_investment_gate_runner_skips_non_strong_buys_and_returns_buy_or_skip():
    init_postgres()
    as_of_date = date.fromisoformat("2026-04-21")
    strong_pass = "ZZPHASE3PASS"
    strong_fail = "ZZPHASE3FAIL"
    weak_symbol = "ZZPHASE3WEAK"

    with session_scope() as session:
        session.execute(delete(LynchScore).where(LynchScore.symbol.in_([strong_pass, strong_fail, weak_symbol])))
        session.execute(delete(PiotroskiScore).where(PiotroskiScore.symbol.in_([strong_pass, strong_fail, weak_symbol])))
        session.execute(delete(MinerviniScore).where(MinerviniScore.symbol.in_([strong_pass, strong_fail, weak_symbol])))
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol.in_([strong_pass, strong_fail, weak_symbol])))
        session.execute(delete(OfficialMarketContextSnapshot).where(OfficialMarketContextSnapshot.as_of_date == as_of_date))

        session.add(
            OfficialMarketContextSnapshot(
                as_of_date=as_of_date,
                nifty50_close=25000.0,
                nifty50_sma200=23000.0,
                india_vix=18.0,
                sector_context={"BANKING": {"close": 105.0, "sma50": 100.0}},
            )
        )
        session.add_all(
            [
                OfficialInvestmentSnapshot(
                    symbol=strong_pass,
                    as_of_date=as_of_date,
                    company_name="Pass Co",
                    sector="BANKING",
                    earnings_date=as_of_date + timedelta(days=20),
                    promoter_holding=55.0,
                    promoter_pledge=0.0,
                    promoter_holding_change_pct=0.5,
                ),
                OfficialInvestmentSnapshot(
                    symbol=strong_fail,
                    as_of_date=as_of_date,
                    company_name="Fail Co",
                    sector="BANKING",
                    earnings_date=as_of_date + timedelta(days=2),
                    promoter_holding=20.0,
                    promoter_pledge=30.0,
                    promoter_holding_change_pct=-4.5,
                ),
                OfficialInvestmentSnapshot(
                    symbol=weak_symbol,
                    as_of_date=as_of_date,
                    company_name="Weak Co",
                    sector="BANKING",
                    earnings_date=as_of_date + timedelta(days=20),
                    promoter_holding=55.0,
                    promoter_pledge=0.0,
                    promoter_holding_change_pct=0.5,
                ),
            ]
        )
        for symbol in (strong_pass, strong_fail):
            session.add(LynchScore(symbol=symbol, as_of_date=as_of_date, vote_yes=True, data_complete=True))
            session.add(PiotroskiScore(symbol=symbol, as_of_date=as_of_date, vote_yes=True, data_complete=True))
            session.add(MinerviniScore(symbol=symbol, as_of_date=as_of_date, vote_yes=True, data_complete=True))
        session.add(LynchScore(symbol=weak_symbol, as_of_date=as_of_date, vote_yes=True, data_complete=True))
        session.add(PiotroskiScore(symbol=weak_symbol, as_of_date=as_of_date, vote_yes=False, data_complete=True))
        session.add(MinerviniScore(symbol=weak_symbol, as_of_date=as_of_date, vote_yes=False, data_complete=True))

    runner = InvestmentGateRunner(
        historical_fetcher=_FakeHistoricalFetcher(
            {
                strong_pass: _make_breakout_frame(),
                strong_fail: _make_breakout_frame(breakout=False, strong_volume=False),
                weak_symbol: _make_breakout_frame(),
            }
        )
    )
    result = runner.run_universe(symbols=[strong_pass, strong_fail, weak_symbol], as_of_date=as_of_date)

    assert result["eligible_strong_buy"] == 2
    assert result["processed"] == 2
    assert result["skipped_non_strong_buy"] == 1
    assert result["buy"] == 1
    assert result["skip"] == 1
    assert result["blocked_by_earnings_proximity"] == 1
    assert result["blocked_by_promoter"] == 1
    assert result["blocked_by_entry_trigger"] == 1

    pass_decision = next(item for item in result["results"] if item.symbol == strong_pass)
    fail_decision = next(item for item in result["results"] if item.symbol == strong_fail)
    assert pass_decision.decision == "BUY"
    assert fail_decision.decision == "SKIP"
    assert "earnings_within_7_days" in fail_decision.failure_reasons
    assert "promoter_gate_blocked" in fail_decision.failure_reasons
    assert "entry_trigger_not_confirmed" in fail_decision.failure_reasons

    with session_scope() as session:
        session.execute(delete(LynchScore).where(LynchScore.symbol.in_([strong_pass, strong_fail, weak_symbol])))
        session.execute(delete(PiotroskiScore).where(PiotroskiScore.symbol.in_([strong_pass, strong_fail, weak_symbol])))
        session.execute(delete(MinerviniScore).where(MinerviniScore.symbol.in_([strong_pass, strong_fail, weak_symbol])))
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol.in_([strong_pass, strong_fail, weak_symbol])))
        session.execute(delete(OfficialMarketContextSnapshot).where(OfficialMarketContextSnapshot.as_of_date == as_of_date))


def test_scheduler_phase3_hook_runs_after_phase2_scoring(monkeypatch):
    as_of_date = date.fromisoformat("2026-04-21")

    class _FakeInvestmentScorer:
        def score_universe(self, as_of_date=None):  # noqa: ANN001
            return {
                "as_of_date": str(as_of_date or "2026-04-21"),
                "processed": 3,
                "strong_buy": 2,
                "watchlist": 1,
                "no_action": 0,
            }

    class _FakeInvestmentGateRunner:
        def run_universe(self, symbols=None, as_of_date=None):  # noqa: ANN001
            return {
                "as_of_date": str(as_of_date or "2026-04-21"),
                "eligible_strong_buy": 2,
                "processed": 2,
                "buy": 1,
                "skip": 1,
                "blocked_by_market_health": 0,
                "blocked_by_sector_strength": 1,
                "blocked_by_earnings_proximity": 0,
                "blocked_by_promoter": 0,
                "blocked_by_entry_trigger": 1,
                "results": [],
            }

    @contextmanager
    def _fake_session_scope():
        yield object()

    monkeypatch.setattr("backend.scheduler.session_scope", _fake_session_scope)
    monkeypatch.setattr("backend.scheduler.add_notification", lambda *args, **kwargs: None)

    service = TradingSchedulerService.__new__(TradingSchedulerService)
    service.investment_scorer = _FakeInvestmentScorer()
    service.investment_gate_runner = _FakeInvestmentGateRunner()

    result = TradingSchedulerService.refresh_official_investment_scores_shadow(service, as_of_date=as_of_date)

    assert result["strong_buy"] == 2
    assert result["phase3"]["eligible_strong_buy"] == 2
    assert result["phase3"]["buy"] == 1
