from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import delete, select

from backend.data.historical_fetcher import SymbolConfig
from backend.db.models_investment import OfficialInvestmentSnapshot
from backend.db.postgres import LynchScore, MinerviniScore, PaperTrade, PiotroskiScore, init_postgres, session_scope
from backend.engine.global_risk_types import GlobalRiskResult, SignalResult
from backend.engine.investment_gate_types import InvestmentGateDecision
from backend.engine.official_investment_recommendation_engine import OfficialInvestmentRecommendationEngine
from backend.scheduler import TradingSchedulerService, settings as scheduler_settings


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


class _FakeGateRunner:
    def __init__(self, summary: dict[str, object]):
        self.summary = summary

    def run_universe(self, as_of_date=None):  # noqa: ANN001
        return self.summary


class _FakeRiskScanner:
    def __init__(
        self,
        *,
        risk_level: str = "GREEN",
        position_size_multiplier: float | None = None,
        signals: list[SignalResult] | None = None,
        summary_message: str | None = None,
    ) -> None:
        default_multiplier = {"GREEN": 1.0, "YELLOW": 0.5, "RED": 0.0}[risk_level]
        self.risk_level = risk_level
        self.position_size_multiplier = (
            float(position_size_multiplier) if position_size_multiplier is not None else default_multiplier
        )
        self.signals = signals or []
        self.summary_message = summary_message or f"{risk_level} risk summary"
        self.calls: list[tuple[date, str]] = []

    def scan(self, as_of_date: date, scan_type: str = "AFTER_MARKET") -> GlobalRiskResult:
        self.calls.append((as_of_date, scan_type))
        return GlobalRiskResult(
            as_of_date=as_of_date,
            scan_type=scan_type,
            risk_level=self.risk_level,
            position_size_multiplier=self.position_size_multiplier,
            signals=list(self.signals),
            active_caution_count=sum(1 for signal in self.signals if signal.severity == "CAUTION"),
            active_block_count=sum(1 for signal in self.signals if signal.severity == "BLOCK"),
            summary_message=self.summary_message,
        )


def _make_breakout_frame(*, high: float, low: float, close: float, volume_ratio: float, periods: int = 30) -> pd.DataFrame:
    index = pd.date_range("2026-03-01", periods=periods, freq="B", tz="Asia/Kolkata")
    base_highs = [high - 4.0] * (periods - 1)
    base_closes = [close - 6.0] * (periods - 1)
    base_volumes = [1_000_000.0] * (periods - 1)
    latest_volume = 1_000_000.0 * volume_ratio
    return pd.DataFrame(
        {
            "Open": base_closes + [close - 1.0],
            "High": base_highs + [high],
            "Low": [low + 1.0] * (periods - 1) + [low],
            "Close": base_closes + [close],
            "Volume": base_volumes + [latest_volume],
        },
        index=index,
    )


def _seed_snapshot_and_scores(
    *,
    symbol: str,
    as_of_date: date,
    lynch_value: float,
    f_score: int,
    rs_percentile: float,
) -> None:
    with session_scope() as session:
        session.add(
            OfficialInvestmentSnapshot(
                symbol=symbol,
                as_of_date=as_of_date,
                company_name=f"{symbol} Limited",
                sector="BANKING",
                earnings_date=as_of_date.replace(day=min(as_of_date.day + 20, 28)),
                promoter_holding=55.0,
                promoter_pledge=0.0,
                promoter_holding_change_pct=0.5,
                eps_growth_3y_cagr=20.0,
                dividend_yield=1.0,
                pe_ratio=12.0,
            )
        )
        session.add(
            LynchScore(
                symbol=symbol,
                as_of_date=as_of_date,
                lynch_value=lynch_value,
                eps_growth_3y_cagr=20.0,
                dividend_yield=1.0,
                pe_ratio=12.0,
                vote_yes=True,
                data_complete=True,
            )
        )
        session.add(
            PiotroskiScore(
                symbol=symbol,
                as_of_date=as_of_date,
                f_score=f_score,
                vote_yes=True,
                data_complete=True,
            )
        )
        session.add(
            MinerviniScore(
                symbol=symbol,
                as_of_date=as_of_date,
                passed_checks=8,
                vote_yes=True,
                rs_percentile=rs_percentile,
                data_complete=True,
            )
        )


