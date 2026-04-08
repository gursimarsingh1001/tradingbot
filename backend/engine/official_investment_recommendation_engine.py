from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.models_investment import OfficialInvestmentSnapshot
from backend.db.postgres import LynchScore, MinerviniScore, PaperTrade, PiotroskiScore, session_scope
from backend.engine.global_risk_scanner import GlobalRiskScanner
from backend.engine.investment_gate_runner import InvestmentGateRunner
from backend.engine.paper_trader_v2 import PaperTrader
from backend.logging_utils import get_logger


settings = get_settings()
logger = get_logger(__name__)


@dataclass(slots=True)
class OfficialInvestmentCutoverResult:
    as_of_date: str | None
    next_session: str | None
    global_risk_level: str | None
    global_risk_scan_type: str | None
    position_size_multiplier: float
    active_global_signals: list[str]
    risk_summary_message: str | None
    strong_buy_candidates: int
    phase3_buy_candidates: int
    created: int
    cleared_existing_plans: int
    skipped_existing_open: int
    blocked_by_market_health: int
    blocked_by_sector_strength: int
    blocked_by_earnings_proximity: int
    blocked_by_promoter: int
    blocked_by_entry_trigger: int
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    failed_examples: dict[str, str] = field(default_factory=dict)


class OfficialInvestmentRecommendationEngine:
    STRATEGY_NAME = "Official Breakout Cutover"
    SIGNAL_TYPE = "INVESTMENT"
    DIRECTION = "BUY"
    DEFAULT_TOP_N = 10
    DEFAULT_MAX_HOLDING_DAYS = 75

    def __init__(
        self,
        *,
        historical_fetcher: HistoricalFetcher | None = None,
        paper_trader: PaperTrader | None = None,
        gate_runner: InvestmentGateRunner | None = None,
        risk_scanner: GlobalRiskScanner | None = None,
    ) -> None:
        self.historical_fetcher = historical_fetcher or HistoricalFetcher()
        self.paper_trader = paper_trader or PaperTrader()
        self.gate_runner = gate_runner or InvestmentGateRunner(historical_fetcher=self.historical_fetcher)
        self.risk_scanner = risk_scanner or GlobalRiskScanner(historical_fetcher=self.historical_fetcher)

    @staticmethod
    def _resolve_as_of_date(session: Session, as_of_date: date | None) -> date | None:
        if as_of_date is not None:
            return as_of_date
        latest = session.scalar(
            select(OfficialInvestmentSnapshot).order_by(OfficialInvestmentSnapshot.as_of_date.desc())
        )
        if latest is None or latest.as_of_date is None:
            return None
        return latest.as_of_date

    @staticmethod
    def _load_snapshots(session: Session, as_of_date: date, symbols: list[str]) -> dict[str, OfficialInvestmentSnapshot]:
        if not symbols:
            return {}
        rows = session.scalars(
            select(OfficialInvestmentSnapshot).where(
                OfficialInvestmentSnapshot.as_of_date == as_of_date,
                OfficialInvestmentSnapshot.symbol.in_(symbols),
            )
        ).all()
        return {row.symbol: row for row in rows if row.symbol}

    @staticmethod
    def _load_score_rows(session: Session, as_of_date: date, symbols: list[str]) -> tuple[dict[str, LynchScore], dict[str, PiotroskiScore], dict[str, MinerviniScore]]:
        if not symbols:
            return {}, {}, {}
        lynch = session.scalars(
            select(LynchScore).where(LynchScore.as_of_date == as_of_date, LynchScore.symbol.in_(symbols))
        ).all()
        piotroski = session.scalars(
            select(PiotroskiScore).where(PiotroskiScore.as_of_date == as_of_date, PiotroskiScore.symbol.in_(symbols))
        ).all()
        minervini = session.scalars(
            select(MinerviniScore).where(MinerviniScore.as_of_date == as_of_date, MinerviniScore.symbol.in_(symbols))
        ).all()
        return (
            {row.symbol: row for row in lynch if row.symbol},
            {row.symbol: row for row in piotroski if row.symbol},
            {row.symbol: row for row in minervini if row.symbol},
        )

    @staticmethod
    def _open_investment_symbols(session: Session) -> set[str]:
        rows = session.scalars(
            select(PaperTrade).where(
                PaperTrade.signal_type == "INVESTMENT",
                PaperTrade.exit_date.is_(None),
            )
        ).all()
        symbols: set[str] = set()
        for row in rows:
            if not row.stock_symbol:
                continue
            metadata = row.metadata_json or {}
            if metadata.get("plan_only"):
                continue
            symbols.add(row.stock_symbol.upper())
        return symbols

    @staticmethod
    def _clear_planned_investment_rows(session: Session, *, from_date: date) -> int:
        trades = session.scalars(
            select(PaperTrade).where(
                PaperTrade.signal_type == "INVESTMENT",
                PaperTrade.entry_date >= from_date,
                PaperTrade.exit_date.is_(None),
            )
        ).all()
        cleared = 0
        for trade in trades:
            metadata = trade.metadata_json or {}
            if metadata.get("plan_only"):
                session.delete(trade)
                cleared += 1
        return cleared

    @staticmethod
    def _clear_official_cutover_plans_for_day(session: Session, *, planned_for: date) -> int:
        trades = session.scalars(
            select(PaperTrade).where(
                PaperTrade.signal_type == "INVESTMENT",
                PaperTrade.entry_date == planned_for,
                PaperTrade.exit_date.is_(None),
            )
        ).all()
        cleared = 0
        for trade in trades:
            metadata = trade.metadata_json or {}
            if metadata.get("plan_only") and metadata.get("source_kind") == "official_investment_cutover":
                session.delete(trade)
                cleared += 1
        return cleared

    @staticmethod
    def _filter_frame(frame: pd.DataFrame | None, as_of_date: date) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        filtered = frame.copy()
        if isinstance(filtered.index, pd.DatetimeIndex):
            filtered = filtered[filtered.index.date <= as_of_date]
        return filtered

    def _load_frames(self, symbols: list[str], as_of_date: date) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        symbol_map = self.historical_fetcher.load_symbol_map()
        frames: dict[str, pd.DataFrame] = {}
        failures: dict[str, str] = {}
        for symbol in symbols:
            config = symbol_map.get(symbol.upper())
            if config is None:
                failures[symbol] = "SymbolConfig missing"
                continue
            try:
                frame = self.historical_fetcher.fetch_symbol_frame(config)
            except Exception as exc:  # pragma: no cover - defensive around data/cache failures
                failures[symbol] = f"{type(exc).__name__}: {exc}"
                continue
            filtered = self._filter_frame(frame, as_of_date)
            if filtered.empty:
                failures[symbol] = "Daily OHLCV frame is empty"
                continue
            frames[symbol] = filtered
        return frames, failures

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed != parsed:
            return default
        return parsed

    @classmethod
    def _clamp(cls, value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    @classmethod
    def _breakout_payload(cls, frame: pd.DataFrame) -> dict[str, float] | None:
        if frame.empty or len(frame) < 21:
            return None
        latest = frame.iloc[-1]
        prior = frame.iloc[-21:-1]
        close = cls._float(latest.get("Close"))
        breakout_day_high = cls._float(latest.get("High"))
        breakout_day_low = cls._float(latest.get("Low"))
        latest_volume = cls._float(latest.get("Volume"))
        prior_high = cls._float(pd.to_numeric(prior["High"], errors="coerce").max())
        avg_volume_20 = cls._float(pd.to_numeric(prior["Volume"], errors="coerce").mean())
        if close <= 0 or breakout_day_high <= 0 or breakout_day_low <= 0 or prior_high <= 0 or avg_volume_20 <= 0 or latest_volume <= 0:
            return None
        return {
            "close": close,
            "prior_20_day_high": prior_high,
            "breakout_day_high": breakout_day_high,
            "breakout_day_low": breakout_day_low,
            "latest_volume": latest_volume,
            "avg_volume_20": avg_volume_20,
            "volume_ratio": latest_volume / avg_volume_20,
        }

    @classmethod
    def _confidence_components(
        cls,
        *,
        lynch_row: LynchScore,
        piotroski_row: PiotroskiScore,
        minervini_row: MinerviniScore,
        decision_debug: dict[str, Any],
        breakout: dict[str, float],
    ) -> tuple[float, dict[str, float]]:
        sector_details = ((decision_debug.get("sector_strength") or {}).get("details") or {})
        sector_close = cls._float(sector_details.get("close"))
        sector_sma50 = cls._float(sector_details.get("sma50"))
        sector_margin_ratio = ((sector_close / sector_sma50) - 1.0) if sector_sma50 > 0 else 0.0

        components = {
            "base_three_of_three": 65.0,
            "lynch_excess": cls._clamp(max(cls._float(lynch_row.lynch_value) - 1.5, 0.0) * 4.0, 0.0, 8.0),
            "piotroski_excess": cls._clamp(max(int(piotroski_row.f_score or 0) - 7, 0) * 3.0, 0.0, 6.0),
            "rs_percentile_excess": cls._clamp(max(cls._float(minervini_row.rs_percentile) - 70.0, 0.0) / 5.0, 0.0, 6.0),
            "volume_expansion": cls._clamp(max(cls._float(breakout.get("volume_ratio")) - 1.5, 0.0) * 8.0, 0.0, 7.0),
            "sector_strength_margin": cls._clamp(max(sector_margin_ratio, 0.0) * 80.0, 0.0, 4.0),
        }
        confidence = round(cls._clamp(sum(components.values()), 65.0, 90.0), 2)
        return confidence, components

    @classmethod
    def _rank_score(
        cls,
        *,
        confidence: float,
        lynch_row: LynchScore,
        piotroski_row: PiotroskiScore,
        minervini_row: MinerviniScore,
        breakout: dict[str, float],
    ) -> float:
        return round(
            confidence
            + min(cls._float(lynch_row.lynch_value), 4.0) * 1.5
            + max(int(piotroski_row.f_score or 0) - 7, 0) * 2.0
            + (cls._float(minervini_row.rs_percentile) / 25.0)
            + min(cls._float(breakout.get("volume_ratio")), 3.0) * 2.0,
            4,
        )

    @classmethod
    def _entry_levels(cls, breakout: dict[str, float]) -> dict[str, float]:
        entry_zone_low = breakout["prior_20_day_high"]
        entry_zone_high = breakout["breakout_day_high"]
        entry_price = entry_zone_high
        stop_loss = max(breakout["breakout_day_low"], entry_price * 0.93)
        if stop_loss >= entry_price:
            stop_loss = entry_price * 0.93
        risk_per_share = max(entry_price - stop_loss, entry_price * 0.01)
        target_1 = entry_price + (2.0 * risk_per_share)
        target_2 = entry_price + (4.0 * risk_per_share)
        target_3 = entry_price + (6.0 * risk_per_share)
        return {
            "entry_zone_low": round(entry_zone_low, 2),
            "entry_zone_high": round(entry_zone_high, 2),
            "entry_price": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "target_1": round(target_1, 2),
            "target_2": round(target_2, 2),
            "target_3": round(target_3, 2),
            "risk_per_share": round(risk_per_share, 2),
        }

    @classmethod
    def _phase2_quality_score(cls, lynch_row: LynchScore, piotroski_row: PiotroskiScore, minervini_row: MinerviniScore) -> float:
        lynch_component = cls._clamp(cls._float(lynch_row.lynch_value) / 2.5, 0.0, 1.0)
        piotroski_component = cls._clamp(cls._float(piotroski_row.f_score) / 9.0, 0.0, 1.0)
        minervini_component = cls._clamp(cls._float(minervini_row.rs_percentile) / 100.0, 0.0, 1.0)
        return round((lynch_component + piotroski_component + minervini_component) / 3.0, 4)

    @classmethod
    def _days_to_earnings(cls, snapshot: OfficialInvestmentSnapshot, as_of_date: date) -> int | None:
        if snapshot.earnings_date is None:
            return None
        return (snapshot.earnings_date - as_of_date).days

    @classmethod
    def _build_signal(
        cls,
        *,
        snapshot: OfficialInvestmentSnapshot,
        lynch_row: LynchScore,
        piotroski_row: PiotroskiScore,
        minervini_row: MinerviniScore,
        decision_debug: dict[str, Any],
        breakout: dict[str, float],
        confidence: float,
        confidence_components: dict[str, float],
        rank_score: float,
        as_of_date: date,
        global_risk_level: str,
        global_risk_scan_type: str,
        position_size_multiplier: float,
        active_global_signals: list[str],
        global_signal_details: dict[str, Any],
    ) -> dict[str, Any]:
        levels = cls._entry_levels(breakout)
        sector_details = ((decision_debug.get("sector_strength") or {}).get("details") or {})
        market_details = ((decision_debug.get("market_health") or {}).get("details") or {})
        quality_score = cls._phase2_quality_score(lynch_row, piotroski_row, minervini_row)
        days_to_earnings = cls._days_to_earnings(snapshot, as_of_date)
        gate_flags = [
            "Official Phase 2 score: 3/3",
            "All Phase 3 safety gates passed",
            "20-day breakout confirmed with volume expansion",
        ]
        basis_points = [
            f"Lynch PEG is {cls._float(lynch_row.lynch_value):.2f}, above the 1.5 approval threshold.",
            f"Piotroski F-Score is {int(piotroski_row.f_score or 0)}/9.",
            f"Minervini RS percentile is {cls._float(minervini_row.rs_percentile):.1f} with 8/8 template checks passing.",
            f"Close broke above the prior 20-day high of {breakout['prior_20_day_high']:.2f} with {breakout['volume_ratio']:.2f}x average volume.",
            f"Sector strength is confirmed with sector close {cls._float(sector_details.get('close')):.2f} above SMA50 {cls._float(sector_details.get('sma50')):.2f}.",
        ]
        explanation_sections = {
            "technical": [
                "Minervini breakout confirmation fired on the latest daily bar.",
                f"Close: {breakout['close']:.2f}",
                f"Prior 20-day high: {breakout['prior_20_day_high']:.2f}",
                f"Breakout-day high / low: {breakout['breakout_day_high']:.2f} / {breakout['breakout_day_low']:.2f}",
                f"Volume ratio vs prior 20-day average: {breakout['volume_ratio']:.2f}x",
            ],
            "phase2": [
                f"Lynch PEG vote passed at {cls._float(lynch_row.lynch_value):.2f}.",
                f"Piotroski vote passed at {int(piotroski_row.f_score or 0)}/9.",
                f"Minervini vote passed with RS percentile {cls._float(minervini_row.rs_percentile):.1f}.",
            ],
            "safety": [
                str((decision_debug.get("market_health") or {}).get("message") or ""),
                str((decision_debug.get("sector_strength") or {}).get("message") or ""),
                str((decision_debug.get("earnings_proximity") or {}).get("message") or ""),
                str((decision_debug.get("promoter") or {}).get("message") or ""),
            ],
            "risk": [
                f"Planned breakout entry zone: {levels['entry_zone_low']:.2f} to {levels['entry_zone_high']:.2f}.",
                f"Stop-loss: {levels['stop_loss']:.2f} ({levels['risk_per_share']:.2f} risk per share).",
                f"Targets: {levels['target_1']:.2f}, {levels['target_2']:.2f}, {levels['target_3']:.2f}.",
                f"Max holding days: {cls.DEFAULT_MAX_HOLDING_DAYS}.",
            ],
        }
        analysis_snapshot = {
            "as_of_date": as_of_date.isoformat(),
            "breakout_close": round(breakout["close"], 2),
            "prior_20_day_high": round(breakout["prior_20_day_high"], 2),
            "breakout_day_high": round(breakout["breakout_day_high"], 2),
            "breakout_day_low": round(breakout["breakout_day_low"], 2),
            "volume_ratio": round(breakout["volume_ratio"], 4),
            "rs_percentile": round(cls._float(minervini_row.rs_percentile), 2),
            "nifty50_close": market_details.get("nifty50_close"),
            "nifty50_sma200": market_details.get("nifty50_sma200"),
            "india_vix": market_details.get("india_vix"),
        }
        audit_payload = {
            "phase2": {
                "label": "STRONG_BUY",
                "votes_yes": 3,
                "lynch_vote": bool(lynch_row.vote_yes),
                "piotroski_vote": bool(piotroski_row.vote_yes),
                "minervini_vote": bool(minervini_row.vote_yes),
                "lynch_value": lynch_row.lynch_value,
                "piotroski_f_score": int(piotroski_row.f_score or 0),
                "minervini_rs_percentile": minervini_row.rs_percentile,
                "minervini_passed_checks": int(minervini_row.passed_checks or 0),
            },
            "phase3": decision_debug,
            "breakout": analysis_snapshot,
            "confidence_components": confidence_components,
            "rank_score": rank_score,
            "global_risk": {
                "risk_level": global_risk_level,
                "scan_type": global_risk_scan_type,
                "position_size_multiplier": position_size_multiplier,
                "active_signals": list(active_global_signals),
                "signal_details": dict(global_signal_details),
            },
        }
        return {
            "stock_symbol": snapshot.symbol,
            "strategy_name": cls.STRATEGY_NAME,
            "signal_type": cls.SIGNAL_TYPE,
            "signal": cls.DIRECTION,
            "confidence_score": confidence,
            "entry_zone_low": levels["entry_zone_low"],
            "entry_zone_high": levels["entry_zone_high"],
            "entry_price": levels["entry_price"],
            "stop_loss": levels["stop_loss"],
            "target_1": levels["target_1"],
            "target_2": levels["target_2"],
            "target_3": levels["target_3"],
            "pattern_name": "Minervini Breakout",
            "regime_at_entry": "OFFICIAL_CUTOVER",
            "news_score_at_entry": 0.0,
            "recommendation_reason": "Official Phase 2 3/3 score, all Phase 3 safety gates passed, and the Minervini breakout trigger confirmed on daily data.",
            "basis_points": basis_points,
            "explanation_sections": explanation_sections,
            "feature_breakdown": confidence_components,
            "analysis_snapshot": analysis_snapshot,
            "sector": snapshot.sector,
            "sector_score": round(cls._clamp(max((cls._float(sector_details.get('close')) / max(cls._float(sector_details.get('sma50')), 0.01)) - 1.0, 0.0) * 10.0, 0.0, 1.0), 4),
            "days_to_earnings": days_to_earnings,
            "event_flags": gate_flags,
            "fundamental_quality_score": quality_score,
            "fundamental_has_snapshot": True,
            "fundamental_confidence": 0.95,
            "financial_data_source": "OFFICIAL_PHASE1_SNAPSHOT",
            "company_name": snapshot.company_name,
            "opened_from": "official_investment_cutover",
            "trigger_style": "BREAKOUT",
            "max_holding_days": cls.DEFAULT_MAX_HOLDING_DAYS,
            "source_kind": "official_investment_cutover",
            "audit_payload": audit_payload,
            "investment_rank": rank_score,
            "global_risk_level": global_risk_level,
            "global_risk_scan_type": global_risk_scan_type,
            "global_risk_as_of_date": as_of_date.isoformat(),
            "position_size_multiplier": position_size_multiplier,
            "active_global_signals": list(active_global_signals),
            "global_signal_details": dict(global_signal_details),
        }

    def rebuild_planned_recommendations(
        self,
        *,
        as_of_date: date | None = None,
        top_n: int = DEFAULT_TOP_N,
    ) -> OfficialInvestmentCutoverResult:
        with session_scope() as session:
            resolved_as_of_date = self._resolve_as_of_date(session, as_of_date)
            if resolved_as_of_date is None:
                return OfficialInvestmentCutoverResult(
                    as_of_date=None,
                    next_session=None,
                    global_risk_level=None,
                    global_risk_scan_type=None,
                    position_size_multiplier=1.0,
                    active_global_signals=[],
                    risk_summary_message=None,
                    strong_buy_candidates=0,
                    phase3_buy_candidates=0,
                    created=0,
                    cleared_existing_plans=0,
                    skipped_existing_open=0,
                    blocked_by_market_health=0,
                    blocked_by_sector_strength=0,
                    blocked_by_earnings_proximity=0,
                    blocked_by_promoter=0,
                    blocked_by_entry_trigger=0,
                )

        if settings.global_risk_scanner_enabled:
            risk_result = self.risk_scanner.scan(resolved_as_of_date, scan_type="AFTER_MARKET")
        else:
            risk_result = None
        risk_level = risk_result.risk_level if risk_result is not None else "GREEN"
        risk_multiplier = risk_result.position_size_multiplier if risk_result is not None else 1.0
        risk_signals = [
            signal.name
            for signal in (risk_result.signals if risk_result is not None else [])
            if signal.severity in {"CAUTION", "BLOCK"}
        ]
        risk_signal_details = {
            signal.name: asdict(signal)
            for signal in (risk_result.signals if risk_result is not None else [])
        }
        gate_summary = self.gate_runner.run_universe(as_of_date=resolved_as_of_date)
        next_session = self.paper_trader.market_calendar.next_trading_day(resolved_as_of_date)
        with session_scope() as session:
            cleared_existing_plans = self._clear_planned_investment_rows(session, from_date=next_session)
        if risk_level == "RED":
            summary = OfficialInvestmentCutoverResult(
                as_of_date=resolved_as_of_date.isoformat(),
                next_session=next_session.isoformat(),
                global_risk_level=risk_level,
                global_risk_scan_type=risk_result.scan_type if risk_result is not None else None,
                position_size_multiplier=risk_multiplier,
                active_global_signals=risk_signals,
                risk_summary_message=risk_result.summary_message if risk_result is not None else None,
                strong_buy_candidates=int(gate_summary.get("eligible_strong_buy") or 0),
                phase3_buy_candidates=int(gate_summary.get("buy") or 0),
                created=0,
                cleared_existing_plans=cleared_existing_plans,
                skipped_existing_open=0,
                blocked_by_market_health=int(gate_summary.get("blocked_by_market_health") or 0),
                blocked_by_sector_strength=int(gate_summary.get("blocked_by_sector_strength") or 0),
                blocked_by_earnings_proximity=int(gate_summary.get("blocked_by_earnings_proximity") or 0),
                blocked_by_promoter=int(gate_summary.get("blocked_by_promoter") or 0),
                blocked_by_entry_trigger=int(gate_summary.get("blocked_by_entry_trigger") or 0),
                recommendations=[],
                failed_examples={},
            )
            logger.warning(
                "Official investment cutover blocked by global risk for %s: level=%s signals=%s",
                summary.as_of_date,
                risk_level,
                ", ".join(risk_signals) or "none",
            )
            return summary

        decisions = list(gate_summary.get("results") or [])
        buy_symbols = [decision.symbol for decision in decisions if decision.decision == "BUY"]

        with session_scope() as session:
            open_symbols = self._open_investment_symbols(session)
            filtered_buy_symbols = [symbol for symbol in buy_symbols if symbol.upper() not in open_symbols]
            snapshots = self._load_snapshots(session, resolved_as_of_date, filtered_buy_symbols)
            lynch_rows, piotroski_rows, minervini_rows = self._load_score_rows(session, resolved_as_of_date, filtered_buy_symbols)

        frames, frame_failures = self._load_frames(filtered_buy_symbols, resolved_as_of_date)
        candidates: list[dict[str, Any]] = []
        failed_examples = dict(frame_failures)

        for decision in decisions:
            if decision.decision != "BUY":
                continue
            if decision.symbol.upper() in open_symbols:
                continue
            snapshot = snapshots.get(decision.symbol)
            lynch_row = lynch_rows.get(decision.symbol)
            piotroski_row = piotroski_rows.get(decision.symbol)
            minervini_row = minervini_rows.get(decision.symbol)
            frame = frames.get(decision.symbol)
            if snapshot is None or lynch_row is None or piotroski_row is None or minervini_row is None:
                failed_examples.setdefault(decision.symbol, "Official snapshot or score rows are missing")
                continue
            breakout = self._breakout_payload(frame) if frame is not None else None
            if breakout is None:
                failed_examples.setdefault(decision.symbol, "Breakout payload could not be derived from daily OHLCV")
                continue
            confidence, confidence_components = self._confidence_components(
                lynch_row=lynch_row,
                piotroski_row=piotroski_row,
                minervini_row=minervini_row,
                decision_debug=decision.debug_payload,
                breakout=breakout,
            )
            rank_score = self._rank_score(
                confidence=confidence,
                lynch_row=lynch_row,
                piotroski_row=piotroski_row,
                minervini_row=minervini_row,
                breakout=breakout,
            )
            candidates.append(
                self._build_signal(
                    snapshot=snapshot,
                    lynch_row=lynch_row,
                    piotroski_row=piotroski_row,
                    minervini_row=minervini_row,
                    decision_debug=decision.debug_payload,
                    breakout=breakout,
                    confidence=confidence,
                    confidence_components=confidence_components,
                    rank_score=rank_score,
                    as_of_date=resolved_as_of_date,
                    global_risk_level=risk_level,
                    global_risk_scan_type=risk_result.scan_type if risk_result is not None else "AFTER_MARKET",
                    position_size_multiplier=risk_multiplier,
                    active_global_signals=risk_signals,
                    global_signal_details=risk_signal_details,
                )
            )

        candidates.sort(
            key=lambda item: (
                float(item.get("investment_rank") or 0.0),
                float(item.get("confidence_score") or 0.0),
                float(item.get("fundamental_quality_score") or 0.0),
                item.get("stock_symbol") or "",
            ),
            reverse=True,
        )
        selected = candidates[: max(int(top_n or self.DEFAULT_TOP_N), 0)]

        created = 0
        recommendations: list[dict[str, Any]] = []
        for signal in selected:
            trade_id = self.paper_trader.plan_signal_trade(
                signal,
                planned_for=next_session,
                activation_window_days=5,
                position_size_multiplier=risk_multiplier,
            )
            signal["paper_trade_id"] = trade_id
            signal["paper_trade_status"] = "PLANNED"
            recommendations.append(signal)
            created += 1

        summary = OfficialInvestmentCutoverResult(
            as_of_date=resolved_as_of_date.isoformat(),
            next_session=next_session.isoformat(),
            global_risk_level=risk_level,
            global_risk_scan_type=risk_result.scan_type if risk_result is not None else None,
            position_size_multiplier=risk_multiplier,
            active_global_signals=risk_signals,
            risk_summary_message=risk_result.summary_message if risk_result is not None else None,
            strong_buy_candidates=int(gate_summary.get("eligible_strong_buy") or 0),
            phase3_buy_candidates=int(gate_summary.get("buy") or 0),
            created=created,
            cleared_existing_plans=cleared_existing_plans,
            skipped_existing_open=len(set(buy_symbols) & open_symbols),
            blocked_by_market_health=int(gate_summary.get("blocked_by_market_health") or 0),
            blocked_by_sector_strength=int(gate_summary.get("blocked_by_sector_strength") or 0),
            blocked_by_earnings_proximity=int(gate_summary.get("blocked_by_earnings_proximity") or 0),
            blocked_by_promoter=int(gate_summary.get("blocked_by_promoter") or 0),
            blocked_by_entry_trigger=int(gate_summary.get("blocked_by_entry_trigger") or 0),
            recommendations=recommendations,
            failed_examples=dict(list(failed_examples.items())[:10]),
        )
        logger.info(
            "Official investment cutover summary for %s: strong_buy=%s phase3_buy=%s created=%s cleared=%s skipped_open=%s blocked_market=%s blocked_sector=%s blocked_earnings=%s blocked_promoter=%s blocked_entry=%s",
            summary.as_of_date,
            summary.strong_buy_candidates,
            summary.phase3_buy_candidates,
            summary.created,
            summary.cleared_existing_plans,
            summary.skipped_existing_open,
            summary.blocked_by_market_health,
            summary.blocked_by_sector_strength,
            summary.blocked_by_earnings_proximity,
            summary.blocked_by_promoter,
            summary.blocked_by_entry_trigger,
        )
        return summary

    def cancel_planned_recommendations_for_day(self, *, planned_for: date) -> int:
        with session_scope() as session:
            cleared = self._clear_official_cutover_plans_for_day(session, planned_for=planned_for)
        if cleared:
            logger.warning("Cancelled %s planned official investment trade(s) for %s due to pre-market global risk.", cleared, planned_for.isoformat())
        return cleared

    @staticmethod
    def asdict(result: OfficialInvestmentCutoverResult) -> dict[str, Any]:
        return asdict(result)


__all__ = ["OfficialInvestmentCutoverResult", "OfficialInvestmentRecommendationEngine"]
