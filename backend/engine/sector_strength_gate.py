from __future__ import annotations

from typing import Any

from backend.db.models_investment import OfficialInvestmentSnapshot, OfficialMarketContextSnapshot
from backend.engine.fundamental_engine import infer_sector_label
from backend.engine.investment_gate_types import GateResult


def _normalize_sector_key(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").upper().strip() if ch.isalnum())


class SectorStrengthGate:
    @staticmethod
    def _sector_row(
        market_context: OfficialMarketContextSnapshot | None,
        sector: str | None,
    ) -> dict[str, Any] | None:
        if market_context is None or not isinstance(market_context.sector_context, dict):
            return None
        normalized_sector = _normalize_sector_key(sector)
        if not normalized_sector:
            return None
        for key, value in market_context.sector_context.items():
            if _normalize_sector_key(str(key)) == normalized_sector and isinstance(value, dict):
                return value
        return None

    @classmethod
    def evaluate(
        cls,
        snapshot: OfficialInvestmentSnapshot | None,
        market_context: OfficialMarketContextSnapshot | None,
    ) -> GateResult:
        if snapshot is None:
            return GateResult(
                False,
                "official_snapshot_missing",
                "Official investment snapshot is missing, so sector strength cannot be validated.",
                {},
            )

        sector = infer_sector_label(snapshot.symbol, snapshot.company_name, snapshot.sector)
        if not sector:
            return GateResult(
                False,
                "sector_missing",
                "Sector is missing for the symbol, so buys are blocked.",
                {"symbol": snapshot.symbol},
            )

        row = cls._sector_row(market_context, sector)
        if row is None:
            return GateResult(
                False,
                "sector_context_missing",
                "Sector index context is missing, so buys are blocked.",
                {"sector": sector},
            )

        close = row.get("close")
        sma50 = row.get("sma50")
        if close is None or sma50 is None:
            return GateResult(
                False,
                "sector_strength_incomplete",
                "Sector strength inputs are incomplete, so buys are blocked.",
                {"sector": sector, "close": close, "sma50": sma50},
            )

        if float(close) <= float(sma50):
            return GateResult(
                False,
                "sector_below_sma50",
                "Sector index is below its SMA50, so buys are blocked.",
                {"sector": sector, "close": float(close), "sma50": float(sma50)},
            )

        return GateResult(
            True,
            "sector_strength_pass",
            "Sector index is above its SMA50.",
            {"sector": sector, "close": float(close), "sma50": float(sma50)},
        )


__all__ = ["SectorStrengthGate"]
