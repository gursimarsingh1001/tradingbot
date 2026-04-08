from __future__ import annotations

from backend.db.models_investment import (
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
from backend.db.postgres import Base, engine


INVESTMENT_TABLES = [
    OfficialQuoteSnapshot.__table__,
    OfficialFinancialPeriod.__table__,
    OfficialShareholdingSnapshot.__table__,
    OfficialCorporateAction.__table__,
    OfficialMarketContextSnapshot.__table__,
    OfficialInvestmentSnapshot.__table__,
    ScreenerCache.__table__,
    GlobalRiskSnapshot.__table__,
    LynchScore.__table__,
    PiotroskiScore.__table__,
    MinerviniScore.__table__,
]


def run() -> None:
    Base.metadata.create_all(bind=engine, tables=INVESTMENT_TABLES)


if __name__ == "__main__":
    run()
