from __future__ import annotations

from datetime import date
from typing import Any

from backend.engine.official_investment_data_service import OfficialInvestmentDataService


class ShadowComparisonService:
    def __init__(self, *, data_service: OfficialInvestmentDataService | None = None) -> None:
        self.data_service = data_service or OfficialInvestmentDataService()

    def compare(
        self,
        *,
        as_of_date: date | None = None,
        missing_bse_mapping_symbols: list[str] | None = None,
        recovered_by_bse_count: int = 0,
    ) -> dict[str, Any]:
        return self.data_service.compare_shadow_snapshots(
            as_of_date=as_of_date,
            missing_bse_mapping_symbols=missing_bse_mapping_symbols,
            recovered_by_bse_count=recovered_by_bse_count,
        )

    @staticmethod
    def build_summary(
        *,
        official_rows,
        legacy_rows,
        missing_bse_mapping_symbols: list[str] | None = None,
        recovered_by_bse_count: int = 0,
    ) -> dict[str, Any]:
        return OfficialInvestmentDataService._build_shadow_summary(
            official_rows=official_rows,
            legacy_rows=legacy_rows,
            missing_bse_mapping_symbols=missing_bse_mapping_symbols or [],
            recovered_by_bse_count=recovered_by_bse_count,
        )


__all__ = ["ShadowComparisonService"]
