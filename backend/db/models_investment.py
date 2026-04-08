from __future__ import annotations

from backend.db.postgres import (
    GlobalRiskSnapshot,
    LynchScore,
    MinerviniScore,
    OfficialCorporateAction,
    OfficialFinancialPeriod,
    OfficialInvestmentSnapshot,
    OfficialMarketContextSnapshot,
    OfficialQuoteSnapshot,
    OfficialShareholdingSnapshot,
    PiotroskiScore,
    ScreenerCache,
)


DailyQuoteSnapshot = OfficialQuoteSnapshot
FinancialPeriod = OfficialFinancialPeriod
ShareholdingSnapshot = OfficialShareholdingSnapshot
CorporateActionRecord = OfficialCorporateAction
MarketContextSnapshot = OfficialMarketContextSnapshot
OfficialSnapshot = OfficialInvestmentSnapshot
GlobalRiskSnapshotRecord = GlobalRiskSnapshot
LynchScoreRecord = LynchScore
PiotroskiScoreRecord = PiotroskiScore
MinerviniScoreRecord = MinerviniScore
ScreenerCacheRecord = ScreenerCache


__all__ = [
    "DailyQuoteSnapshot",
    "FinancialPeriod",
    "ShareholdingSnapshot",
    "CorporateActionRecord",
    "MarketContextSnapshot",
    "OfficialSnapshot",
    "GlobalRiskSnapshotRecord",
    "LynchScoreRecord",
    "PiotroskiScoreRecord",
    "MinerviniScoreRecord",
    "ScreenerCacheRecord",
    "LynchScore",
    "PiotroskiScore",
    "MinerviniScore",
    "ScreenerCache",
    "OfficialQuoteSnapshot",
    "OfficialFinancialPeriod",
    "OfficialShareholdingSnapshot",
    "OfficialCorporateAction",
    "OfficialMarketContextSnapshot",
    "OfficialInvestmentSnapshot",
    "GlobalRiskSnapshot",
]
