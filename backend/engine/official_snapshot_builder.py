from __future__ import annotations

from datetime import date
from typing import Any

from backend.engine.official_investment_data_service import OfficialInvestmentDataService


class OfficialSnapshotBuilder:
    def __init__(self, *, data_service: OfficialInvestmentDataService | None = None) -> None:
        self.data_service = data_service or OfficialInvestmentDataService()

    def rebuild_daily_snapshot(self, *, as_of_date: date | None = None) -> dict[str, Any]:
        return self.data_service.rebuild_official_investment_snapshots(as_of_date=as_of_date)


__all__ = ["OfficialSnapshotBuilder"]
