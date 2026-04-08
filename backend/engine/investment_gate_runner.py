from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.models_investment import OfficialInvestmentSnapshot
from backend.db.postgres import LynchScore, MinerviniScore, PiotroskiScore, session_scope
from backend.engine.earnings_proximity_gate import EarningsProximityGate
from backend.engine.entry_trigger import EntryTrigger
from backend.engine.investment_gate_types import GateResult, InvestmentGateDecision
from backend.engine.market_health_gate import MarketHealthGate
from backend.engine.promoter_gate import PromoterGate
from backend.engine.sector_strength_gate import SectorStrengthGate
from backend.logging_utils import get_logger


logger = get_logger(__name__)


class InvestmentGateRunner:
    def __init__(self, historical_fetcher: HistoricalFetcher | None = None) -> None:
        self.historical_fetcher = historical_fetcher or HistoricalFetcher()

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
    def _load_phase2_votes(session: Session, as_of_date: date, symbols: list[str] | None = None) -> dict[str, dict[str, Any]]:
        vote_map: dict[str, dict[str, Any]] = {}
        for model, key in (
            (LynchScore, "lynch_vote"),
            (PiotroskiScore, "piotroski_vote"),
            (MinerviniScore, "minervini_vote"),
        ):
            stmt = select(model).where(model.as_of_date == as_of_date)
            if symbols:
                stmt = stmt.where(model.symbol.in_(symbols))
            for row in session.scalars(stmt).all():
                vote_map.setdefault(row.symbol, {})[key] = bool(row.vote_yes)

        normalized_symbols = set(symbols or []) | set(vote_map)
        final: dict[str, dict[str, Any]] = {}
        for symbol in normalized_symbols:
            lynch_vote = bool(vote_map.get(symbol, {}).get("lynch_vote"))
            piotroski_vote = bool(vote_map.get(symbol, {}).get("piotroski_vote"))
            minervini_vote = bool(vote_map.get(symbol, {}).get("minervini_vote"))
            votes_yes = sum(1 for vote in (lynch_vote, piotroski_vote, minervini_vote) if vote)
            label = "STRONG_BUY" if votes_yes == 3 else "WATCHLIST" if votes_yes == 2 else "NO_ACTION"
            final[symbol] = {
                "lynch_vote": lynch_vote,
                "piotroski_vote": piotroski_vote,
                "minervini_vote": minervini_vote,
                "votes_yes": votes_yes,
                "label": label,
            }
        return final

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

    def _load_frames(self, symbols: list[str], as_of_date: date) -> tuple[dict[str, Any], dict[str, str]]:
        symbol_map = self.historical_fetcher.load_symbol_map()
        frames: dict[str, Any] = {}
        failures: dict[str, str] = {}
        for symbol in symbols:
            config = symbol_map.get(symbol.upper())
            if config is None:
                failures[symbol] = "SymbolConfig missing"
                continue
            try:
                frame = self.historical_fetcher.fetch_symbol_frame(config)
            except Exception as exc:  # pragma: no cover
                failures[symbol] = f"{type(exc).__name__}: {exc}"
                continue
            if getattr(frame, "empty", True):
                failures[symbol] = "Daily OHLCV frame is empty"
                continue
            if hasattr(frame, "index") and getattr(frame.index, "date", None) is not None:
                frame = frame[frame.index.date <= as_of_date]
            frames[symbol] = frame
        return frames, failures

    @staticmethod
    def _decision_from_gate_results(
        *,
        symbol: str,
        as_of_date: date,
        phase2_label: str,
        phase2_votes_yes: int,
        market_health: GateResult,
        sector_strength: GateResult,
        earnings_proximity: GateResult,
        promoter: GateResult,
        entry_trigger: GateResult,
    ) -> InvestmentGateDecision:
        all_gates_passed = all(
            (
                market_health.passed,
                sector_strength.passed,
                earnings_proximity.passed,
                promoter.passed,
                entry_trigger.passed,
            )
        )
        failed_results = [
            result
            for result in (market_health, sector_strength, earnings_proximity, promoter, entry_trigger)
            if not result.passed
        ]
        return InvestmentGateDecision(
            symbol=symbol,
            as_of_date=as_of_date,
            phase2_label=phase2_label,
            phase2_votes_yes=phase2_votes_yes,
            decision="BUY" if all_gates_passed else "SKIP",
            all_gates_passed=all_gates_passed,
            market_health_passed=market_health.passed,
            sector_strength_passed=sector_strength.passed,
            earnings_proximity_passed=earnings_proximity.passed,
            promoter_passed=promoter.passed,
            entry_trigger_passed=entry_trigger.passed,
            failure_reasons=[result.code for result in failed_results],
            debug_payload={
                "market_health": {"code": market_health.code, "message": market_health.message, "details": market_health.details},
                "sector_strength": {"code": sector_strength.code, "message": sector_strength.message, "details": sector_strength.details},
                "earnings_proximity": {"code": earnings_proximity.code, "message": earnings_proximity.message, "details": earnings_proximity.details},
                "promoter": {"code": promoter.code, "message": promoter.message, "details": promoter.details},
                "entry_trigger": {"code": entry_trigger.code, "message": entry_trigger.message, "details": entry_trigger.details},
            },
        )

    def score_symbol(self, symbol: str, as_of_date: date | None = None) -> InvestmentGateDecision | None:
        normalized_symbol = symbol.upper().strip()
        with session_scope() as session:
            resolved_as_of_date = self._resolve_as_of_date(session, as_of_date)
            if resolved_as_of_date is None:
                return None
            vote_map = self._load_phase2_votes(session, resolved_as_of_date, [normalized_symbol])
            phase2 = vote_map.get(normalized_symbol, {"votes_yes": 0, "label": "NO_ACTION"})
            snapshot = self._load_snapshots(session, resolved_as_of_date, [normalized_symbol]).get(normalized_symbol)
            market_context = MarketHealthGate.load_context(session, resolved_as_of_date)
            recent_market_contexts = MarketHealthGate.load_recent_contexts(session, resolved_as_of_date)

        if str(phase2["label"]) != "STRONG_BUY":
            return InvestmentGateDecision(
                symbol=normalized_symbol,
                as_of_date=resolved_as_of_date,
                phase2_label=str(phase2["label"]),
                phase2_votes_yes=int(phase2["votes_yes"]),
                decision="SKIP",
                all_gates_passed=False,
                market_health_passed=False,
                sector_strength_passed=False,
                earnings_proximity_passed=False,
                promoter_passed=False,
                entry_trigger_passed=False,
                failure_reasons=["phase2_not_strong_buy"],
                debug_payload={"gates_run": False},
            )

        frames, frame_failures = self._load_frames([normalized_symbol], resolved_as_of_date)
        market_health = MarketHealthGate.evaluate(market_context, recent_contexts=recent_market_contexts)
        sector_strength = SectorStrengthGate.evaluate(snapshot, market_context)
        earnings_proximity = EarningsProximityGate.evaluate(snapshot, resolved_as_of_date)
        promoter = PromoterGate.evaluate(snapshot)
        if normalized_symbol in frames:
            entry_trigger = EntryTrigger.evaluate(normalized_symbol, frames[normalized_symbol], resolved_as_of_date)
        else:
            entry_trigger = GateResult(
                False,
                "daily_ohlcv_frame_missing",
                "Daily OHLCV history could not be loaded.",
                {"symbol": normalized_symbol, "error": frame_failures.get(normalized_symbol)},
            )
        return self._decision_from_gate_results(
            symbol=normalized_symbol,
            as_of_date=resolved_as_of_date,
            phase2_label=str(phase2["label"]),
            phase2_votes_yes=int(phase2["votes_yes"]),
            market_health=market_health,
            sector_strength=sector_strength,
            earnings_proximity=earnings_proximity,
            promoter=promoter,
            entry_trigger=entry_trigger,
        )

    def run_universe(self, symbols: list[str] | None = None, as_of_date: date | None = None) -> dict[str, Any]:
        normalized_symbols = [symbol.upper().strip() for symbol in symbols or [] if symbol]
        with session_scope() as session:
            resolved_as_of_date = self._resolve_as_of_date(session, as_of_date)
            if resolved_as_of_date is None:
                return {
                    "as_of_date": None,
                    "requested": len(normalized_symbols),
                    "eligible_strong_buy": 0,
                    "processed": 0,
                    "buy": 0,
                    "skip": 0,
                    "skipped_non_strong_buy": 0,
                    "blocked_by_market_health": 0,
                    "blocked_by_sector_strength": 0,
                    "blocked_by_earnings_proximity": 0,
                    "blocked_by_promoter": 0,
                    "blocked_by_entry_trigger": 0,
                    "results": [],
                    "failed_examples": {},
                }
            vote_map = self._load_phase2_votes(session, resolved_as_of_date, normalized_symbols or None)
            strong_buy_symbols = sorted(symbol for symbol, payload in vote_map.items() if str(payload.get("label")) == "STRONG_BUY")
            snapshots = self._load_snapshots(session, resolved_as_of_date, strong_buy_symbols)
            market_context = MarketHealthGate.load_context(session, resolved_as_of_date)
            recent_market_contexts = MarketHealthGate.load_recent_contexts(session, resolved_as_of_date)

        frames, frame_failures = self._load_frames(strong_buy_symbols, resolved_as_of_date)
        results: list[InvestmentGateDecision] = []
        blocked_by_market_health = 0
        blocked_by_sector_strength = 0
        blocked_by_earnings_proximity = 0
        blocked_by_promoter = 0
        blocked_by_entry_trigger = 0

        for symbol in strong_buy_symbols:
            snapshot = snapshots.get(symbol)
            phase2 = vote_map[symbol]
            market_health = MarketHealthGate.evaluate(market_context, recent_contexts=recent_market_contexts)
            sector_strength = SectorStrengthGate.evaluate(snapshot, market_context)
            earnings_proximity = EarningsProximityGate.evaluate(snapshot, resolved_as_of_date)
            promoter = PromoterGate.evaluate(snapshot)
            if symbol in frames:
                entry_trigger = EntryTrigger.evaluate(symbol, frames[symbol], resolved_as_of_date)
            else:
                entry_trigger = GateResult(
                    False,
                    "daily_ohlcv_frame_missing",
                    "Daily OHLCV history could not be loaded.",
                    {"symbol": symbol, "error": frame_failures.get(symbol)},
                )

            decision = self._decision_from_gate_results(
                symbol=symbol,
                as_of_date=resolved_as_of_date,
                phase2_label=str(phase2["label"]),
                phase2_votes_yes=int(phase2["votes_yes"]),
                market_health=market_health,
                sector_strength=sector_strength,
                earnings_proximity=earnings_proximity,
                promoter=promoter,
                entry_trigger=entry_trigger,
            )
            if not market_health.passed:
                blocked_by_market_health += 1
            if not sector_strength.passed:
                blocked_by_sector_strength += 1
            if not earnings_proximity.passed:
                blocked_by_earnings_proximity += 1
            if not promoter.passed:
                blocked_by_promoter += 1
            if not entry_trigger.passed:
                blocked_by_entry_trigger += 1
            results.append(decision)
            logger.info(
                "Phase3 investment gate decision for %s on %s: %s (%s)",
                symbol,
                resolved_as_of_date.isoformat(),
                decision.decision,
                ", ".join(decision.failure_reasons) if decision.failure_reasons else "all_gates_passed",
            )

        summary = {
            "as_of_date": resolved_as_of_date.isoformat(),
            "requested": len(normalized_symbols) if normalized_symbols else len(vote_map),
            "eligible_strong_buy": len(strong_buy_symbols),
            "processed": len(results),
            "buy": sum(1 for result in results if result.decision == "BUY"),
            "skip": sum(1 for result in results if result.decision == "SKIP"),
            "skipped_non_strong_buy": max(len(normalized_symbols) - len(strong_buy_symbols), 0) if normalized_symbols else 0,
            "blocked_by_market_health": blocked_by_market_health,
            "blocked_by_sector_strength": blocked_by_sector_strength,
            "blocked_by_earnings_proximity": blocked_by_earnings_proximity,
            "blocked_by_promoter": blocked_by_promoter,
            "blocked_by_entry_trigger": blocked_by_entry_trigger,
            "results": results,
            "failed_examples": dict(list(frame_failures.items())[:10]),
        }
        logger.info(
            "Phase3 investment gate shadow summary for %s: strong_buy=%s buy=%s skip=%s blocked_market=%s blocked_sector=%s blocked_earnings=%s blocked_promoter=%s blocked_entry=%s",
            summary["as_of_date"],
            summary["eligible_strong_buy"],
            summary["buy"],
            summary["skip"],
            summary["blocked_by_market_health"],
            summary["blocked_by_sector_strength"],
            summary["blocked_by_earnings_proximity"],
            summary["blocked_by_promoter"],
            summary["blocked_by_entry_trigger"],
        )
        return summary


__all__ = ["InvestmentGateRunner", "InvestmentGateDecision"]
