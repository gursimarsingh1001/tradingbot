from __future__ import annotations

import json
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
from backend.data.nse_client import NSEClient, get_nse_client
from backend.db.models_investment import (
    OfficialCorporateAction,
    OfficialFinancialPeriod,
    OfficialInvestmentSnapshot,
    OfficialMarketContextSnapshot,
    OfficialQuoteSnapshot,
    OfficialShareholdingSnapshot,
)
from backend.db.postgres import (
    StockFundamentalSnapshot,
    get_config_value,
    session_scope,
    upsert_config_value,
)
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
        historical_fetcher: HistoricalFetcher | None = None,
        angel_client: AngelOneClient | None = None,
    ) -> None:
        self.nse_client = nse_client or get_nse_client()
        self.bse_client = bse_client or get_bse_client()
        self.historical_fetcher = historical_fetcher or HistoricalFetcher()
        self.angel_client = angel_client or get_angel_one_client()

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

    @staticmethod
    def _official_missing_field_counts(rows: list[OfficialInvestmentSnapshot]) -> dict[str, int]:
        tracked_fields = (
            "market_cap",
            "pe_ratio",
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

    def refresh_quote_snapshots(self, *, symbol_configs: list[SymbolConfig] | None = None, as_of_date: date | None = None) -> dict[str, Any]:
        as_of_date = as_of_date or date.today()
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
                        for key in ("market_cap", "pe_ratio", "dividend_yield", "week_52_high", "week_52_low"):
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
                        "dividend_yield": metrics.get("dividend_yield"),
                        "industry_pe": metrics.get("industry_pe"),
                        "week_52_high": metrics.get("week_52_high"),
                        "week_52_low": metrics.get("week_52_low"),
                        "raw_payload": {"nse": nse_payload, "bse": bse_payload},
                        "metadata_json": {
                            "isin": config.isin,
                            "bse_scripcode": config.bse_scripcode,
                            "canonical_exchange": config.canonical_exchange,
                        },
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
        as_of_date = as_of_date or date.today()
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
        as_of_date = as_of_date or date.today()
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

        vix_payload = self.nse_client.fetch_all_indices()
        india_vix = self.nse_client.extract_india_vix(vix_payload)
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
                    "raw_payload": {"nse_all_indices": vix_payload},
                },
            )
        return {
            "as_of_date": as_of_date.isoformat(),
            "nifty50_close": nifty_close,
            "nifty50_sma200": nifty_sma200,
            "india_vix": india_vix,
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

                for item in combined_periods:
                    self._upsert_record(
                        session,
                        OfficialFinancialPeriod,
                        {
                            "symbol": config.symbol,
                            "period_type": str(item["period_type"]),
                            "period_end": item["period_end"],
                        },
                        {
                            "fiscal_label": item.get("fiscal_label"),
                            "earnings_date": item.get("earnings_date"),
                            "source_status": "NSE+BSE" if bse_financial_payload is not None else "NSE_ONLY",
                            "revenue": item.get("revenue"),
                            "net_profit": item.get("net_profit"),
                            "operating_profit": item.get("operating_profit"),
                            "ebit": item.get("ebit"),
                            "eps_basic": item.get("eps_basic"),
                            "operating_cash_flow": item.get("operating_cash_flow"),
                            "total_debt": item.get("total_debt"),
                            "shareholder_equity": item.get("shareholder_equity"),
                            "capital_employed": item.get("capital_employed"),
                            "current_assets": item.get("current_assets"),
                            "current_liabilities": item.get("current_liabilities"),
                            "gross_margin": item.get("gross_margin"),
                            "asset_turnover": item.get("asset_turnover"),
                            "roa": item.get("roa"),
                            "shares_outstanding": item.get("shares_outstanding"),
                            "npa_pct": item.get("npa_pct"),
                            "capital_adequacy_pct": item.get("capital_adequacy_pct"),
                            "raw_payload": item.get("raw_payload") or {},
                        },
                    )
                    stored_periods += 1
                if shareholding.get("as_of_date") is not None:
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

            stored = 0
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
                roe = self._calc_margin(latest_annual.net_profit if latest_annual else None, latest_annual.shareholder_equity if latest_annual else None)
                roce = self._calc_margin(latest_annual.ebit if latest_annual else None, latest_annual.capital_employed if latest_annual else None)
                debt_to_equity = self._calc_ratio(latest_annual.total_debt if latest_annual else None, latest_annual.shareholder_equity if latest_annual else None)
                current_ratio = self._calc_ratio(latest_annual.current_assets if latest_annual else None, latest_annual.current_liabilities if latest_annual else None)
                promoter_holding = latest_shareholding.promoter_holding if latest_shareholding else None
                promoter_pledge = latest_shareholding.promoter_pledge if latest_shareholding else None
                fii_holding = latest_shareholding.fii_holding if latest_shareholding else None
                dii_holding = latest_shareholding.dii_holding if latest_shareholding else None

                self._upsert_record(
                    session,
                    OfficialInvestmentSnapshot,
                    {"symbol": quote.symbol, "as_of_date": as_of_date},
                    {
                        "company_name": quote.company_name,
                        "sector": quote.sector,
                        "earnings_date": (latest_quarter.earnings_date if latest_quarter and latest_quarter.earnings_date else (latest_annual.earnings_date if latest_annual else None)),
                        "market_cap": quote.market_cap,
                        "pe_ratio": quote.pe_ratio,
                        "dividend_yield": quote.dividend_yield,
                        "industry_pe": quote.industry_pe,
                        "week_52_high": quote.week_52_high,
                        "week_52_low": quote.week_52_low,
                        "eps_ttm": eps_ttm,
                        "eps_growth_3y_cagr": self._calc_eps_growth_cagr(quarterlies),
                        "revenue_growth_pct": revenue_growth_pct,
                        "profit_growth_pct": profit_growth_pct,
                        "operating_margin": operating_margin,
                        "net_margin": net_margin,
                        "roe": roe,
                        "roce": roce,
                        "debt_to_equity": debt_to_equity,
                        "current_ratio": current_ratio,
                        "promoter_holding": promoter_holding,
                        "promoter_pledge": promoter_pledge,
                        "promoter_holding_change_pct": self._holding_change(promoter_holding, previous_shareholding.promoter_holding if previous_shareholding else None),
                        "fii_holding": fii_holding,
                        "dii_holding": dii_holding,
                        "fii_holding_change_pct": self._holding_change(fii_holding, previous_shareholding.fii_holding if previous_shareholding else None),
                        "dii_holding_change_pct": self._holding_change(dii_holding, previous_shareholding.dii_holding if previous_shareholding else None),
                        "npa_pct": latest_annual.npa_pct if latest_annual else None,
                        "capital_adequacy_pct": latest_annual.capital_adequacy_pct if latest_annual else None,
                        "source_coverage": {
                            "quote": quote.source_status,
                            "has_quarterly_periods": bool(quarterlies),
                            "has_annual_periods": bool(annuals),
                            "has_shareholding": latest_shareholding is not None,
                            "has_market_context": market_context is not None,
                        },
                        "raw_metrics": {
                            "market_context": market_context.sector_context if market_context else {},
                            "nifty50_close": market_context.nifty50_close if market_context else None,
                            "nifty50_sma200": market_context.nifty50_sma200 if market_context else None,
                            "india_vix": market_context.india_vix if market_context else None,
                            "aaa_bond_yield": market_context.aaa_bond_yield if market_context else settings.official_aaa_bond_yield,
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