def _cleanup_symbols(symbols: list[str]) -> None:
    with session_scope() as session:
        session.execute(delete(LynchScore).where(LynchScore.symbol.in_(symbols)))
        session.execute(delete(PiotroskiScore).where(PiotroskiScore.symbol.in_(symbols)))
        session.execute(delete(MinerviniScore).where(MinerviniScore.symbol.in_(symbols)))
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol.in_(symbols)))
        session.execute(delete(PaperTrade).where(PaperTrade.stock_symbol.in_(symbols)))


def _gate_decision(symbol: str, as_of_date: date, *, decision: str = "BUY") -> InvestmentGateDecision:
    return InvestmentGateDecision(
        symbol=symbol,
        as_of_date=as_of_date,
        phase2_label="STRONG_BUY",
        phase2_votes_yes=3,
        decision=decision,
        all_gates_passed=decision == "BUY",
        market_health_passed=decision == "BUY",
        sector_strength_passed=decision == "BUY",
        earnings_proximity_passed=decision == "BUY",
        promoter_passed=decision == "BUY",
        entry_trigger_passed=decision == "BUY",
        failure_reasons=[] if decision == "BUY" else ["entry_trigger_not_confirmed"],
        debug_payload={
            "market_health": {"message": "Market health supports new investment buys.", "details": {"nifty50_close": 25000.0, "nifty50_sma200": 23000.0, "india_vix": 18.0}},
            "sector_strength": {"message": "Sector index is above its SMA50.", "details": {"sector": "BANKING", "close": 105.0, "sma50": 100.0}},
            "earnings_proximity": {"message": "No near-term earnings risk was detected.", "details": {}},
            "promoter": {"message": "Promoter quality checks passed.", "details": {}},
            "entry_trigger": {"message": "The Minervini breakout confirmation fired.", "details": {"prior_20_day_high": 100.0, "volume_ratio": 2.0}},
        },
    )


def test_cutover_strict_zero_clears_only_investment_plans():
    init_postgres()
    as_of_date = date.fromisoformat("2026-04-21")
    symbol = "ZZCUTZERO"
    _cleanup_symbols([symbol, "ZZINTRAPLAN"])
    _seed_snapshot_and_scores(symbol=symbol, as_of_date=as_of_date, lynch_value=2.0, f_score=8, rs_percentile=82.0)

    paper_trader = OfficialInvestmentRecommendationEngine().paper_trader
    next_session = paper_trader.market_calendar.next_trading_day(as_of_date)
    with session_scope() as session:
        session.add(
            PaperTrade(
                stock_symbol=symbol,
                strategy_name="Old Official Plan",
                signal_type="INVESTMENT",
                entry_date=next_session,
                entry_price=100.0,
                stop_loss=93.0,
                target_1=114.0,
                target_2=128.0,
                target_3=142.0,
                shares=10,
                confidence_score=70.0,
                metadata_json={"plan_only": True, "plan_status": "PLANNED"},
            )
        )
        session.add(
            PaperTrade(
                stock_symbol="ZZINTRAPLAN",
                strategy_name="Tomorrow Watchlist",
                signal_type="INTRADAY",
                entry_date=next_session,
                entry_price=100.0,
                stop_loss=98.0,
                target_1=102.0,
                target_2=104.0,
                target_3=106.0,
                shares=10,
                confidence_score=70.0,
                metadata_json={"plan_only": True, "plan_status": "PLANNED"},
            )
        )

    engine = OfficialInvestmentRecommendationEngine(
        historical_fetcher=_FakeHistoricalFetcher({}),
        gate_runner=_FakeGateRunner(
            {
                "as_of_date": as_of_date.isoformat(),
                "eligible_strong_buy": 1,
                "buy": 0,
                "blocked_by_market_health": 0,
                "blocked_by_sector_strength": 0,
                "blocked_by_earnings_proximity": 0,
                "blocked_by_promoter": 0,
                "blocked_by_entry_trigger": 1,
                "results": [],
            }
        ),
        risk_scanner=_FakeRiskScanner(),
    )
    result = engine.rebuild_planned_recommendations(as_of_date=as_of_date)

    assert result.created == 0
    assert result.cleared_existing_plans >= 1
    assert result.recommendations == []

    with session_scope() as session:
        remaining_investment_plans = session.scalars(
            select(PaperTrade).where(PaperTrade.stock_symbol == symbol, PaperTrade.exit_date.is_(None))
        ).all()
        remaining_intraday_plans = session.scalars(
            select(PaperTrade).where(PaperTrade.stock_symbol == "ZZINTRAPLAN", PaperTrade.exit_date.is_(None))
        ).all()

    assert remaining_investment_plans == []
    assert len(remaining_intraday_plans) == 1
    assert (remaining_intraday_plans[0].metadata_json or {}).get("plan_only") is True

    _cleanup_symbols([symbol, "ZZINTRAPLAN"])


