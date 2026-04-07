from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import pandas as pd

from backend.config import get_settings
from backend.data.angel_one_client import AngelOneClient, get_angel_one_client
from backend.data.data_quality import validate_ohlcv_frame
from backend.data.indicator_calculator import IndicatorCalculator
from backend.data.universe_filters import is_pure_nse_stock
from backend.db.influx import InfluxMarketDataStore, get_influx_store
from backend.logging_utils import get_logger


settings = get_settings()
logger = get_logger(__name__)

PREFERRED_BATCH_SYMBOLS: list[str] = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "SBIN",
    "BHARTIARTL",
    "ITC",
    "LT",
    "KOTAKBANK",
    "AXISBANK",
    "MARUTI",
    "HINDUNILVR",
    "SUNPHARMA",
    "ASIANPAINT",
    "BAJFINANCE",
    "HCLTECH",
    "NTPC",
    "ULTRACEMCO",
    "WIPRO",
    "TECHM",
    "TITAN",
    "BAJAJFINSV",
    "POWERGRID",
    "TATAMOTORS",
    "TATASTEEL",
    "M&M",
    "BEL",
    "ADANIPORTS",
    "NESTLEIND",
    "GRASIM",
    "HINDALCO",
    "COALINDIA",
    "JSWSTEEL",
    "CIPLA",
    "DRREDDY",
    "EICHERMOT",
    "HEROMOTOCO",
    "APOLLOHOSP",
    "TRENT",
    "INDUSINDBK",
    "BPCL",
    "BRITANNIA",
    "SBILIFE",
    "DIVISLAB",
    "ONGC",
    "HDFCLIFE",
    "ADANIENT",
    "BAJAJ-AUTO",
    "SHRIRAMFIN",
]


@dataclass
class SymbolConfig:
    symbol: str
    token: str
    company_name: str
    exchange: str = "NSE"
    sector: str | None = None
    trading_symbol: str | None = None
    series: str | None = None
    lot_size: int | None = None
    tick_size: float | None = None
    isin: str | None = None
    canonical_exchange: str | None = None
    bse_scripcode: str | None = None


