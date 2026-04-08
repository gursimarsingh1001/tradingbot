from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.data.angel_one_client import AngelOneClient, get_angel_one_client
from backend.data.bse_mapping_builder import build_bse_symbol_mappings, load_nifty500_isin_map, load_openapi_bse_rows
from backend.data.indicator_calculator import IndicatorCalculator
from backend.data.universe_filters import is_pure_nse_stock
from backend.db.influx import InfluxMarketDataStore, get_influx_store


settings = get_settings()

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

    @staticmethod
    def _normalize_optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _load_bse_symbol_mapping(self) -> dict[str, dict[str, str]]:
        path = Path(settings.bse_symbol_mapping_path)
        payload: list[dict[str, Any]] = []
        if path.exists():
            try:
                raw_payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw_payload = []
            if isinstance(raw_payload, list):
                payload = [item for item in raw_payload if isinstance(item, dict)]

        if not payload:
            repo_root = Path(settings.symbols_config_path).resolve().parents[2]
            master_path = repo_root / "tmp" / "OpenAPIScripMaster.json"
            nifty_csv_path = repo_root / "tmp" / "ind_nifty500list.csv"
            try:
                symbols_payload = json.loads(Path(settings.symbols_config_path).read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                symbols_payload = []
            if isinstance(symbols_payload, list):
                payload, _ = build_bse_symbol_mappings(
                    symbols_payload,
                    load_openapi_bse_rows(master_path),
                    load_nifty500_isin_map(nifty_csv_path),
                )

        mapping: dict[str, dict[str, str]] = {}
        for item in payload:
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
        cached = self.influx_store.query_symbol_history(symbol_config.symbol, start=start, stop=stop)
        if not cached.empty:
            last_cached = cached.index.max()
            if getattr(last_cached, "tzinfo", None) is None:
                last_cached = last_cached.tz_localize(settings.tzinfo)
            if (stop - last_cached).days <= 7:
                return IndicatorCalculator.enrich(cached)
        try:
            from backend.data.dhan_client import get_dhan_client
            dhan_client = get_dhan_client()
            
            # Deterministic split based on symbol length + first char
            hash_val = sum(ord(c) for c in symbol_config.symbol)
            use_dhan_first = (hash_val % 2 == 0)
            
            clients = [
                ("dhan", lambda: dhan_client.get_historical_candles(
                    symbol_config.token,
                    symbol=symbol_config.symbol,
                    exchange=symbol_config.exchange,
                    interval="ONE_DAY",
                    from_date=start,
                    to_date=stop,
                )),
                ("angel", lambda: self.angel_client.get_historical_candles(
                    symbol_config.token,
                    exchange=symbol_config.exchange,
                    interval="ONE_DAY",
                    from_date=start,
                    to_date=stop,
                ))
            ]
            
            if not use_dhan_first:
                clients.reverse()
                
            frame = None
            last_err = None
            for client_name, fetch_func in clients:
                try:
                    frame = fetch_func()
                    if not frame.empty:
                        break
                except Exception as e:
                    last_err = e
                    print(f"Historical fetch failed on {client_name} for {symbol_config.symbol}: {e}. Falling back...")
                    
            if frame is None or frame.empty:
                if last_err:
                    raise last_err
                else:
                    raise Exception("Both brokers returned empty data.")
        except Exception as exc:
            if not cached.empty:
                return IndicatorCalculator.enrich(cached)
            print(f"Historical fetch failed for {symbol_config.symbol} (all brokers): {exc}")
            return cached

        if not frame.empty:
            enriched = IndicatorCalculator.enrich(frame)
            self.influx_store.write_price_history(symbol_config.symbol, enriched)
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
    print({"fetchedAt": datetime.now(tz=settings.tzinfo).isoformat(), "results": results})


if __name__ == "__main__":
    main()