def test_cutover_ranks_phase3_buys_and_excludes_existing_open_positions():
    init_postgres()
    as_of_date = date.fromisoformat("2026-04-21")
    symbols = ["ZZCUTA", "ZZCUTB", "ZZCUTC"]
    _cleanup_symbols(symbols)
    _seed_snapshot_and_scores(symbol="ZZCUTA", as_of_date=as_of_date, lynch_value=2.4, f_score=9, rs_percentile=92.0)
    _seed_snapshot_and_scores(symbol="ZZCUTB", as_of_date=as_of_date, lynch_value=2.0, f_score=8, rs_percentile=84.0)
    _seed_snapshot_and_scores(symbol="ZZCUTC", as_of_date=as_of_date, lynch_value=1.9, f_score=8, rs_percentile=78.0)

    with session_scope() as session:
        session.add(
            PaperTrade(
                stock_symbol="ZZCUTB",
                strategy_name="Existing Investment",
                signal_type="INVESTMENT",
                entry_date=as_of_date,
                entry_price=100.0,
                stop_loss=92.0,
                target_1=116.0,
                target_2=132.0,
                target_3=148.0,
                shares=10,
                confidence_score=72.0,
                metadata_json={"plan_only": False, "plan_status": "OPEN"},
            )
        )

    frames = {
        "ZZCUTA": _make_breakout_frame(high=112.0, low=104.0, close=111.0, volume_ratio=2.4),
        "ZZCUTB": _make_breakout_frame(high=109.0, low=103.0, close=108.0, volume_ratio=2.0),
        "ZZCUTC": _make_breakout_frame(high=107.0, low=101.0, close=106.0, volume_ratio=1.8),
    }
    engine = OfficialInvestmentRecommendationEngine(
        historical_fetcher=_FakeHistoricalFetcher(frames),
        gate_runner=_FakeGateRunner(
            {
                "as_of_date": as_of_date.isoformat(),
                "eligible_strong_buy": 3,
                "buy": 3,
                "blocked_by_market_health": 0,
                "blocked_by_sector_strength": 0,
                "blocked_by_earnings_proximity": 0,
                "blocked_by_promoter": 0,
                "blocked_by_entry_trigger": 0,
                "results": [
                    _gate_decision("ZZCUTA", as_of_date),
                    _gate_decision("ZZCUTB", as_of_date),
                    _gate_decision("ZZCUTC", as_of_date),
                ],
            }
        ),
        risk_scanner=_FakeRiskScanner(),
    )
    result = engine.rebuild_planned_recommendations(as_of_date=as_of_date, top_n=2)

    assert result.created == 2
    assert result.skipped_existing_open == 1
    assert [item["stock_symbol"] for item in result.recommendations] == ["ZZCUTA", "ZZCUTC"]
    assert all(item["strategy_name"] == "Official Breakout Cutover" for item in result.recommendations)
    assert all(item["trigger_style"] == "BREAKOUT" for item in result.recommendations)
    assert all(65.0 <= float(item["confidence_score"]) <= 90.0 for item in result.recommendations)
    assert all(item["global_risk_level"] == "GREEN" for item in result.recommendations)
    assert result.recommendations[0]["target_1"] < result.recommendations[0]["target_2"] < result.recommendations[0]["target_3"]
    assert result.recommendations[0]["audit_payload"]["phase2"]["label"] == "STRONG_BUY"

    with session_scope() as session:
        planned_rows = session.scalars(
            select(PaperTrade).where(
                PaperTrade.signal_type == "INVESTMENT",
                PaperTrade.exit_date.is_(None),
                PaperTrade.stock_symbol.in_(symbols),
            )
        ).all()

    planned_symbols = sorted(
        row.stock_symbol for row in planned_rows if (row.metadata_json or {}).get("plan_only")
    )
    assert planned_symbols == ["ZZCUTA", "ZZCUTC"]
    assert all((row.metadata_json or {}).get("position_size_multiplier") == 1.0 for row in planned_rows if (row.metadata_json or {}).get("plan_only"))

    _cleanup_symbols(symbols)


