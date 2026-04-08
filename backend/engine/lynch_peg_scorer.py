from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from backend.db.models_investment import OfficialInvestmentSnapshot


@dataclass(slots=True)
class LynchScoreResult:
    symbol: str
    as_of_date: date
    lynch_value: float | None
    eps_growth_3y_cagr: float | None
    dividend_yield: float | None
    pe_ratio: float | None
    vote_yes: bool
    data_complete: bool
    missing_fields: list[str] = field(default_factory=list)
    details_json: dict[str, Any] = field(default_factory=dict)


class LynchPegScorer:
    YES_THRESHOLD = 1.5

    @classmethod
    def score(cls, snapshot: OfficialInvestmentSnapshot, as_of_date: date) -> LynchScoreResult:
        missing_fields: list[str] = []
        eps_growth = snapshot.eps_growth_3y_cagr
        dividend_yield = snapshot.dividend_yield
        pe_ratio = snapshot.pe_ratio

        if eps_growth is None:
            missing_fields.append("eps_growth_3y_cagr")
        if dividend_yield is None:
            missing_fields.append("dividend_yield")
        if pe_ratio is None:
            missing_fields.append("pe_ratio")
        elif pe_ratio <= 0:
            missing_fields.append("pe_ratio_non_positive")

        lynch_value: float | None = None
        if not missing_fields:
            lynch_value = (float(eps_growth) + float(dividend_yield)) / float(pe_ratio)

        vote_yes = bool(lynch_value is not None and lynch_value > cls.YES_THRESHOLD)
        details_json = {
            "formula": "(eps_growth_3y_cagr + dividend_yield) / pe_ratio",
            "threshold": cls.YES_THRESHOLD,
            "inputs": {
                "eps_growth_3y_cagr": eps_growth,
                "dividend_yield": dividend_yield,
                "pe_ratio": pe_ratio,
            },
        }
        return LynchScoreResult(
            symbol=snapshot.symbol,
            as_of_date=as_of_date,
            lynch_value=lynch_value,
            eps_growth_3y_cagr=eps_growth,
            dividend_yield=dividend_yield,
            pe_ratio=pe_ratio,
            vote_yes=vote_yes,
            data_complete=not missing_fields,
            missing_fields=missing_fields,
            details_json=details_json,
        )


__all__ = ["LynchPegScorer", "LynchScoreResult"]
