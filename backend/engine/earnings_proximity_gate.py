from __future__ import annotations

from datetime import date

from backend.db.models_investment import OfficialInvestmentSnapshot
from backend.engine.investment_gate_types import GateResult


class EarningsProximityGate:
    BLOCK_WINDOW_DAYS = 7

    @classmethod
    def evaluate(
        cls,
        snapshot: OfficialInvestmentSnapshot | None,
        as_of_date: date,
    ) -> GateResult:
        if snapshot is None:
            return GateResult(
                False,
                "official_snapshot_missing",
                "Official investment snapshot is missing, so earnings proximity cannot be checked.",
                {},
            )

        if snapshot.earnings_date is None:
            return GateResult(
                True,
                "earnings_date_unknown",
                "Earnings date is unknown, so the earnings gate did not block the trade.",
                {"symbol": snapshot.symbol},
            )

        days_until_earnings = (snapshot.earnings_date - as_of_date).days
        if 0 <= days_until_earnings <= cls.BLOCK_WINDOW_DAYS:
            return GateResult(
                False,
                "earnings_within_7_days",
                "Earnings are within the next 7 days, so buys are blocked.",
                {
                    "earnings_date": snapshot.earnings_date.isoformat(),
                    "days_until_earnings": days_until_earnings,
                },
            )

        return GateResult(
            True,
            "earnings_clear",
            "No near-term earnings event blocks the trade.",
            {
                "earnings_date": snapshot.earnings_date.isoformat(),
                "days_until_earnings": days_until_earnings,
            },
        )


__all__ = ["EarningsProximityGate"]