def test_cutover_red_global_risk_blocks_and_clears_plans():
    init_postgres()
    as_of_date = date.fromisoformat("2026-04-21")
    symbol = "ZZCUTRED"
    _cleanup_symbols([symbol])
    _seed_snapshot_and_scores(symbol=symbol, as_of_date=as_of_date, lynch_value=2.3, f_score=9, rs_percentile=88.0)

    paper_trader = OfficialInvestmentRecommendationEngine().paper_trader
    next_session = paper_trader.market_calendar.next_trading_day(as_of_date)
    with session_scope() as session:
        session.add(
            PaperTrade(
                stock_symbol=symbol,
                strategy_name="Old Official Plan",
                signal_type="INVESTMENT",
                entry_date=next_session,
                entry_price=100.0,
                stop_loss=93.0,
                target_1=114.0,
                target_2=128.0,
                target_3=142.0,
                shares=10,
                confidence_score=70.0,
                metadata_json={
                    "plan_only": True,
                    "plan_status": "PLANNED",
                    "source_kind": "official_investment_cutover",
                },
            )
        )

    engine = OfficialInvestmentRecommendationEngine(
        historical_fetcher=_FakeHistoricalFetcher({symbol: _make_breakout_frame(high=112.0, low=104.0, close=111.0, volume_ratio=2.0)}),
        gate_runner=_FakeGateRunner(
            {
                "as_of_date": as_of_date.isoformat(),
                "eligible_strong_buy": 1,
                "buy": 1,
                "blocked_by_market_health": 0,
                "blocked_by_sector_strength": 0,
                "blocked_by_earnings_proximity": 0,
                "blocked_by_promoter": 0,
                "blocked_by_entry_trigger": 0,
                "results": [_gate_decision(symbol, as_of_date)],
            }
        ),
        risk_scanner=_FakeRiskScanner(
            risk_level="RED",
            position_size_multiplier=0.0,
            signals=[SignalResult("vix_velocity", "BLOCK", 55.0, 40.0, "VIX velocity spike", {})],
            summary_message="RED risk: crisis overlay blocked new investment plans.",
        ),
    )

    result = engine.rebuild_planned_recommendations(as_of_date=as_of_date)

    assert result.global_risk_level == "RED"
    assert result.created == 0
    assert result.cleared_existing_plans == 1
    assert result.active_global_signals == ["vix_velocity"]

    with session_scope() as session:
        remaining = session.scalars(
            select(PaperTrade).where(PaperTrade.stock_symbol == symbol, PaperTrade.exit_date.is_(None))
        ).all()
    assert remaining == []
    _cleanup_symbols([symbol])


