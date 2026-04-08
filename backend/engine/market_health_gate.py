from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models_investment import OfficialMarketContextSnapshot
from backend.engine.global_risk_types import GlobalRiskThresholds
from backend.engine.investment_gate_types import GateResult


class MarketHealthGate:
    PANIC_VIX_THRESHOLD = 25.0

    @staticmethod
    def load_context(session: Session, as_of_date: date) -> OfficialMarketContextSnapshot | None:
        return session.scalar(
            select(OfficialMarketContextSnapshot)
            .where(OfficialMarketContextSnapshot.as_of_date <= as_of_date)
            .order_by(OfficialMarketContextSnapshot.as_of_date.desc())
        )

    @staticmethod
    def load_recent_contexts(session: Session, as_of_date: date, *, limit: int = 6) -> list[OfficialMarketContextSnapshot]:
        return session.scalars(
            select(OfficialMarketContextSnapshot)
            .where(OfficialMarketContextSnapshot.as_of_date <= as_of_date)
            .order_by(OfficialMarketContextSnapshot.as_of_date.desc())
            .limit(limit)
        ).all()

    @classmethod
    def _vix_velocity_details(cls, recent_contexts: list[OfficialMarketContextSnapshot] | None) -> dict[str, float | None | str]:
        rows = list(recent_contexts or [])
        if len(rows) < 6:
            return {
                "status": "insufficient_history",
                "current_vix": None,
                "vix_5day_avg": None,
                "vix_velocity_pct": None,
                "vix_velocity_severity": "NONE",
            }
        current_vix = rows[0].india_vix
        previous = [row.india_vix for row in rows[1:6] if row.india_vix is not None and float(row.india_vix) > 0]
        if current_vix is None or float(current_vix) <= 0 or len(previous) < 5:
            return {
                "status": "invalid_values",
                "current_vix": float(current_vix) if current_vix is not None else None,
                "vix_5day_avg": None,
                "vix_velocity_pct": None,
                "vix_velocity_severity": "NONE",
            }
        avg_vix = sum(float(value) for value in previous) / len(previous)
        velocity_pct = ((float(current_vix) - avg_vix) / avg_vix) * 100.0
        severity = "NONE"
        if velocity_pct >= GlobalRiskThresholds.VIX_VELOCITY_BLOCK:
            severity = "BLOCK"
        elif velocity_pct >= GlobalRiskThresholds.VIX_VELOCITY_CAUTION:
            severity = "CAUTION"
        return {
            "status": "ok",
            "current_vix": float(current_vix),
            "vix_5day_avg": round(avg_vix, 4),
            "vix_velocity_pct": round(velocity_pct, 4),
            "vix_velocity_severity": severity,
        }

    @classmethod
    def evaluate(
        cls,
        market_context: OfficialMarketContextSnapshot | None,
        *,
        recent_contexts: list[OfficialMarketContextSnapshot] | None = None,
    ) -> GateResult:
        if market_context is None:
            return GateResult(False, "market_context_missing", "Official market context is missing, so buys are blocked.", {})

        nifty_close = market_context.nifty50_close
        nifty_sma200 = market_context.nifty50_sma200
        india_vix = market_context.india_vix
        vix_velocity = cls._vix_velocity_details(recent_contexts)
        if nifty_close is None or nifty_sma200 is None or india_vix is None:
            return GateResult(
                False,
                "market_health_incomplete",
                "Market health inputs are incomplete, so buys are blocked.",
                {
                    "nifty50_close": nifty_close,
                    "nifty50_sma200": nifty_sma200,
                    "india_vix": india_vix,
                    **vix_velocity,
                },
            )

        reasons: list[str] = []
        if float(nifty_close) < float(nifty_sma200):
            reasons.append("nifty50_below_sma200")
        if float(india_vix) > cls.PANIC_VIX_THRESHOLD:
            reasons.append("india_vix_above_25")
        if str(vix_velocity.get("vix_velocity_severity")) == "BLOCK":
            reasons.append("india_vix_velocity_block")

        if reasons:
            return GateResult(
                False,
                "market_health_blocked",
                "Market health blocked new investment buys.",
                {
                    "reasons": reasons,
                    "nifty50_close": float(nifty_close),
                    "nifty50_sma200": float(nifty_sma200),
                    "india_vix": float(india_vix),
                    "vix_threshold": cls.PANIC_VIX_THRESHOLD,
                    **vix_velocity,
                },
            )

        return GateResult(
            True,
            "market_health_pass",
            "Market health supports new investment buys.",
            {
                "nifty50_close": float(nifty_close),
                "nifty50_sma200": float(nifty_sma200),
                "india_vix": float(india_vix),
                "vix_threshold": cls.PANIC_VIX_THRESHOLD,
                **vix_velocity,
            },
        )


__all__ = ["MarketHealthGate"]
