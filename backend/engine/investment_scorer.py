from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.models_investment import OfficialFinancialPeriod, OfficialInvestmentSnapshot
from backend.db.postgres import LynchScore, MinerviniScore, PiotroskiScore, session_scope
from backend.engine.lynch_peg_scorer import LynchPegScorer, LynchScoreResult
from backend.engine.minervini_scorer import MinerviniScorer, MinerviniScoreResult
from backend.engine.piotroski_scorer import PiotroskiScorer, PiotroskiScoreResult
from backend.logging_utils import get_logger


logger = get_logger(__name__)


@dataclass(slots=True)
class CombinedInvestmentScore:
    symbol: str
    as_of_date: date
    votes_yes: int
    lynch_vote: bool
    piotroski_vote: bool
    minervini_vote: bool
    label: str
    lynch_value: float | None
    piotroski_f_score: int
    minervini_passed_checks: int
    minervini_rs_percentile: float | None


class InvestmentScorer:
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
    def _load_snapshots(session: Session, as_of_date: date, symbols: list[str] | None) -> list[OfficialInvestmentSnapshot]:
        stmt = select(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.as_of_date == as_of_date)
        if symbols:
            stmt = stmt.where(OfficialInvestmentSnapshot.symbol.in_(symbols))
        return session.scalars(stmt.order_by(OfficialInvestmentSnapshot.symbol.asc())).all()

    @staticmethod
    def _load_annual_periods(session: Session, as_of_date: date, symbols: list[str]) -> dict[str, list[OfficialFinancialPeriod]]:
        if not symbols:
            return {}
        rows = session.scalars(
            select(OfficialFinancialPeriod).where(
                OfficialFinancialPeriod.symbol.in_(symbols),
                OfficialFinancialPeriod.period_type == "ANNUAL",
                OfficialFinancialPeriod.period_end <= as_of_date,
            )
        ).all()
        grouped: dict[str, list[OfficialFinancialPeriod]] = {}
        for row in rows:
            grouped.setdefault(row.symbol, []).append(row)
        for symbol_rows in grouped.values():
            symbol_rows.sort(key=lambda period: period.period_end or date.min, reverse=True)
        return grouped

    @staticmethod
    def _filter_frame(frame: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
        if frame.empty:
            return frame
        if isinstance(frame.index, pd.DatetimeIndex):
            return frame[frame.index.date <= as_of_date]
        return frame

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
            except Exception as exc:  # pragma: no cover - defensive around network/cache fetch
                failures[symbol] = f"{type(exc).__name__}: {exc}"
                continue
            frames[symbol] = self._filter_frame(frame, as_of_date)
        return frames, failures

    @staticmethod
    def _compute_rs_percentiles(frames: dict[str, pd.DataFrame]) -> dict[str, float | None]:
        returns: dict[str, float] = {}
        for symbol, frame in frames.items():
            if frame.empty or len(frame) < MinerviniScorer.LOOKBACK_SESSIONS:
                continue
            closes = pd.to_numeric(frame["Close"], errors="coerce")
            current_close = closes.iloc[-1]
            base_close = closes.iloc[-MinerviniScorer.LOOKBACK_SESSIONS]
            if pd.isna(current_close) or pd.isna(base_close) or float(base_close) <= 0:
                continue
            returns[symbol] = (float(current_close) / float(base_close)) - 1.0
        if not returns:
            return {symbol: None for symbol in frames}
        percentile_series = pd.Series(returns, dtype="float64").rank(pct=True, method="average") * 100.0
        percentiles = {symbol: float(percentile_series.get(symbol)) for symbol in returns}
        for symbol in frames:
            percentiles.setdefault(symbol, None)
        return percentiles

    @staticmethod
    def _label(votes_yes: int) -> str:
        if votes_yes == 3:
            return "STRONG_BUY"
        if votes_yes == 2:
            return "WATCHLIST"
        return "NO_ACTION"

    @staticmethod
    def _upsert_lynch_score(session: Session, result: LynchScoreResult) -> None:
        row = session.scalar(
            select(LynchScore).where(
                LynchScore.symbol == result.symbol,
                LynchScore.as_of_date == result.as_of_date,
            )
        )
        payload = {
            "lynch_value": result.lynch_value,
            "eps_growth_3y_cagr": result.eps_growth_3y_cagr,
            "dividend_yield": result.dividend_yield,
            "pe_ratio": result.pe_ratio,
            "vote_yes": result.vote_yes,
            "data_complete": result.data_complete,
            "missing_fields": result.missing_fields,
            "details_json": result.details_json,
        }
        if row is None:
            session.add(LynchScore(symbol=result.symbol, as_of_date=result.as_of_date, **payload))
            return
        for key, value in payload.items():
            setattr(row, key, value)

    @staticmethod
    def _upsert_piotroski_score(session: Session, result: PiotroskiScoreResult) -> None:
        row = session.scalar(
            select(PiotroskiScore).where(
                PiotroskiScore.symbol == result.symbol,
                PiotroskiScore.as_of_date == result.as_of_date,
            )
        )
        payload = {
            "f_score": result.f_score,
            "vote_yes": result.vote_yes,
            "data_complete": result.data_complete,
            "missing_fields": result.missing_fields,
            "signals_json": result.signals_json,
        }
        if row is None:
            session.add(PiotroskiScore(symbol=result.symbol, as_of_date=result.as_of_date, **payload))
            return
        for key, value in payload.items():
            setattr(row, key, value)

    @staticmethod
    def _upsert_minervini_score(session: Session, result: MinerviniScoreResult) -> None:
        row = session.scalar(
            select(MinerviniScore).where(
                MinerviniScore.symbol == result.symbol,
                MinerviniScore.as_of_date == result.as_of_date,
            )
        )
        payload = {
            "passed_checks": result.passed_checks,
            "vote_yes": result.vote_yes,
            "rs_percentile": result.rs_percentile,
            "data_complete": result.data_complete,
            "missing_fields": result.missing_fields,
            "checks_json": result.checks_json,
        }
        if row is None:
            session.add(MinerviniScore(symbol=result.symbol, as_of_date=result.as_of_date, **payload))
            return
        for key, value in payload.items():
            setattr(row, key, value)

    def _combined_from_results(
        self,
        symbol: str,
        as_of_date: date,
        lynch: LynchScoreResult,
        piotroski: PiotroskiScoreResult,
        minervini: MinerviniScoreResult,
    ) -> CombinedInvestmentScore:
        votes_yes = sum(
            1 for vote in (lynch.vote_yes, piotroski.vote_yes, minervini.vote_yes) if vote
        )
        return CombinedInvestmentScore(
            symbol=symbol,
            as_of_date=as_of_date,
            votes_yes=votes_yes,
            lynch_vote=lynch.vote_yes,
            piotroski_vote=piotroski.vote_yes,
            minervini_vote=minervini.vote_yes,
            label=self._label(votes_yes),
            lynch_value=lynch.lynch_value,
            piotroski_f_score=piotroski.f_score,
            minervini_passed_checks=minervini.passed_checks,
            minervini_rs_percentile=minervini.rs_percentile,
        )

    def score_symbol(self, symbol: str, as_of_date: date | None = None) -> CombinedInvestmentScore | None:
        summary = self.score_universe(symbols=[symbol], as_of_date=as_of_date)
        results = list(summary.get("results") or [])
        return results[0] if results else None

    def score_universe(self, symbols: list[str] | None = None, as_of_date: date | None = None) -> dict[str, Any]:
        normalized_symbols = [symbol.upper().strip() for symbol in symbols or [] if symbol]
        with session_scope() as session:
            resolved_as_of_date = self._resolve_as_of_date(session, as_of_date)
            if resolved_as_of_date is None:
                return {
                    "as_of_date": None,
                    "requested": len(normalized_symbols),
                    "processed": 0,
                    "strong_buy": 0,
                    "watchlist": 0,
                    "no_action": 0,
                    "results": [],
                    "failed_examples": {},
                }
            snapshots = self._load_snapshots(session, resolved_as_of_date, normalized_symbols or None)
            annual_periods = self._load_annual_periods(
                session,
                resolved_as_of_date,
                [snapshot.symbol for snapshot in snapshots],
            )

        frames, failures = self._load_frames([snapshot.symbol for snapshot in snapshots], resolved_as_of_date)
        rs_percentiles = self._compute_rs_percentiles(frames)

        lynch_results: dict[str, LynchScoreResult] = {}
        piotroski_results: dict[str, PiotroskiScoreResult] = {}
        minervini_results: dict[str, MinerviniScoreResult] = {}
        combined_results: list[CombinedInvestmentScore] = []

        for snapshot in snapshots:
            lynch = LynchPegScorer.score(snapshot, resolved_as_of_date)
            piotroski = PiotroskiScorer.score(
                snapshot,
                annual_periods.get(snapshot.symbol, []),
                resolved_as_of_date,
            )
            minervini = MinerviniScorer.score(
                snapshot.symbol,
                frames.get(snapshot.symbol),
                rs_percentiles.get(snapshot.symbol),
                resolved_as_of_date,
            )
            lynch_results[snapshot.symbol] = lynch
            piotroski_results[snapshot.symbol] = piotroski
            minervini_results[snapshot.symbol] = minervini
            combined_results.append(
                self._combined_from_results(snapshot.symbol, resolved_as_of_date, lynch, piotroski, minervini)
            )

        with session_scope() as session:
            for symbol in [snapshot.symbol for snapshot in snapshots]:
                self._upsert_lynch_score(session, lynch_results[symbol])
                self._upsert_piotroski_score(session, piotroski_results[symbol])
                self._upsert_minervini_score(session, minervini_results[symbol])

        strong_buy = sum(1 for result in combined_results if result.label == "STRONG_BUY")
        watchlist = sum(1 for result in combined_results if result.label == "WATCHLIST")
        no_action = sum(1 for result in combined_results if result.label == "NO_ACTION")
        return {
            "as_of_date": resolved_as_of_date.isoformat(),
            "requested": len(normalized_symbols) if normalized_symbols else len(snapshots),
            "processed": len(combined_results),
            "strong_buy": strong_buy,
            "watchlist": watchlist,
            "no_action": no_action,
            "results": combined_results,
            "failed_examples": dict(list(failures.items())[:10]),
        }


__all__ = ["CombinedInvestmentScore", "InvestmentScorer"]