def test_cutover_yellow_global_risk_halves_planned_shares():
    init_postgres()
    as_of_date = date.fromisoformat("2026-04-21")
    symbol = "ZZCUTYELLOW"
    _cleanup_symbols([symbol])
    _seed_snapshot_and_scores(symbol=symbol, as_of_date=as_of_date, lynch_value=2.2, f_score=8, rs_percentile=86.0)

    frame = _make_breakout_frame(high=112.0, low=104.0, close=111.0, volume_ratio=2.5)
    green_engine = OfficialInvestmentRecommendationEngine(
        historical_fetcher=_FakeHistoricalFetcher({symbol: frame}),
        gate_runner=_FakeGateRunner(
            {
                "as_of_date": as_of_date.isoformat(),
                "eligible_strong_buy": 1,
                "buy": 1,
                "blocked_by_market_health": 0,
                "blocked_by_sector_strength": 0,
                "blocked_by_earnings_proximity": 0,
                "blocked_by_promoter": 0,
                "blocked_by_entry_trigger": 0,
                "results": [_gate_decision(symbol, as_of_date)],
            }
        ),
        risk_scanner=_FakeRiskScanner(),
    )
    green_result = green_engine.rebuild_planned_recommendations(as_of_date=as_of_date, top_n=1)
    green_trade_id = green_result.recommendations[0]["paper_trade_id"]

    with session_scope() as session:
        green_trade = session.scalar(select(PaperTrade).where(PaperTrade.trade_id == green_trade_id))
        green_shares = int(green_trade.shares or 0)
        green_metadata = dict(green_trade.metadata_json or {})

    yellow_engine = OfficialInvestmentRecommendationEngine(
        historical_fetcher=_FakeHistoricalFetcher({symbol: frame}),
        gate_runner=_FakeGateRunner(
            {
                "as_of_date": as_of_date.isoformat(),
                "eligible_strong_buy": 1,
                "buy": 1,
                "blocked_by_market_health": 0,
                "blocked_by_sector_strength": 0,
                "blocked_by_earnings_proximity": 0,
                "blocked_by_promoter": 0,
                "blocked_by_entry_trigger": 0,
                "results": [_gate_decision(symbol, as_of_date)],
            }
        ),
        risk_scanner=_FakeRiskScanner(
            risk_level="YELLOW",
            position_size_multiplier=0.5,
            signals=[SignalResult("sp500_overnight", "CAUTION", -2.3, -2.0, "Overnight weakness", {})],
            summary_message="YELLOW risk: halve size only.",
        ),
    )
    yellow_result = yellow_engine.rebuild_planned_recommendations(as_of_date=as_of_date, top_n=1)
    yellow_trade_id = yellow_result.recommendations[0]["paper_trade_id"]

    with session_scope() as session:
        yellow_trade = session.scalar(select(PaperTrade).where(PaperTrade.trade_id == yellow_trade_id))
        yellow_shares = int(yellow_trade.shares or 0)
        yellow_metadata = dict(yellow_trade.metadata_json or {})

    assert green_shares > 0
    assert yellow_result.global_risk_level == "YELLOW"
    assert yellow_result.created == 1
    assert yellow_shares < green_shares
    assert yellow_metadata.get("position_size_multiplier") == 0.5
    assert yellow_metadata.get("global_risk_level") == "YELLOW"
    assert yellow_metadata.get("base_planned_shares") == green_metadata.get("base_planned_shares")
    _cleanup_symbols([symbol])


