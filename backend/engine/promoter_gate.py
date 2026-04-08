from __future__ import annotations

from backend.db.models_investment import OfficialInvestmentSnapshot
from backend.engine.investment_gate_types import GateResult


class PromoterGate:
    MIN_PROMOTER_HOLDING = 26.0
    MAX_PROMOTER_PLEDGE = 25.0
    MAX_PROMOTER_DROP = -3.0

    @classmethod
    def evaluate(cls, snapshot: OfficialInvestmentSnapshot | None) -> GateResult:
        if snapshot is None:
            return GateResult(
                False,
                "official_snapshot_missing",
                "Official investment snapshot is missing, so promoter quality cannot be checked.",
                {},
            )

        promoter_holding = snapshot.promoter_holding
        promoter_pledge = snapshot.promoter_pledge
        promoter_change = snapshot.promoter_holding_change_pct
        if promoter_holding is None or promoter_pledge is None or promoter_change is None:
            return GateResult(
                False,
                "promoter_data_incomplete",
                "Promoter data is incomplete, so buys are blocked.",
                {
                    "promoter_holding": promoter_holding,
                    "promoter_pledge": promoter_pledge,
                    "promoter_holding_change_pct": promoter_change,
                },
            )

        reasons: list[str] = []
        if float(promoter_holding) < cls.MIN_PROMOTER_HOLDING:
            reasons.append("promoter_holding_below_26")
        if float(promoter_pledge) > cls.MAX_PROMOTER_PLEDGE:
            reasons.append("promoter_pledge_above_25")
        if float(promoter_change) < cls.MAX_PROMOTER_DROP:
            reasons.append("promoter_holding_drop_gt_3")

        if reasons:
            return GateResult(
                False,
                "promoter_gate_blocked",
                "Promoter quality checks blocked the trade.",
                {
                    "reasons": reasons,
                    "promoter_holding": float(promoter_holding),
                    "promoter_pledge": float(promoter_pledge),
                    "promoter_holding_change_pct": float(promoter_change),
                },
            )

        return GateResult(
            True,
            "promoter_pass",
            "Promoter quality checks passed.",
            {
                "promoter_holding": float(promoter_holding),
                "promoter_pledge": float(promoter_pledge),
                "promoter_holding_change_pct": float(promoter_change),
            },
        )


__all__ = ["PromoterGate"]
