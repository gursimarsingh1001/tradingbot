from __future__ import annotations

from backend.db.postgres import (
    OfficialCorporateAction,
    OfficialFinancialPeriod,
    OfficialInvestmentSnapshot,
    OfficialMarketContextSnapshot,
    OfficialQuoteSnapshot,
    OfficialShareholdingSnapshot,
)


DailyQuoteSnapshot = OfficialQuoteSnapshot
FinancialPeriod = OfficialFinancialPeriod
ShareholdingSnapshot = OfficialShareholdingSnapshot
CorporateActionRecord = OfficialCorporateAction
MarketContextSnapshot = OfficialMarketContextSnapshot
OfficialSnapshot = OfficialInvestmentSnapshot


__all__ = [
    "DailyQuoteSnapshot",
    "FinancialPeriod",
    "ShareholdingSnapshot",
    "CorporateActionRecord",
    "MarketContextSnapshot",
    "OfficialSnapshot",
    "OfficialQuoteSnapshot",
    "OfficialFinancialPeriod",
    "OfficialShareholdingSnapshot",
    "OfficialCorporateAction",
    "OfficialMarketContextSnapshot",
    "OfficialInvestmentSnapshot",
]