def test_after_market_analysis_defers_investment_generation_when_cutover_enabled(monkeypatch):
    init_postgres()
    service = TradingSchedulerService()
    calls: dict[str, object] = {"generate_called": False, "clear_signal_type": None}

    monkeypatch.setattr(scheduler_settings, "official_investment_cutover_enabled", True)
    monkeypatch.setattr(service, "refresh_daily_fundamentals", lambda: {"loaded": 0})
    monkeypatch.setattr(service, "_holiday_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service.historical_fetcher, "select_symbols", lambda limit=None: [])
    monkeypatch.setattr(
        service.signal_engine.intelligence_engine.sector_strength_engine,
        "refresh_from_frames",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(service, "sync_news_for_symbols", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service.paper_trader,
        "clear_planned_watchlist_trades",
        lambda **kwargs: calls.__setitem__("clear_signal_type", kwargs.get("signal_type")),
    )
    monkeypatch.setattr(
        service,
        "generate_after_market_investment_recommendations",
        lambda *args, **kwargs: calls.__setitem__("generate_called", True),
    )

    service.after_market_analysis(force=True)

    assert calls["generate_called"] is False
    assert calls["clear_signal_type"] == "INTRADAY"
    monkeypatch.setattr(scheduler_settings, "official_investment_cutover_enabled", False)


def test_refresh_official_market_context_shadow_runs_cutover_when_enabled(monkeypatch):
    service = TradingSchedulerService()
    as_of_date = date.fromisoformat("2026-04-21")

    monkeypatch.setattr(scheduler_settings, "official_investment_shadow_enabled", True)
    monkeypatch.setattr(scheduler_settings, "official_investment_cutover_enabled", True)
    monkeypatch.setattr(service, "_holiday_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service.official_investment_data_service, "refresh_corporate_actions", lambda: {"stored": 1})
    monkeypatch.setattr(
        service.official_investment_data_service,
        "refresh_market_context",
        lambda: {"as_of_date": as_of_date.isoformat(), "sector_context_count": 12},
    )
    monkeypatch.setattr(
        service.official_snapshot_builder,
        "rebuild_daily_snapshot",
        lambda **kwargs: {"stored": 1, "as_of_date": as_of_date.isoformat()},
    )
    monkeypatch.setattr(
        service,
        "refresh_official_investment_scores_shadow",
        lambda **kwargs: {"as_of_date": as_of_date.isoformat(), "strong_buy": 2, "phase3": {"buy": 1}},
    )
    monkeypatch.setattr(
        service.shadow_comparison_service,
        "compare",
        lambda **kwargs: {"officialCoverage": 15, "coverageCompared": 15},
    )

    def _fake_generate(*args, **kwargs):  # noqa: ANN002, ANN003
        service._last_official_investment_cutover_summary = {
            "strong_buy_candidates": 2,
            "phase3_buy_candidates": 1,
            "created": 1,
            "blocked_by_market_health": 0,
            "blocked_by_sector_strength": 1,
            "blocked_by_earnings_proximity": 0,
            "blocked_by_promoter": 0,
            "blocked_by_entry_trigger": 1,
        }
        return [{"stock_symbol": "ZZCUTA", "strategy_name": "Official Breakout Cutover"}]

    monkeypatch.setattr(service, "generate_after_market_investment_recommendations", _fake_generate)

    result = service.refresh_official_market_context_shadow()

    assert result["cutover_recommendations"] == [{"stock_symbol": "ZZCUTA", "strategy_name": "Official Breakout Cutover"}]
    assert result["cutover_summary"]["created"] == 1
    monkeypatch.setattr(scheduler_settings, "official_investment_cutover_enabled", False)


def test_pre_market_red_risk_cancels_planned_official_trades(monkeypatch):
    service = TradingSchedulerService()
    today = date.today()

    monkeypatch.setattr(scheduler_settings, "global_risk_scanner_enabled", True)
    monkeypatch.setattr(service, "_holiday_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_planned_cutover_risk_levels_for_day", lambda planned_for: ["GREEN"])
    monkeypatch.setattr(
        service.global_risk_scanner,
        "scan",
        lambda as_of_date, scan_type="PRE_MARKET": GlobalRiskResult(
            as_of_date=as_of_date,
            scan_type=scan_type,
            risk_level="RED",
            position_size_multiplier=0.0,
            signals=[SignalResult("currency_stress", "BLOCK", 2.5, 2.0, "USDINR stress", {})],
            active_caution_count=0,
            active_block_count=1,
            summary_message="RED risk overnight.",
        ),
    )
    monkeypatch.setattr(
        service.official_investment_recommendation_engine,
        "cancel_planned_recommendations_for_day",
        lambda planned_for: 3 if planned_for == today else 0,
    )

    result = service.run_global_risk_scan_pre_market()

    assert result["risk_level"] == "RED"
    assert result["cancelled_plans"] == 3
    assert result["prior_plan_risk_levels"] == ["GREEN"]
