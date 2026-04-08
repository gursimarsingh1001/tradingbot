from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isnan
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.config import get_settings
from backend.data.angel_one_client import AngelOneClient, get_angel_one_client
from backend.data.bse_client import BSEClient, get_bse_client
from backend.data.historical_fetcher import HistoricalFetcher, SymbolConfig
from backend.data.moneycontrol_client import MoneycontrolClient, get_moneycontrol_client
from backend.data.nse_client import NSEClient, get_nse_client
from backend.data.screener_client import ScreenerClient, ScreenerCompanyData, get_screener_client
from backend.db.models_investment import (
    OfficialCorporateAction,
    OfficialFinancialPeriod,
    OfficialInvestmentSnapshot,
    OfficialMarketContextSnapshot,
    OfficialQuoteSnapshot,
    OfficialShareholdingSnapshot,
    ScreenerCache,
)
from backend.db.postgres import (
    StockFundamentalSnapshot,
    get_config_value,
    session_scope,
    upsert_config_value,
)
from backend.engine.data_reconciler import DataReconciler
from backend.engine.fundamental_engine import infer_sector_label
from backend.logging_utils import get_logger


settings = get_settings()
logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class OfficialSectorIndexTarget:
    sector: str
    symbol: str
    token: str
    exchange: str
    trading_symbol: str


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if isnan(parsed):
        return None
    return parsed