class HistoricalFetcher:
    def __init__(
        self,
        angel_client: AngelOneClient | None = None,
        influx_store: InfluxMarketDataStore | None = None,
    ) -> None:
        self.angel_client = angel_client or get_angel_one_client()
        self.influx_store = influx_store or get_influx_store()

    def load_symbols(self) -> list[SymbolConfig]:
        path = Path(settings.symbols_config_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        bse_mapping = self._load_bse_symbol_mapping()
        return [
            SymbolConfig(
                symbol=item["symbol"],
                token=item["token"],
                company_name=item.get("companyName", item["symbol"]),
                exchange=item.get("exchange", "NSE"),
                sector=item.get("sector"),
                trading_symbol=item.get("tradingSymbol"),
                series=item.get("series"),
                lot_size=item.get("lotSize"),
                tick_size=item.get("tickSize"),
                isin=(bse_mapping.get(str(item["symbol"]).upper(), {}).get("isin") or item.get("isin")),
                canonical_exchange=(
                    bse_mapping.get(str(item["symbol"]).upper(), {}).get("canonicalExchange")
                    or item.get("canonicalExchange")
                    or item.get("exchange", "NSE")
                ),
                bse_scripcode=(
                    bse_mapping.get(str(item["symbol"]).upper(), {}).get("bseScripcode")
                    or bse_mapping.get(str(item["symbol"]).upper(), {}).get("bse_scripcode")
                    or item.get("bseScripcode")
                    or item.get("bse_scripcode")
                ),
            )
            for item in payload
        ]

    @staticmethod
    def _normalize_optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _load_bse_symbol_mapping(self) -> dict[str, dict[str, str]]:
        path = Path(settings.bse_symbol_mapping_path)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, list):
            return {}
        mapping: dict[str, dict[str, str]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            symbol = self._normalize_optional_text(item.get("symbol"))
            if not symbol:
                continue
            mapping[symbol.upper()] = {
                "isin": self._normalize_optional_text(item.get("isin")) or "",
                "canonicalExchange": self._normalize_optional_text(item.get("canonicalExchange"))
                or self._normalize_optional_text(item.get("canonical_exchange"))
                or "",
                "bseScripcode": self._normalize_optional_text(item.get("bseScripcode"))
                or self._normalize_optional_text(item.get("bse_scripcode"))
                or "",
            }
        return mapping

    def load_symbol_map(self) -> dict[str, SymbolConfig]:
        return {item.symbol.upper(): item for item in self.load_symbols()}

    def is_backtest_candidate(self, config: SymbolConfig) -> bool:
        return is_pure_nse_stock(
            symbol=config.symbol,
            company_name=config.company_name,
            trading_symbol=config.trading_symbol,
            exchange=config.exchange,
            series=config.series,
        )

    def select_symbols(self, *, limit: int | None = 50, preferred: list[str] | None = None) -> list[SymbolConfig]:
        symbol_map = self.load_symbol_map()
        eligible_symbols = [config for config in self.load_symbols() if self.is_backtest_candidate(config)]
        preferred = preferred or PREFERRED_BATCH_SYMBOLS
        selected: list[SymbolConfig] = []
        seen: set[str] = set()
        target_count = limit if limit is not None and limit > 0 else None

        for symbol in preferred:
            config = symbol_map.get(symbol.upper())
            if config is None or config.symbol in seen:
                continue
            selected.append(config)
            seen.add(config.symbol)
            if target_count is not None and len(selected) >= target_count:
                return selected

        for config in eligible_symbols:
            if config.symbol in seen:
                continue
            selected.append(config)
            seen.add(config.symbol)
            if target_count is not None and len(selected) >= target_count:
                break
        return selected

    def fetch_symbol_frame(self, symbol_config: SymbolConfig) -> Any:
        stop = datetime.now(tz=settings.tzinfo)
        start = stop - timedelta(days=3660)
        try:
            cached = self.influx_store.query_symbol_history(symbol_config.symbol, start=start, stop=stop)
        except Exception as exc:
            logger.warning("Historical cache query failed for %s: InfluxDB: %s", symbol_config.symbol, exc)
            cached = pd.DataFrame()
        if not cached.empty:
            validated_cached = validate_ohlcv_frame(cached)
            if validated_cached.empty:
                logger.warning("Rejected invalid cached OHLCV data for %s", symbol_config.symbol)
                cached = pd.DataFrame()
            else:
                cached = validated_cached
        if not cached.empty:
            last_cached = cached.index.max()
            if getattr(last_cached, "tzinfo", None) is None:
                last_cached = last_cached.tz_localize(settings.tzinfo)
            if (stop - last_cached).days <= 7:
                return IndicatorCalculator.enrich(cached)
        try:
            frame = self.angel_client.get_historical_candles(
                symbol_config.token,
                exchange=symbol_config.exchange,
                interval="ONE_DAY",
                from_date=start,
                to_date=stop,
            )
        except Exception as exc:
            if not cached.empty:
                return IndicatorCalculator.enrich(cached)
            logger.warning("Historical fetch failed for %s: Angel One: %s", symbol_config.symbol, exc)
            return cached

        if not frame.empty:
            frame = validate_ohlcv_frame(frame)
        if not frame.empty:
            enriched = IndicatorCalculator.enrich(frame)
            try:
                self.influx_store.write_price_history(symbol_config.symbol, enriched)
            except Exception as exc:
                logger.warning("Historical cache write failed for %s: InfluxDB: %s", symbol_config.symbol, exc)
            return enriched

        if not cached.empty:
            return IndicatorCalculator.enrich(cached)
        return cached

    def fetch_symbol(self, symbol_config: SymbolConfig) -> dict[str, Any]:
        frame = self.fetch_symbol_frame(symbol_config)
        if frame.empty:
            return {"symbol": symbol_config.symbol, "rows": 0}
        return {
            "symbol": symbol_config.symbol,
            "rows": len(frame),
            "from": frame.index.min().isoformat(),
            "to": frame.index.max().isoformat(),
        }

    def run_pilot(self, limit: int = 5) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for symbol_config in self.select_symbols(limit=limit):
            results.append(self.fetch_symbol(symbol_config))
        return results

    def run_full(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for symbol_config in self.select_symbols(limit=None):
            results.append(self.fetch_symbol(symbol_config))
        return results

    def run_subset(self, symbols: list[str]) -> list[dict[str, Any]]:
        symbol_map = self.load_symbol_map()
        results: list[dict[str, Any]] = []
        for symbol in symbols:
            config = symbol_map.get(symbol.upper())
            if config is None:
                results.append({"symbol": symbol, "rows": 0, "error": "Symbol not found in config"})
                continue
            results.append(self.fetch_symbol(config))
        return results


def main() -> None:
    fetcher = HistoricalFetcher()
    results = fetcher.run_pilot(limit=5)
    logger.info("Historical fetch pilot completed: %s", {"fetchedAt": datetime.now(tz=settings.tzinfo).isoformat(), "results": results})


if __name__ == "__main__":
    main()