class OfficialInvestmentDataService:
    MARKET_CONTEXT_LOOKBACK_DAYS = 420
    DAILY_UNIVERSE_LIMIT = 900

    def __init__(
        self,
        *,
        nse_client: NSEClient | None = None,
        bse_client: BSEClient | None = None,
        screener_client: ScreenerClient | None = None,
        moneycontrol_client: MoneycontrolClient | None = None,
        historical_fetcher: HistoricalFetcher | None = None,
        angel_client: AngelOneClient | None = None,
    ) -> None:
        self.nse_client = nse_client or get_nse_client()
        self.bse_client = bse_client or get_bse_client()
        self.screener_client = screener_client or get_screener_client()
        self.moneycontrol_client = moneycontrol_client or get_moneycontrol_client()
        self.historical_fetcher = historical_fetcher or HistoricalFetcher()
        self.angel_client = angel_client or get_angel_one_client()
        self.reconciler = DataReconciler(tolerance=settings.cross_validation_tolerance)

    @staticmethod
    def _today_local() -> date:
        return datetime.now(tz=settings.tzinfo).date()

    @staticmethod
    def _normalize_match_key(value: Any) -> str:
        text = str(value or "").upper()
        text = re.sub(r"\b(LIMITED|LTD)\b", "", text)
        return re.sub(r"[^A-Z0-9]+", "", text)

    @classmethod
    def _moneycontrol_lookup_keys(cls, config: SymbolConfig) -> set[str]:
        trading_symbol = (
            str(config.trading_symbol or "")
            .upper()
            .replace("-EQ", "")
            .replace("-BE", "")
            .replace("-BZ", "")
            .replace("-SM", "")
        )
        keys = {
            cls._normalize_match_key(config.symbol),
            cls._normalize_match_key(trading_symbol),
            cls._normalize_match_key(config.company_name),
        }
        return {key for key in keys if key}

    @classmethod
    def _index_moneycontrol_events(
        cls,
        symbol_configs: list[SymbolConfig],
        events: list[Any],
    ) -> dict[str, list[Any]]:
        indexed: dict[str, list[Any]] = {}
        for config in symbol_configs:
            config_keys = cls._moneycontrol_lookup_keys(config)
            matches: list[Any] = []
            for event in events:
                event_keys = {
                    cls._normalize_match_key(event.symbol),
                    cls._normalize_match_key(event.company_name),
                    cls._normalize_match_key((event.raw_payload or {}).get("stockShortName")),
                    cls._normalize_match_key((event.raw_payload or {}).get("scId")),
                }
                event_keys = {key for key in event_keys if key}
                if config_keys.intersection(event_keys):
                    matches.append(event)
            matches.sort(key=lambda item: item.earnings_date or date.max)
            if matches:
                indexed[config.symbol.upper()] = matches
        return indexed

    @staticmethod
    def _upsert_record(session, model, filters: dict[str, Any], values: dict[str, Any]):
        existing = session.scalar(select(model).filter_by(**filters))
        if existing is None:
            session.add(model(**filters, **values))
            return
        for key, value in values.items():
            setattr(existing, key, value)

    @staticmethod
    def _load_json_file(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return payload if isinstance(payload, list) else []

    @classmethod
    def _load_sector_targets(cls) -> list[OfficialSectorIndexTarget]:
        targets: list[OfficialSectorIndexTarget] = []
        for item in cls._load_json_file(Path(settings.official_sector_index_config_path)):
            if not isinstance(item, dict):
                continue
            sector = str(item.get("sector") or "").strip().upper()
            symbol = str(item.get("symbol") or "").strip().upper()
            token = str(item.get("token") or "").strip()
            exchange = str(item.get("exchange") or "NSE").strip().upper()
            trading_symbol = str(item.get("tradingSymbol") or item.get("trading_symbol") or symbol).strip()
            if sector and symbol and token:
                targets.append(
                    OfficialSectorIndexTarget(
                        sector=sector,
                        symbol=symbol,
                        token=token,
                        exchange=exchange,
                        trading_symbol=trading_symbol,
                    )
                )
        return targets

    def investment_universe(self, *, limit: int | None = None) -> list[SymbolConfig]:
        selected = self.historical_fetcher.select_symbols(limit=None)
        limit = self.DAILY_UNIVERSE_LIMIT if limit is None else limit
        return selected[:limit] if limit and limit > 0 else selected

    @staticmethod
    def _calc_margin(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator in (None, 0):
            return None
        return (numerator / denominator) * 100.0

    @staticmethod
    def _calc_ratio(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator in (None, 0):
            return None
        return numerator / denominator

    @classmethod
    def _derive_roa(cls, net_profit: float | None, total_assets: float | None) -> float | None:
        return cls._calc_ratio(net_profit, total_assets)

    @classmethod
    def _derive_asset_turnover(cls, revenue: float | None, total_assets: float | None) -> float | None:
        return cls._calc_ratio(revenue, total_assets)

    @staticmethod
    def _calc_yoy_growth(current: float | None, previous: float | None) -> float | None:
        if current is None or previous in (None, 0):
            return None
        return ((current - previous) / abs(previous)) * 100.0

    @staticmethod
    def _calc_eps_ttm(quarters: list[OfficialFinancialPeriod]) -> float | None:
        if len(quarters) < 4:
            return None
        values = [period.eps_basic for period in quarters[:4] if period.eps_basic is not None]
        if len(values) != 4:
            return None
        return sum(values)

    @classmethod
    def _calc_eps_growth_cagr(cls, quarters: list[OfficialFinancialPeriod]) -> float | None:
        if len(quarters) < 16:
            return None
        current_ttm = cls._calc_eps_ttm(quarters[:4])
        prior_ttm = cls._calc_eps_ttm(quarters[12:16])
        if current_ttm is None or prior_ttm in (None, 0) or current_ttm <= 0 or prior_ttm <= 0:
            return None
        return (((current_ttm / prior_ttm) ** (1 / 3)) - 1) * 100.0

    @staticmethod
    def _holding_change(current: float | None, previous: float | None) -> float | None:
        if current is None or previous is None:
            return None
        return current - previous

    @classmethod
    def _normalize_financial_period_metrics(cls, item: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(item)
        total_assets = _safe_float(normalized.get("total_assets"))
        normalized["total_assets"] = total_assets
        if normalized.get("roa") is None:
            normalized["roa"] = cls._derive_roa(_safe_float(normalized.get("net_profit")), total_assets)
        if normalized.get("asset_turnover") is None:
            normalized["asset_turnover"] = cls._derive_asset_turnover(
                _safe_float(normalized.get("revenue")),
                total_assets,
            )
        return normalized

    @staticmethod
    def _board_meeting_action_types() -> tuple[str, ...]:
        return ("BOARD_MEETING_RESULTS", "MONEYCONTROL_EARNINGS")

    @staticmethod
    def _cache_age_days(fetched_at: datetime | None, *, reference: datetime) -> int | None:
        if fetched_at is None:
            return None
        return max(0, (reference.date() - fetched_at.date()).days)

    @classmethod
    def _screener_cache_stale(cls, row: ScreenerCache | None, *, reference: datetime) -> bool:
        if row is None or row.fetched_at is None:
            return True
        age = cls._cache_age_days(row.fetched_at, reference=reference)
        return age is None or age > settings.screener_cache_stale_days

    @staticmethod
    def _merge_prefer_existing(existing: Any, fallback: Any) -> Any:
        return existing if existing is not None else fallback

    @staticmethod
    def _derive_week_52_range(frame: Any) -> tuple[float | None, float | None]:
        if frame is None or getattr(frame, "empty", True):
            return (None, None)
        try:
            recent = frame.tail(252)
        except Exception:
            recent = frame
        try:
            high_series = recent.get("High")
            low_series = recent.get("Low")
            if high_series is not None and low_series is not None:
                week_52_high = _safe_float(high_series.max())
                week_52_low = _safe_float(low_series.min())
                if week_52_high is not None or week_52_low is not None:
                    return (week_52_high, week_52_low)
            close_series = recent.get("Close")
            if close_series is not None:
                close_high = _safe_float(close_series.max())
                close_low = _safe_float(close_series.min())
                return (close_high, close_low)
        except Exception:
            return (None, None)
        return (None, None)

    @classmethod
    def _enrich_period_from_screener(
        cls,
        row: OfficialFinancialPeriod | None,
        screener_flat: dict[str, Any],
        *,
        previous: bool = False,
    ) -> dict[str, Any]:
        prefix = "previous_annual_" if previous else "latest_annual_"
        revenue = screener_flat.get(f"{prefix}revenue")
        net_profit = screener_flat.get(f"{prefix}net_profit")
        operating_margin = screener_flat.get(f"{prefix}operating_margin")
        total_assets = screener_flat.get("total_assets")
        operating_cash_flow = screener_flat.get("operating_cash_flow")
        total_debt = screener_flat.get("total_debt")
        current_assets = screener_flat.get("current_assets")
        current_liabilities = screener_flat.get("current_liabilities")
        shares_outstanding = screener_flat.get("shares_outstanding")
        return {
            "revenue": cls._merge_prefer_existing(row.revenue if row else None, revenue),
            "net_profit": cls._merge_prefer_existing(row.net_profit if row else None, net_profit),
            "operating_profit": cls._merge_prefer_existing(
                row.operating_profit if row else None,
                (None if revenue in (None, 0) or operating_margin is None else (revenue * operating_margin) / 100.0),
            ),
            "eps_basic": cls._merge_prefer_existing(row.eps_basic if row else None, None),
            "operating_cash_flow": cls._merge_prefer_existing(row.operating_cash_flow if row else None, operating_cash_flow),
            "total_debt": cls._merge_prefer_existing(row.total_debt if row else None, total_debt),
            "total_assets": cls._merge_prefer_existing(row.total_assets if row else None, total_assets),
            "current_assets": cls._merge_prefer_existing(row.current_assets if row else None, current_assets),
            "current_liabilities": cls._merge_prefer_existing(row.current_liabilities if row else None, current_liabilities),
            "gross_margin": cls._merge_prefer_existing(row.gross_margin if row else None, None if operating_margin is None else operating_margin / 100.0),
            "asset_turnover": cls._merge_prefer_existing(
                row.asset_turnover if row else None,
                cls._derive_asset_turnover(revenue, total_assets),
            ),
            "roa": cls._merge_prefer_existing(
                row.roa if row else None,
                cls._derive_roa(net_profit, total_assets),
            ),
            "shares_outstanding": cls._merge_prefer_existing(row.shares_outstanding if row else None, shares_outstanding),
        }

    @staticmethod
    def _can_overwrite_screener_period(row: OfficialFinancialPeriod | None) -> bool:
        if row is None:
            return True
        if str(row.source_status or "").upper() == "SCREENER_ONLY":
            return True
        raw_payload = row.raw_payload or {}
        return bool(isinstance(raw_payload, dict) and raw_payload.get("hybrid_screener_enrichment"))

    @staticmethod
    def _quarter_end_pair(reference_date: date) -> tuple[date, date]:
        quarter_candidates: list[date] = []
        for year in (reference_date.year, reference_date.year - 1):
            for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
                candidate = date(year, month, day)
                if candidate <= reference_date:
                    quarter_candidates.append(candidate)
        quarter_candidates = sorted(set(quarter_candidates), reverse=True)
        latest = quarter_candidates[0] if quarter_candidates else reference_date
        previous = quarter_candidates[1] if len(quarter_candidates) > 1 else latest - timedelta(days=92)
        return latest, previous

    @classmethod
    def _upsert_screener_shareholding_snapshot(
        cls,
        session,
        *,
        symbol: str,
        as_of_date: date,
        values: dict[str, Any],
        fetched_at: datetime | None,
        source_url: str | None,
    ) -> bool:
        tracked_fields = ("promoter_holding", "fii_holding", "dii_holding")
        if not any(values.get(field) is not None for field in tracked_fields):
            return False
        existing = session.scalar(
            select(OfficialShareholdingSnapshot).where(
                OfficialShareholdingSnapshot.symbol == symbol,
                OfficialShareholdingSnapshot.as_of_date == as_of_date,
            )
        )
        enrichment_payload = {
            "hybrid_screener_enrichment": {
                "applied": True,
                "source_url": source_url,
                "fetched_at": fetched_at.isoformat() if fetched_at is not None else None,
            }
        }
        if existing is None:
            session.add(
                OfficialShareholdingSnapshot(
                    symbol=symbol,
                    as_of_date=as_of_date,
                    promoter_holding=values.get("promoter_holding"),
                    promoter_pledge=None,
                    fii_holding=values.get("fii_holding"),
                    dii_holding=values.get("dii_holding"),
                    source_status="SCREENER_ONLY",
                    raw_payload=enrichment_payload,
                )
            )
            return True

        updated = False
        overwrite_existing = str(existing.source_status or "").upper() == "SCREENER_ONLY"
        for field in tracked_fields:
            value = values.get(field)
            if value is None:
                continue
            if overwrite_existing or getattr(existing, field) is None:
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    updated = True
        raw_payload = dict(existing.raw_payload or {})
        raw_payload.update(enrichment_payload)
        if raw_payload != (existing.raw_payload or {}):
            existing.raw_payload = raw_payload
            updated = True
        if updated:
            if overwrite_existing or not existing.source_status:
                existing.source_status = "SCREENER_ONLY"
            else:
                existing.source_status = "NSE+SCREENER"
        return updated

    @staticmethod
    def _hybrid_candidates(
        *,
        official_source: str,
        official_value: Any,
        screener_value: Any,
        screener_stale: bool,
    ) -> list[dict[str, Any]]:
        official_candidate = {"source": official_source, "value": official_value}
        screener_candidate = {"source": "SCREENER", "value": screener_value, "stale": screener_stale}
        if screener_value is not None and not screener_stale:
            return [screener_candidate, official_candidate]
        return [official_candidate, screener_candidate]

    @staticmethod
    def _earnings_date_from_actions(actions: list[OfficialCorporateAction], *, as_of_date: date) -> date | None:
        upcoming = [
            action.ex_date
            for action in actions
            if action.ex_date is not None and action.ex_date >= as_of_date and action.action_type in OfficialInvestmentDataService._board_meeting_action_types()
        ]
        return min(upcoming) if upcoming else None

    @staticmethod
    def _screener_cache_payload(row: ScreenerCache | None) -> dict[str, Any]:
        return dict(row.data_json or {}) if row is not None and isinstance(row.data_json, dict) else {}

    @classmethod
    def _flatten_screener_cache(cls, row: ScreenerCache | None) -> dict[str, Any]:
        payload = cls._screener_cache_payload(row)
        if not payload:
            return {}
        try:
            screener_data = ScreenerCompanyData(
                symbol=str(payload.get("symbol") or row.symbol or ""),
                company_name=payload.get("company_name"),
                screener_slug=payload.get("screener_slug"),
                source_url=payload.get("source_url"),
                fetched_at=payload.get("fetched_at"),
                top_ratios=dict(payload.get("top_ratios") or {}),
                quarterly_ttm=dict(payload.get("quarterly_ttm") or {}),
                annual_latest=dict(payload.get("annual_latest") or {}),
                annual_previous=dict(payload.get("annual_previous") or {}),
                balance_sheet=dict(payload.get("balance_sheet") or {}),
                cash_flow=dict(payload.get("cash_flow") or {}),
                ratios=dict(payload.get("ratios") or {}),
                shareholding_latest=dict(payload.get("shareholding_latest") or {}),
                shareholding_previous=dict(payload.get("shareholding_previous") or {}),
                computed=dict(payload.get("computed") or {}),
                raw_sections=dict(payload.get("raw_sections") or {}),
            )
            flat = screener_data.to_flat_dict()
        except Exception:
            flat = {
                "symbol": payload.get("symbol") or (row.symbol if row is not None else None),
                "company_name": payload.get("company_name"),
                "screener_slug": payload.get("screener_slug"),
                "source_url": payload.get("source_url"),
                "fetched_at": payload.get("fetched_at"),
            }
        if flat.get("market_cap") is None:
            flat["market_cap"] = flat.get("market_cap_crores")
        return flat

    @staticmethod
    def _official_missing_field_counts(rows: list[OfficialInvestmentSnapshot]) -> dict[str, int]:
        tracked_fields = (
            "market_cap",
            "pe_ratio",
            "pb_ratio",
            "dividend_yield",
            "industry_pe",
            "eps_ttm",
            "eps_growth_3y_cagr",
            "revenue_growth_pct",
            "profit_growth_pct",
            "operating_margin",
            "net_margin",
            "roe",
            "roce",
            "debt_to_equity",
            "current_ratio",
            "promoter_holding",
            "promoter_pledge",
            "fii_holding",
            "dii_holding",
            "earnings_date",
        )
        counts = {field: 0 for field in tracked_fields}
        for row in rows:
            for field in tracked_fields:
                if getattr(row, field) is None:
                    counts[field] += 1
        return counts

    @classmethod
    def _build_shadow_summary(
        cls,
        *,
        official_rows: list[OfficialInvestmentSnapshot],
        legacy_rows: list[StockFundamentalSnapshot],
        missing_bse_mapping_symbols: list[str],
        recovered_by_bse_count: int,
    ) -> dict[str, Any]:
        legacy_by_symbol = {row.symbol: row for row in legacy_rows if row.symbol}
        material_differences = {
            "pe_ratio": 0,
            "revenue_growth_pct": 0,
            "profit_growth_pct": 0,
            "roe": 0,
            "debt_to_equity": 0,
            "promoter_holding": 0,
        }
        comparison_count = 0
        for row in official_rows:
            legacy = legacy_by_symbol.get(row.symbol)
            if legacy is None:
                continue
            comparison_count += 1
            if row.pe_ratio is not None and legacy.pe_ratio is not None and abs(row.pe_ratio - legacy.pe_ratio) > max(1.0, abs(legacy.pe_ratio) * 0.10):
                material_differences["pe_ratio"] += 1
            if row.revenue_growth_pct is not None and legacy.revenue_growth_pct is not None and abs(row.revenue_growth_pct - legacy.revenue_growth_pct) > 5.0:
                material_differences["revenue_growth_pct"] += 1
            if row.profit_growth_pct is not None and legacy.profit_growth_pct is not None and abs(row.profit_growth_pct - legacy.profit_growth_pct) > 5.0:
                material_differences["profit_growth_pct"] += 1
            if row.roe is not None and legacy.roe is not None and abs(row.roe - legacy.roe) > 3.0:
                material_differences["roe"] += 1
            if row.debt_to_equity is not None and legacy.debt_to_equity is not None and abs(row.debt_to_equity - legacy.debt_to_equity) > 0.2:
                material_differences["debt_to_equity"] += 1
            if row.promoter_holding is not None and legacy.promoter_holding is not None and abs(row.promoter_holding - legacy.promoter_holding) > 1.0:
                material_differences["promoter_holding"] += 1
        return {
            "generatedAt": datetime.now(tz=settings.tzinfo).isoformat(),
            "asOfDate": official_rows[0].as_of_date.isoformat() if official_rows and official_rows[0].as_of_date else None,
            "officialCoverage": len(official_rows),
            "legacyCoverage": len(legacy_rows),
            "coverageCompared": comparison_count,
            "missingBseMappings": len(missing_bse_mapping_symbols),
            "missingBseMappingSymbols": missing_bse_mapping_symbols[:25],
            "recoveredByBse": recovered_by_bse_count,
            "missingFieldCounts": cls._official_missing_field_counts(official_rows),
            "materialDifferences": material_differences,
            "sampleSymbols": [row.symbol for row in official_rows[:10]],
        }

    def refresh_screener_cache(
        self,
        *,
        symbol_configs: list[SymbolConfig] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        if not settings.hybrid_data_enabled or not settings.screener_enabled:
            return {"enabled": False, "requested": 0, "refreshed": 0, "used_cached": 0, "failed_examples": {}}
        symbol_configs = symbol_configs or self.investment_universe(limit=settings.screener_batch_size)
        now = datetime.now(tz=settings.tzinfo)
        refreshed = 0
        used_cached = 0
        failures: dict[str, str] = {}
        with session_scope() as session:
            existing_rows = session.scalars(
                select(ScreenerCache).where(ScreenerCache.symbol.in_([config.symbol for config in symbol_configs]))
            ).all()
            existing_by_symbol = {row.symbol.upper(): row for row in existing_rows if row.symbol}
            for config in symbol_configs:
                existing = existing_by_symbol.get(config.symbol.upper())
                if not force_refresh and existing is not None and not self._screener_cache_stale(existing, reference=now):
                    used_cached += 1
                    continue
                try:
                    parsed = self.screener_client.fetch_company_data(config.symbol)
                    self._upsert_record(
                        session,
                        ScreenerCache,
                        {"symbol": config.symbol},
                        {
                            "company_name": parsed.company_name or config.company_name,
                            "screener_slug": parsed.screener_slug,
                            "source_url": parsed.source_url,
                            "fetched_at": now,
                            "data_json": parsed.to_cache_payload(),
                            "raw_payload": {"source": "SCREENER"},
                        },
                    )
                    refreshed += 1
                except Exception as exc:
                    failures[config.symbol] = f"{type(exc).__name__}: {exc}"
        return {
            "enabled": True,
            "requested": len(symbol_configs),
            "refreshed": refreshed,
            "used_cached": used_cached,
            "failed_examples": dict(list(failures.items())[:10]),
        }

    def refresh_upcoming_earnings_calendar(
        self,
        *,
        symbol_configs: list[SymbolConfig] | None = None,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        if not settings.hybrid_data_enabled:
            return {"enabled": False, "requested": 0, "stored": 0, "failed_examples": {}}
        as_of_date = as_of_date or self._today_local()
        symbol_configs = symbol_configs or self.investment_universe(limit=None)
        requested = 0
        stored = 0
        failures: dict[str, str] = {}
        to_date = as_of_date + timedelta(days=7)
        moneycontrol_events_by_symbol: dict[str, list[Any]] = {}
        moneycontrol_prefetch_error: str | None = None
        if settings.moneycontrol_enabled:
            try:
                moneycontrol_events = self.moneycontrol_client.fetch_results_calendar_range(as_of_date, to_date)
                moneycontrol_events_by_symbol = self._index_moneycontrol_events(symbol_configs, moneycontrol_events)
            except Exception as exc:
                moneycontrol_prefetch_error = f"{type(exc).__name__}: {exc}"
                failures["MONEYCONTROL_PREFETCH"] = moneycontrol_prefetch_error
        with session_scope() as session:
            for config in symbol_configs:
                requested += 1
                events: list[dict[str, Any]] = []
                bse_error: str | None = None
                moneycontrol_error: str | None = None
                if settings.bse_board_meetings_enabled and config.bse_scripcode:
                    try:
                        payload = self.bse_client.fetch_upcoming_board_meetings(
                            config.bse_scripcode,
                            from_date=as_of_date.isoformat(),
                            to_date=to_date.isoformat(),
                        )
                        events.extend(self.bse_client.extract_board_meeting_events(config.symbol, config.bse_scripcode, payload))
                    except Exception as exc:
                        bse_error = f"BSE board meetings failed: {type(exc).__name__}: {exc}"
                if not events and settings.moneycontrol_enabled:
                    try:
                        mc_event = next(
                            (
                                event
                                for event in moneycontrol_events_by_symbol.get(config.symbol.upper(), [])
                                if event.earnings_date is not None
                            ),
                            None,
                        )
                        if mc_event is not None and mc_event.earnings_date is not None:
                            events.append(
                                {
                                    "symbol": config.symbol,
                                    "meeting_date": mc_event.earnings_date,
                                    "action_type": "MONEYCONTROL_EARNINGS",
                                    "description": f"Moneycontrol earnings calendar fallback for {config.company_name}",
                                    "raw_payload": mc_event.raw_payload,
                                }
                            )
                    except Exception as exc:
                        moneycontrol_error = f"Moneycontrol failed: {type(exc).__name__}: {exc}"
                if not events:
                    if moneycontrol_error:
                        failures[config.symbol] = " | ".join(
                            error for error in (bse_error, moneycontrol_error) if error
                        )
                    elif not settings.moneycontrol_enabled and bse_error:
                        failures[config.symbol] = bse_error
                    elif moneycontrol_prefetch_error and bse_error:
                        failures[config.symbol] = " | ".join(
                            error for error in (bse_error, f"Moneycontrol prefetch failed: {moneycontrol_prefetch_error}") if error
                        )
                for event in events:
                    self._upsert_record(
                        session,
                        OfficialCorporateAction,
                        {
                            "symbol": config.symbol,
                            "ex_date": event["meeting_date"],
                            "action_type": event["action_type"],
                        },
                        {
                            "description": event.get("description"),
                            "source_status": event["action_type"],
                            "raw_payload": event.get("raw_payload") or {},
                        },
                    )
                    stored += 1
        return {
            "enabled": True,
            "requested": requested,
            "stored": stored,
            "failed_examples": dict(list(failures.items())[:10]),
        }

    def refresh_quote_snapshots(self, *, symbol_configs: list[SymbolConfig] | None = None, as_of_date: date | None = None) -> dict[str, Any]:
        as_of_date = as_of_date or self._today_local()
        symbol_configs = symbol_configs or self.investment_universe(limit=self.DAILY_UNIVERSE_LIMIT)
        stored = 0
        recovered_by_bse = 0
        missing_bse_mappings: list[str] = []
        failures: dict[str, str] = {}
        with session_scope() as session:
            for config in symbol_configs:
                nse_payload: dict[str, Any] | None = None
                bse_payload: dict[str, Any] | None = None
                used_bse_fallback = False
                source_status = "NSE_ONLY"
                try:
                    nse_payload = self.nse_client.fetch_quote_equity(config.symbol)
                except Exception as exc:
                    failures[config.symbol] = f"NSE quote failed: {type(exc).__name__}: {exc}"
                metrics = self.nse_client.extract_quote_metrics(config.symbol, nse_payload or {})
                if (metrics["market_cap"] is None or metrics["pe_ratio"] is None or metrics["week_52_high"] is None) and config.bse_scripcode:
                    try:
                        bse_payload = self.bse_client.fetch_stock_info(config.bse_scripcode)
                        bse_metrics = self.bse_client.extract_stock_info_metrics(config.symbol, config.bse_scripcode, bse_payload)
                        for key in ("market_cap", "pe_ratio", "pb_ratio", "dividend_yield", "week_52_high", "week_52_low"):
                            if metrics.get(key) is None and bse_metrics.get(key) is not None:
                                metrics[key] = bse_metrics[key]
                                used_bse_fallback = True
                        if used_bse_fallback:
                            recovered_by_bse += 1
                            source_status = "NSE+BSE"
                    except Exception as exc:
                        failures[config.symbol] = f"{failures.get(config.symbol, '')} | BSE quote failed: {type(exc).__name__}: {exc}".strip(" |")
                elif (metrics["market_cap"] is None or metrics["pe_ratio"] is None) and not config.bse_scripcode:
                    missing_bse_mappings.append(config.symbol)
                if metrics.get("week_52_high") is None or metrics.get("week_52_low") is None:
                    try:
                        frame = self.historical_fetcher.fetch_symbol_frame(config)
                        derived_high, derived_low = self._derive_week_52_range(frame)
                        if metrics.get("week_52_high") is None and derived_high is not None:
                            metrics["week_52_high"] = derived_high
                        if metrics.get("week_52_low") is None and derived_low is not None:
                            metrics["week_52_low"] = derived_low
                    except Exception:
                        pass
                if nse_payload is None and bse_payload is not None:
                    source_status = "BSE_ONLY"
                elif nse_payload is None and bse_payload is None:
                    source_status = "FAILED"
                self._upsert_record(
                    session,
                    OfficialQuoteSnapshot,
                    {"symbol": config.symbol, "as_of_date": as_of_date},
                    {
                        "company_name": config.company_name,
                        "sector": infer_sector_label(config.symbol, config.company_name, config.sector),
                        "source_status": source_status,
                        "used_bse_fallback": used_bse_fallback,
                        "market_cap": metrics.get("market_cap"),
                        "pe_ratio": metrics.get("pe_ratio"),
                        "metadata_json": {
                            "pb_ratio": metrics.get("pb_ratio"),
                            "isin": config.isin,
                            "bse_scripcode": config.bse_scripcode,
                            "canonical_exchange": config.canonical_exchange,
                        },
                        "dividend_yield": metrics.get("dividend_yield"),
                        "industry_pe": metrics.get("industry_pe"),
                        "week_52_high": metrics.get("week_52_high"),
                        "week_52_low": metrics.get("week_52_low"),
                        "raw_payload": {"nse": nse_payload, "bse": bse_payload},
                    },
                )
                stored += 1
            upsert_config_value(
                session,
                settings.official_shadow_quote_state_key,
                {
                    "lastRunAt": datetime.now(tz=settings.tzinfo).isoformat(),
                    "lastRequested": len(symbol_configs),
                    "lastStored": stored,
                    "lastRecoveredByBse": recovered_by_bse,
                    "missingBseMappings": len(missing_bse_mappings),
                    "failedExamples": dict(list(failures.items())[:10]),
                },
            )
        return {
            "requested": len(symbol_configs),
            "stored": stored,
            "recovered_by_bse": recovered_by_bse,
            "missing_bse_mappings": missing_bse_mappings,
            "failed_examples": dict(list(failures.items())[:10]),
            "as_of_date": as_of_date.isoformat(),
        }

    def refresh_corporate_actions(self, *, symbol_configs: list[SymbolConfig] | None = None, as_of_date: date | None = None) -> dict[str, Any]:
        as_of_date = as_of_date or self._today_local()
        symbol_configs = symbol_configs or self.investment_universe(limit=self.DAILY_UNIVERSE_LIMIT)
        stored = 0
        failures: dict[str, str] = {}
        with session_scope() as session:
            for config in symbol_configs:
                try:
                    payload = self.nse_client.fetch_corporate_actions(config.symbol)
                    actions = self.nse_client.extract_corporate_actions(config.symbol, payload)
                except Exception as exc:
                    failures[config.symbol] = f"{type(exc).__name__}: {exc}"
                    continue
                for action in actions:
                    self._upsert_record(
                        session,
                        OfficialCorporateAction,
                        {
                            "symbol": config.symbol,
                            "ex_date": action["ex_date"],
                            "action_type": action["action_type"],
                        },
                        {
                            "description": action.get("description"),
                            "source_status": "NSE_ONLY",
                            "raw_payload": action.get("raw_payload") or {},
                        },
                    )
                    stored += 1
        return {
            "requested": len(symbol_configs),
            "stored": stored,
            "failed_examples": dict(list(failures.items())[:10]),
            "as_of_date": as_of_date.isoformat(),
        }

    def refresh_market_context(self, *, as_of_date: date | None = None) -> dict[str, Any]:
        as_of_date = as_of_date or self._today_local()
        stop = datetime.now(tz=settings.tzinfo)
        start = stop - timedelta(days=self.MARKET_CONTEXT_LOOKBACK_DAYS)
        nifty_frame = self.angel_client.get_historical_candles(
            "99926000",
            exchange="NSE",
            interval="ONE_DAY",
            from_date=start,
            to_date=stop,
        )
        nifty_close = None
        nifty_sma200 = None
        if not nifty_frame.empty:
            latest = nifty_frame.iloc[-1]
            nifty_close = _safe_float(latest.get("Close"))
            sma_200_series = nifty_frame["Close"].rolling(200).mean()
            nifty_sma200 = _safe_float(sma_200_series.iloc[-1]) if len(sma_200_series) else None

        sector_context: dict[str, Any] = {}
        for target in self._load_sector_targets():
            try:
                frame = self.angel_client.get_historical_candles(
                    target.token,
                    exchange=target.exchange,
                    interval="ONE_DAY",
                    from_date=start,
                    to_date=stop,
                )
            except Exception:
                continue
            if frame.empty:
                continue
            latest = frame.iloc[-1]
            close = _safe_float(latest.get("Close"))
            sma_50 = _safe_float(frame["Close"].rolling(50).mean().iloc[-1]) if len(frame) >= 50 else None
            sector_context[target.sector] = {
                "symbol": target.symbol,
                "close": close,
                "sma50": sma_50,
                "aboveSma50": bool(close is not None and sma_50 is not None and close > sma_50),
            }

        vix_payload: dict[str, Any] | None = None
        vix_error: str | None = None
        vix_source = "NSE_ONLY"
        with session_scope() as session:
            previous_context = session.scalar(
                select(OfficialMarketContextSnapshot)
                .where(OfficialMarketContextSnapshot.as_of_date < as_of_date)
                .order_by(OfficialMarketContextSnapshot.as_of_date.desc())
            )
        try:
            vix_payload = self.nse_client.fetch_all_indices()
            india_vix = self.nse_client.extract_india_vix(vix_payload)
        except Exception as exc:
            india_vix = previous_context.india_vix if previous_context is not None else None
            vix_error = f"{type(exc).__name__}: {exc}"
            vix_source = "STALE_PREVIOUS" if previous_context is not None and india_vix is not None else "UNAVAILABLE"
            logger.warning("Falling back for India VIX on %s because NSE allIndices failed: %s", as_of_date, vix_error)
        with session_scope() as session:
            self._upsert_record(
                session,
                OfficialMarketContextSnapshot,
                {"as_of_date": as_of_date},
                {
                    "nifty50_close": nifty_close,
                    "nifty50_sma200": nifty_sma200,
                    "india_vix": india_vix,
                    "aaa_bond_yield": settings.official_aaa_bond_yield,
                    "sector_context": sector_context,
                    "raw_payload": {
                        "nse_all_indices": vix_payload,
                        "india_vix_source": vix_source,
                        "india_vix_error": vix_error,
                    },
                },
            )
        return {
            "as_of_date": as_of_date.isoformat(),
            "nifty50_close": nifty_close,
            "nifty50_sma200": nifty_sma200,
            "india_vix": india_vix,
            "india_vix_source": vix_source,
            "sector_context_count": len(sector_context),
        }

    def refresh_weekly_fundamentals(self, *, symbol_configs: list[SymbolConfig] | None = None, batch_size: int | None = None) -> dict[str, Any]:
        symbol_configs = symbol_configs or self.investment_universe(limit=None)
        batch_size = batch_size or settings.official_weekly_batch_size
        with session_scope() as session:
            state = get_config_value(session, settings.official_shadow_weekly_state_key, {}) or {}
        offset = int(state.get("nextOffset") or 0)
        selected = symbol_configs[offset : offset + batch_size]
        if not selected:
            offset = 0
            selected = symbol_configs[:batch_size]
        requested = len(selected)
        processed = 0
        stored_periods = 0
        stored_shareholding = 0
        recovered_by_bse = 0
        missing_bse_mappings: list[str] = []
        failures: dict[str, str] = {}
        with session_scope() as session:
            for config in selected:
                processed += 1
                nse_shareholding_payload: dict[str, Any] | None = None
                bse_financial_payload: dict[str, Any] | None = None
                try:
                    nse_financial_payload = self.nse_client.fetch_financial_results(config.symbol, period="Quarterly")
                    nse_quarters = self.nse_client.extract_financial_periods(config.symbol, nse_financial_payload, period_type="QUARTERLY")
                except Exception as exc:
                    nse_quarters = []
                    failures[config.symbol] = f"NSE quarterly failed: {type(exc).__name__}: {exc}"
                try:
                    nse_annual_payload = self.nse_client.fetch_financial_results(config.symbol, period="Annual")
                    nse_annuals = self.nse_client.extract_financial_periods(config.symbol, nse_annual_payload, period_type="ANNUAL")
                except Exception as exc:
                    nse_annuals = []
                    failures[config.symbol] = f"{failures.get(config.symbol, '')} | NSE annual failed: {type(exc).__name__}: {exc}".strip(" |")
                try:
                    nse_shareholding_payload = self.nse_client.fetch_shareholding(config.symbol)
                    shareholding = self.nse_client.extract_shareholding_snapshot(config.symbol, nse_shareholding_payload)
                except Exception as exc:
                    shareholding = {"symbol": config.symbol, "as_of_date": None}
                    failures[config.symbol] = f"{failures.get(config.symbol, '')} | NSE shareholding failed: {type(exc).__name__}: {exc}".strip(" |")

                combined_periods = list(nse_quarters) + list(nse_annuals)
                if (not combined_periods or all(item.get("earnings_date") is None for item in combined_periods)) and config.bse_scripcode:
                    try:
                        bse_financial_payload = self.bse_client.fetch_financial_results(config.bse_scripcode)
                        bse_quarters = self.bse_client.extract_financial_periods(
                            config.symbol,
                            config.bse_scripcode,
                            bse_financial_payload,
                            period_type="QUARTERLY",
                        )
                        seen = {(item.get("period_type"), item.get("period_end")) for item in combined_periods}
                        for item in bse_quarters:
                            key = (item.get("period_type"), item.get("period_end"))
                            if key in seen:
                                continue
                            combined_periods.append(item)
                            seen.add(key)
                            recovered_by_bse += 1
                    except Exception as exc:
                        failures[config.symbol] = f"{failures.get(config.symbol, '')} | BSE financial failed: {type(exc).__name__}: {exc}".strip(" |")
                elif not config.bse_scripcode:
                    missing_bse_mappings.append(config.symbol)

                combined_annual_period_ends: set[date] = set()
                official_shareholding_dates: set[date] = set()
                for item in combined_periods:
                    normalized_item = self._normalize_financial_period_metrics(item)
                    if str(normalized_item.get("period_type") or "").upper() == "ANNUAL" and normalized_item.get("period_end") is not None:
                        combined_annual_period_ends.add(normalized_item["period_end"])
                    self._upsert_record(
                        session,
                        OfficialFinancialPeriod,
                        {
                            "symbol": config.symbol,
                            "period_type": str(normalized_item["period_type"]),
                            "period_end": normalized_item["period_end"],
                        },
                        {
                            "fiscal_label": normalized_item.get("fiscal_label"),
                            "earnings_date": normalized_item.get("earnings_date"),
                            "source_status": "NSE+BSE" if bse_financial_payload is not None else "NSE_ONLY",
                            "revenue": normalized_item.get("revenue"),
                            "net_profit": normalized_item.get("net_profit"),
                            "operating_profit": normalized_item.get("operating_profit"),
                            "ebit": normalized_item.get("ebit"),
                            "eps_basic": normalized_item.get("eps_basic"),
                            "operating_cash_flow": normalized_item.get("operating_cash_flow"),
                            "total_debt": normalized_item.get("total_debt"),
                            "total_assets": normalized_item.get("total_assets"),
                            "shareholder_equity": normalized_item.get("shareholder_equity"),
                            "capital_employed": normalized_item.get("capital_employed"),
                            "current_assets": normalized_item.get("current_assets"),
                            "current_liabilities": normalized_item.get("current_liabilities"),
                            "gross_margin": normalized_item.get("gross_margin"),
                            "asset_turnover": normalized_item.get("asset_turnover"),
                            "roa": normalized_item.get("roa"),
                            "shares_outstanding": normalized_item.get("shares_outstanding"),
                            "npa_pct": normalized_item.get("npa_pct"),
                            "capital_adequacy_pct": normalized_item.get("capital_adequacy_pct"),
                            "raw_payload": normalized_item.get("raw_payload") or {},
                        },
                    )
                    stored_periods += 1
                if shareholding.get("as_of_date") is not None:
                    official_shareholding_dates.add(shareholding["as_of_date"])
                    self._upsert_record(
                        session,
                        OfficialShareholdingSnapshot,
                        {
                            "symbol": config.symbol,
                            "as_of_date": shareholding["as_of_date"],
                        },
                        {
                            "promoter_holding": shareholding.get("promoter_holding"),
                            "promoter_pledge": shareholding.get("promoter_pledge"),
                            "fii_holding": shareholding.get("fii_holding"),
                            "dii_holding": shareholding.get("dii_holding"),
                            "source_status": "NSE_ONLY",
                            "raw_payload": nse_shareholding_payload or {},
                        },
                    )
                    stored_shareholding += 1

            screener_result = self.refresh_screener_cache(symbol_configs=selected, force_refresh=True)
            earnings_result = self.refresh_upcoming_earnings_calendar(symbol_configs=selected)

            if settings.hybrid_data_enabled and settings.screener_enabled:
                screener_rows = session.scalars(
                    select(ScreenerCache).where(ScreenerCache.symbol.in_([config.symbol for config in selected]))
                ).all()
                screener_by_symbol = {row.symbol.upper(): row for row in screener_rows if row.symbol}
                annual_rows = session.scalars(
                    select(OfficialFinancialPeriod).where(
                        OfficialFinancialPeriod.symbol.in_([config.symbol for config in selected]),
                        OfficialFinancialPeriod.period_type == "ANNUAL",
                    )
                ).all()
                annual_by_symbol: dict[str, list[OfficialFinancialPeriod]] = {}
                for row in annual_rows:
                    annual_by_symbol.setdefault(row.symbol.upper(), []).append(row)
                for symbol_rows in annual_by_symbol.values():
                    symbol_rows.sort(key=lambda item: item.period_end or date.min, reverse=True)
                for config in selected:
                    screener_row = screener_by_symbol.get(config.symbol.upper())
                    flat = self._flatten_screener_cache(screener_row)
                    if not flat:
                        continue
                    existing_rows = annual_by_symbol.get(config.symbol.upper(), [])
                    for index, period in enumerate(existing_rows[:2]):
                        hybrid_values = self._enrich_period_from_screener(period, flat, previous=index == 1)
                        overwrite_existing = self._can_overwrite_screener_period(period)
                        enriched_existing = False
                        for key, value in hybrid_values.items():
                            if value is None:
                                continue
                            if overwrite_existing or getattr(period, key) is None:
                                if getattr(period, key) != value:
                                    setattr(period, key, value)
                                    enriched_existing = True
                        raw_payload = dict(period.raw_payload or {})
                        raw_payload["hybrid_screener_enrichment"] = {
                            "applied": True,
                            "source_url": flat.get("source_url"),
                            "fetched_at": flat.get("fetched_at"),
                        }
                        if raw_payload != (period.raw_payload or {}):
                            period.raw_payload = raw_payload
                            enriched_existing = True
                        if enriched_existing:
                            if overwrite_existing or not period.source_status:
                                period.source_status = "SCREENER_ONLY"
                            else:
                                period.source_status = "NSE+SCREENER"
                            stored_periods += 1
                    reference_year = self._today_local().year - 1
                    desired_periods = [
                        (False, date(reference_year, 3, 31), "latest_annual_revenue"),
                        (True, date(reference_year - 1, 3, 31), "previous_annual_revenue"),
                    ]
                    existing_end_dates = {row.period_end for row in existing_rows if row.period_end is not None}
                    existing_end_dates.update(combined_annual_period_ends)
                    for is_previous, synthetic_period_end, revenue_key in desired_periods:
                        if flat.get(revenue_key) is None or synthetic_period_end in existing_end_dates:
                            continue
                        hybrid_values = self._enrich_period_from_screener(None, flat, previous=is_previous)
                        session.add(
                            OfficialFinancialPeriod(
                                symbol=config.symbol,
                                period_type="ANNUAL",
                                period_end=synthetic_period_end,
                                fiscal_label=str(synthetic_period_end.year),
                                source_status="SCREENER_ONLY",
                                revenue=hybrid_values.get("revenue"),
                                net_profit=hybrid_values.get("net_profit"),
                                operating_profit=hybrid_values.get("operating_profit"),
                                operating_cash_flow=hybrid_values.get("operating_cash_flow"),
                                total_debt=hybrid_values.get("total_debt"),
                                total_assets=hybrid_values.get("total_assets"),
                                current_assets=hybrid_values.get("current_assets"),
                                current_liabilities=hybrid_values.get("current_liabilities"),
                                gross_margin=hybrid_values.get("gross_margin"),
                                asset_turnover=hybrid_values.get("asset_turnover"),
                                roa=hybrid_values.get("roa"),
                                shares_outstanding=hybrid_values.get("shares_outstanding"),
                                raw_payload={
                                    "hybrid_screener_enrichment": {
                                        "applied": True,
                                        "source_url": flat.get("source_url"),
                                        "fetched_at": flat.get("fetched_at"),
                                        "previous": is_previous,
                                    }
                                },
                            )
                        )
                        stored_periods += 1

                    shareholding_reference_date = (
                        screener_row.fetched_at.date()
                        if screener_row is not None and screener_row.fetched_at is not None
                        else self._today_local()
                    )
                    latest_holding_date, previous_holding_date = self._quarter_end_pair(shareholding_reference_date)
                    if latest_holding_date not in official_shareholding_dates and self._upsert_screener_shareholding_snapshot(
                        session,
                        symbol=config.symbol,
                        as_of_date=latest_holding_date,
                        values={
                            "promoter_holding": flat.get("promoter_holding"),
                            "fii_holding": flat.get("fii_holding"),
                            "dii_holding": flat.get("dii_holding"),
                        },
                        fetched_at=screener_row.fetched_at if screener_row is not None else None,
                        source_url=screener_row.source_url if screener_row is not None else None,
                    ):
                        stored_shareholding += 1
                    if previous_holding_date not in official_shareholding_dates and self._upsert_screener_shareholding_snapshot(
                        session,
                        symbol=config.symbol,
                        as_of_date=previous_holding_date,
                        values={
                            "promoter_holding": flat.get("promoter_holding_previous"),
                            "fii_holding": flat.get("fii_holding_previous"),
                            "dii_holding": flat.get("dii_holding_previous"),
                        },
                        fetched_at=screener_row.fetched_at if screener_row is not None else None,
                        source_url=screener_row.source_url if screener_row is not None else None,
                    ):
                        stored_shareholding += 1

            next_offset = offset + requested
            if next_offset >= len(symbol_configs):
                next_offset = 0
            upsert_config_value(
                session,
                settings.official_shadow_weekly_state_key,
                {
                    "lastRunAt": datetime.now(tz=settings.tzinfo).isoformat(),
                    "lastOffset": offset,
                    "nextOffset": next_offset,
                    "lastRequested": requested,
                    "lastProcessed": processed,
                    "lastStoredPeriods": stored_periods,
                    "lastStoredShareholding": stored_shareholding,
                    "lastRecoveredByBse": recovered_by_bse,
                    "missingBseMappings": len(missing_bse_mappings),
                    "lastScreenerRefreshed": int(screener_result.get("refreshed") or 0),
                    "lastBoardMeetingsStored": int(earnings_result.get("stored") or 0),
                    "failedExamples": dict(list(failures.items())[:10]),
                },
            )
        return {
            "requested": requested,
            "processed": processed,
            "stored_periods": stored_periods,
            "stored_shareholding": stored_shareholding,
            "next_offset": (offset + requested) % max(len(symbol_configs), 1),
            "recovered_by_bse": recovered_by_bse,
            "missing_bse_mappings": missing_bse_mappings,
            "screener_cache": screener_result,
            "earnings_calendar": earnings_result,
            "failed_examples": dict(list(failures.items())[:10]),
        }

    def rebuild_official_investment_snapshots(self, *, as_of_date: date | None = None) -> dict[str, Any]:
        with session_scope() as session:
            if as_of_date is None:
                latest_quote = session.scalar(select(OfficialQuoteSnapshot).order_by(OfficialQuoteSnapshot.as_of_date.desc()))
                if latest_quote is None or latest_quote.as_of_date is None:
                    return {"stored": 0, "as_of_date": None}
                as_of_date = latest_quote.as_of_date
            quote_rows = session.scalars(select(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.as_of_date == as_of_date)).all()
            if not quote_rows:
                return {"stored": 0, "as_of_date": as_of_date.isoformat()}
            financial_rows = session.scalars(select(OfficialFinancialPeriod).where(OfficialFinancialPeriod.period_end <= as_of_date)).all()
            shareholding_rows = session.scalars(select(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.as_of_date <= as_of_date)).all()
            market_context = session.scalar(select(OfficialMarketContextSnapshot).where(OfficialMarketContextSnapshot.as_of_date == as_of_date))
            screener_rows = session.scalars(
                select(ScreenerCache).where(ScreenerCache.symbol.in_([row.symbol for row in quote_rows]))
            ).all()
            board_meeting_rows = session.scalars(
                select(OfficialCorporateAction).where(
                    OfficialCorporateAction.symbol.in_([row.symbol for row in quote_rows]),
                    OfficialCorporateAction.action_type.in_(self._board_meeting_action_types()),
                )
            ).all()

            financial_by_symbol: dict[str, list[OfficialFinancialPeriod]] = {}
            for row in financial_rows:
                financial_by_symbol.setdefault(row.symbol, []).append(row)
            for symbol_rows in financial_by_symbol.values():
                symbol_rows.sort(key=lambda item: ((item.period_end or date.min), item.period_type), reverse=True)

            shareholding_by_symbol: dict[str, list[OfficialShareholdingSnapshot]] = {}
            for row in shareholding_rows:
                shareholding_by_symbol.setdefault(row.symbol, []).append(row)
            for symbol_rows in shareholding_by_symbol.values():
                symbol_rows.sort(key=lambda item: item.as_of_date or date.min, reverse=True)

            screener_by_symbol = {row.symbol.upper(): row for row in screener_rows if row.symbol}
            board_actions_by_symbol: dict[str, list[OfficialCorporateAction]] = {}
            for row in board_meeting_rows:
                board_actions_by_symbol.setdefault(row.symbol.upper(), []).append(row)
            for symbol_rows in board_actions_by_symbol.values():
                symbol_rows.sort(key=lambda item: item.ex_date or date.max)

            stored = 0
            reference_dt = datetime.now(tz=settings.tzinfo)
            for quote in quote_rows:
                periods = financial_by_symbol.get(quote.symbol, [])
                quarterlies = [item for item in periods if item.period_type == "QUARTERLY"]
                annuals = [item for item in periods if item.period_type == "ANNUAL"]
                latest_quarter = quarterlies[0] if quarterlies else None
                previous_year_quarter = quarterlies[4] if len(quarterlies) >= 5 else None
                latest_annual = annuals[0] if annuals else latest_quarter
                shareholding_history = shareholding_by_symbol.get(quote.symbol, [])
                latest_shareholding = shareholding_history[0] if shareholding_history else None
                previous_shareholding = shareholding_history[1] if len(shareholding_history) > 1 else None

                eps_ttm = self._calc_eps_ttm(quarterlies)
                revenue_growth_pct = self._calc_yoy_growth(latest_quarter.revenue if latest_quarter else None, previous_year_quarter.revenue if previous_year_quarter else None)
                profit_growth_pct = self._calc_yoy_growth(latest_quarter.net_profit if latest_quarter else None, previous_year_quarter.net_profit if previous_year_quarter else None)
                operating_margin = (
                    latest_quarter.gross_margin
                    if latest_quarter and latest_quarter.gross_margin is not None
                    else self._calc_margin(latest_quarter.operating_profit if latest_quarter else None, latest_quarter.revenue if latest_quarter else None)
                )
                net_margin = self._calc_margin(latest_quarter.net_profit if latest_quarter else None, latest_quarter.revenue if latest_quarter else None)
                annual_roa = (
                    latest_annual.roa
                    if latest_annual and latest_annual.roa is not None
                    else self._derive_roa(latest_annual.net_profit if latest_annual else None, latest_annual.total_assets if latest_annual else None)
                )
                annual_asset_turnover = (
                    latest_annual.asset_turnover
                    if latest_annual and latest_annual.asset_turnover is not None
                    else self._derive_asset_turnover(latest_annual.revenue if latest_annual else None, latest_annual.total_assets if latest_annual else None)
                )
                roe = self._calc_margin(latest_annual.net_profit if latest_annual else None, latest_annual.shareholder_equity if latest_annual else None)
                roce = self._calc_margin(latest_annual.ebit if latest_annual else None, latest_annual.capital_employed if latest_annual else None)
                debt_to_equity = self._calc_ratio(latest_annual.total_debt if latest_annual else None, latest_annual.shareholder_equity if latest_annual else None)
                current_ratio = self._calc_ratio(latest_annual.current_assets if latest_annual else None, latest_annual.current_liabilities if latest_annual else None)
                promoter_holding = latest_shareholding.promoter_holding if latest_shareholding else None
                promoter_pledge = latest_shareholding.promoter_pledge if latest_shareholding else None
                fii_holding = latest_shareholding.fii_holding if latest_shareholding else None
                dii_holding = latest_shareholding.dii_holding if latest_shareholding else None
                screener_row = screener_by_symbol.get(quote.symbol.upper())
                screener_flat = self._flatten_screener_cache(screener_row)
                screener_stale = self._screener_cache_stale(screener_row, reference=reference_dt) if screener_row is not None else False
                board_earnings_date = self._earnings_date_from_actions(
                    board_actions_by_symbol.get(quote.symbol.upper(), []),
                    as_of_date=as_of_date,
                )
                official_earnings_date = (
                    latest_quarter.earnings_date
                    if latest_quarter and latest_quarter.earnings_date
                    else (latest_annual.earnings_date if latest_annual else None)
                )
                official_pb_ratio = None
                if isinstance(quote.metadata_json, dict):
                    official_pb_ratio = quote.metadata_json.get("pb_ratio")

                screener_revenue_growth = self._calc_yoy_growth(
                    screener_flat.get("latest_annual_revenue"),
                    screener_flat.get("previous_annual_revenue"),
                )
                screener_profit_growth = self._calc_yoy_growth(
                    screener_flat.get("latest_annual_net_profit"),
                    screener_flat.get("previous_annual_net_profit"),
                )
                screener_operating_margin = (
                    screener_flat.get("operating_margin_ttm")
                    if screener_flat.get("operating_margin_ttm") is not None
                    else screener_flat.get("latest_annual_operating_margin")
                )
                screener_net_margin = None
                if screener_flat.get("net_profit_ttm") is not None and screener_flat.get("revenue_ttm") not in (None, 0):
                    screener_net_margin = (screener_flat["net_profit_ttm"] / screener_flat["revenue_ttm"]) * 100.0
                screener_fii_holding_change = self._holding_change(
                    screener_flat.get("fii_holding"),
                    screener_flat.get("fii_holding_previous"),
                )
                screener_dii_holding_change = self._holding_change(
                    screener_flat.get("dii_holding"),
                    screener_flat.get("dii_holding_previous"),
                )

                reconciled = self.reconciler.reconcile_fields(
                    {
                        "earnings_date": [
                            {"source": "BSE_BOARD_MEETING", "value": board_earnings_date},
                            {
                                "source": "MONEYCONTROL",
                                "value": board_earnings_date
                                if any(
                                    action.source_status == "MONEYCONTROL_EARNINGS"
                                    for action in board_actions_by_symbol.get(quote.symbol.upper(), [])
                                    if action.ex_date == board_earnings_date
                                )
                                else None,
                            },
                            {"source": "OFFICIAL_PERIOD", "value": official_earnings_date},
                        ],
                        "market_cap": [
                            {"source": "OFFICIAL_QUOTE", "value": quote.market_cap},
                            {"source": "SCREENER", "value": screener_flat.get("market_cap"), "stale": screener_stale},
                        ],
                        "pe_ratio": [
                            {"source": "OFFICIAL_QUOTE", "value": quote.pe_ratio},
                            {"source": "SCREENER", "value": screener_flat.get("pe_ratio"), "stale": screener_stale},
                        ],
                        "pb_ratio": [
                            {"source": "OFFICIAL_QUOTE", "value": official_pb_ratio},
                            {"source": "SCREENER", "value": screener_flat.get("pb_ratio"), "stale": screener_stale},
                        ],
                        "dividend_yield": [
                            {"source": "OFFICIAL_QUOTE", "value": quote.dividend_yield},
                            {"source": "SCREENER", "value": screener_flat.get("dividend_yield"), "stale": screener_stale},
                        ],
                        "industry_pe": [{"source": "OFFICIAL_QUOTE", "value": quote.industry_pe}],
                        "week_52_high": [{"source": "OFFICIAL_QUOTE", "value": quote.week_52_high}],
                        "week_52_low": [{"source": "OFFICIAL_QUOTE", "value": quote.week_52_low}],
                        "eps_ttm": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=eps_ttm,
                                screener_value=screener_flat.get("eps_ttm"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "eps_growth_3y_cagr": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=self._calc_eps_growth_cagr(quarterlies),
                                screener_value=screener_flat.get("eps_growth_3y_cagr"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "revenue_growth_pct": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=revenue_growth_pct,
                                screener_value=screener_revenue_growth,
                                screener_stale=screener_stale,
                            ),
                        ],
                        "profit_growth_pct": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=profit_growth_pct,
                                screener_value=screener_profit_growth,
                                screener_stale=screener_stale,
                            ),
                        ],
                        "operating_margin": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=operating_margin,
                                screener_value=screener_operating_margin,
                                screener_stale=screener_stale,
                            ),
                        ],
                        "net_margin": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=net_margin,
                                screener_value=screener_net_margin,
                                screener_stale=screener_stale,
                            ),
                        ],
                        "roe": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=roe,
                                screener_value=screener_flat.get("roe"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "roce": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=roce,
                                screener_value=screener_flat.get("roce"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "debt_to_equity": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=debt_to_equity,
                                screener_value=screener_flat.get("debt_equity"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "current_ratio": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_PERIOD",
                                official_value=current_ratio,
                                screener_value=screener_flat.get("current_ratio"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "promoter_holding": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_SHAREHOLDING",
                                official_value=promoter_holding,
                                screener_value=screener_flat.get("promoter_holding"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "promoter_pledge": [{"source": "OFFICIAL_SHAREHOLDING", "value": promoter_pledge}],
                        "promoter_holding_change_pct": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_SHAREHOLDING",
                                official_value=self._holding_change(
                                    promoter_holding,
                                    previous_shareholding.promoter_holding if previous_shareholding else None,
                                ),
                                screener_value=screener_flat.get("promoter_holding_change_pct"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "fii_holding": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_SHAREHOLDING",
                                official_value=fii_holding,
                                screener_value=screener_flat.get("fii_holding"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "dii_holding": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_SHAREHOLDING",
                                official_value=dii_holding,
                                screener_value=screener_flat.get("dii_holding"),
                                screener_stale=screener_stale,
                            ),
                        ],
                        "fii_holding_change_pct": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_SHAREHOLDING",
                                official_value=self._holding_change(
                                    fii_holding,
                                    previous_shareholding.fii_holding if previous_shareholding else None,
                                ),
                                screener_value=screener_fii_holding_change,
                                screener_stale=screener_stale,
                            ),
                        ],
                        "dii_holding_change_pct": [
                            *self._hybrid_candidates(
                                official_source="OFFICIAL_SHAREHOLDING",
                                official_value=self._holding_change(
                                    dii_holding,
                                    previous_shareholding.dii_holding if previous_shareholding else None,
                                ),
                                screener_value=screener_dii_holding_change,
                                screener_stale=screener_stale,
                            ),
                        ],
                        "npa_pct": [{"source": "OFFICIAL_PERIOD", "value": latest_annual.npa_pct if latest_annual else None}],
                        "capital_adequacy_pct": [{"source": "OFFICIAL_PERIOD", "value": latest_annual.capital_adequacy_pct if latest_annual else None}],
                    }
                )

                self._upsert_record(
                    session,
                    OfficialInvestmentSnapshot,
                    {"symbol": quote.symbol, "as_of_date": as_of_date},
                    {
                        "company_name": quote.company_name,
                        "sector": quote.sector,
                        "earnings_date": reconciled.values.get("earnings_date"),
                        "market_cap": reconciled.values.get("market_cap"),
                        "pe_ratio": reconciled.values.get("pe_ratio"),
                        "pb_ratio": reconciled.values.get("pb_ratio"),
                        "dividend_yield": reconciled.values.get("dividend_yield"),
                        "industry_pe": reconciled.values.get("industry_pe"),
                        "week_52_high": reconciled.values.get("week_52_high"),
                        "week_52_low": reconciled.values.get("week_52_low"),
                        "eps_ttm": reconciled.values.get("eps_ttm"),
                        "eps_growth_3y_cagr": reconciled.values.get("eps_growth_3y_cagr"),
                        "revenue_growth_pct": reconciled.values.get("revenue_growth_pct"),
                        "profit_growth_pct": reconciled.values.get("profit_growth_pct"),
                        "operating_margin": reconciled.values.get("operating_margin"),
                        "net_margin": reconciled.values.get("net_margin"),
                        "roe": reconciled.values.get("roe"),
                        "roce": reconciled.values.get("roce"),
                        "debt_to_equity": reconciled.values.get("debt_to_equity"),
                        "current_ratio": reconciled.values.get("current_ratio"),
                        "promoter_holding": reconciled.values.get("promoter_holding"),
                        "promoter_pledge": reconciled.values.get("promoter_pledge"),
                        "promoter_holding_change_pct": reconciled.values.get("promoter_holding_change_pct"),
                        "fii_holding": reconciled.values.get("fii_holding"),
                        "dii_holding": reconciled.values.get("dii_holding"),
                        "fii_holding_change_pct": reconciled.values.get("fii_holding_change_pct"),
                        "dii_holding_change_pct": reconciled.values.get("dii_holding_change_pct"),
                        "npa_pct": reconciled.values.get("npa_pct"),
                        "capital_adequacy_pct": reconciled.values.get("capital_adequacy_pct"),
                        "source_coverage": {
                            "quote": quote.source_status,
                            "has_quarterly_periods": bool(quarterlies),
                            "has_annual_periods": bool(annuals),
                            "has_shareholding": latest_shareholding is not None,
                            "has_market_context": market_context is not None,
                            "has_screener_cache": screener_row is not None,
                            "screener_cache_stale": screener_stale,
                            "has_board_meeting_earnings": board_earnings_date is not None,
                        },
                        "data_sources": reconciled.to_data_sources_payload(),
                        "raw_metrics": {
                            "market_context": market_context.sector_context if market_context else {},
                            "nifty50_close": market_context.nifty50_close if market_context else None,
                            "nifty50_sma200": market_context.nifty50_sma200 if market_context else None,
                            "india_vix": market_context.india_vix if market_context else None,
                            "aaa_bond_yield": market_context.aaa_bond_yield if market_context else settings.official_aaa_bond_yield,
                            "annual_roa": annual_roa,
                            "annual_asset_turnover": annual_asset_turnover,
                            "annual_total_assets": latest_annual.total_assets if latest_annual else None,
                            "screener_cache_age_days": self._cache_age_days(screener_row.fetched_at if screener_row else None, reference=reference_dt),
                            "screener_source_url": screener_row.source_url if screener_row else None,
                            "screener_raw_fields": screener_flat,
                            "reconciler_fill_rate": reconciled.fill_rate,
                            "reconciler_mismatches": reconciled.mismatches,
                        },
                    },
                )
                stored += 1
        return {"stored": stored, "as_of_date": as_of_date.isoformat()}

    def compare_shadow_snapshots(self, *, as_of_date: date | None = None, missing_bse_mapping_symbols: list[str] | None = None, recovered_by_bse_count: int = 0) -> dict[str, Any]:
        missing_bse_mapping_symbols = missing_bse_mapping_symbols or []
        with session_scope() as session:
            if as_of_date is None:
                latest = session.scalar(select(OfficialInvestmentSnapshot).order_by(OfficialInvestmentSnapshot.as_of_date.desc()))
                if latest is None or latest.as_of_date is None:
                    return {"officialCoverage": 0, "legacyCoverage": 0}
                as_of_date = latest.as_of_date
            official_rows = session.scalars(select(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.as_of_date == as_of_date)).all()
            legacy_rows = session.scalars(select(StockFundamentalSnapshot)).all()
            latest_legacy_by_symbol: dict[str, StockFundamentalSnapshot] = {}
            for row in legacy_rows:
                if not row.symbol:
                    continue
                current = latest_legacy_by_symbol.get(row.symbol)
                if current is None or (row.as_of_date or date.min) > (current.as_of_date or date.min):
                    latest_legacy_by_symbol[row.symbol] = row
            summary = self._build_shadow_summary(
                official_rows=official_rows,
                legacy_rows=list(latest_legacy_by_symbol.values()),
                missing_bse_mapping_symbols=missing_bse_mapping_symbols,
                recovered_by_bse_count=recovered_by_bse_count,
            )
            upsert_config_value(session, settings.official_shadow_summary_key, summary)
        return summary
