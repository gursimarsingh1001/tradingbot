from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any

from backend.config import get_settings
from backend.data.angel_one_client import get_angel_one_client
from backend.data.data_quality import validate_quote_snapshot
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.redis_client import get_cache
from backend.logging_utils import get_logger


settings = get_settings()
logger = get_logger(__name__)


@dataclass(frozen=True)
class QuoteTarget:
    symbol: str
    token: str
    exchange: str
    trading_symbol: str


INDEX_TARGETS: dict[str, QuoteTarget] = {
    "NIFTY50": QuoteTarget(symbol="NIFTY50", token="99926000", exchange="NSE", trading_symbol="Nifty 50"),
    "BANKNIFTY": QuoteTarget(symbol="BANKNIFTY", token="99926009", exchange="NSE", trading_symbol="Nifty Bank"),
    "SENSEX": QuoteTarget(symbol="SENSEX", token="99919000", exchange="BSE", trading_symbol="SENSEX"),
    "INDIA_VIX": QuoteTarget(symbol="INDIA_VIX", token="99926017", exchange="NSE", trading_symbol="India VIX"),
}


class MarketDataService:
    CACHE_REFRESH_SECONDS = 5
    LIVE_WATCHLIST_LIMIT = 47
    QUOTE_BATCH_SIZE = 20

    def __init__(self) -> None:
        self.cache = get_cache()
        self.angel_client = get_angel_one_client()
        self.historical_fetcher = HistoricalFetcher(angel_client=self.angel_client)
        self._lock = Lock()

    @staticmethod
    def _default_indices() -> dict[str, Any]:
        return {
            "NIFTY50": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "BANKNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "SENSEX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            "INDIA_VIX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
        }

    @staticmethod
    def _normalize_quote(payload: dict[str, Any]) -> dict[str, float]:
        data = payload.get("data") or {}
        ltp = float(data.get("ltp") or 0.0)
        close = float(data.get("close") or 0.0)
        change = ltp - close if close else 0.0
        change_pct = (change / close) if close else 0.0
        return {
            "ltp": ltp,
            "close": close,
            "change": change,
            "change_pct": change_pct,
        }

    def _is_stale(self, *, force: bool = False) -> bool:
        if force:
            return True
        state = self.cache.get_json("live:cache_state", {})
        refreshed_at = state.get("refreshed_at")
        if not refreshed_at:
            return True
        try:
            last_refresh = datetime.fromisoformat(refreshed_at)
        except ValueError:
            return True
        age = (datetime.now(tz=settings.tzinfo) - last_refresh).total_seconds()
        return age >= self.CACHE_REFRESH_SECONDS

    @staticmethod
    def _build_quote_map(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        fetched = ((payload or {}).get("data") or {}).get("fetched") or []
        quote_map: dict[tuple[str, str], dict[str, Any]] = {}
        for item in fetched:
            exchange = str(item.get("exchange") or item.get("exchangeSegment") or "").upper()
            token = str(item.get("symbolToken") or item.get("symboltoken") or item.get("symbol_token") or "")
            if exchange and token:
                quote_map[(exchange, token)] = item
        return quote_map

    def _fetch_quote_map(self, exchange_tokens: dict[str, list[str]]) -> dict[tuple[str, str], dict[str, Any]]:
        if not exchange_tokens:
            return {}
        market_data = self.angel_client.get_market_data("OHLC", exchange_tokens)
        return self._build_quote_map(market_data)

    def _chunked_quote_map(self, symbol_configs: list[Any]) -> dict[tuple[str, str], dict[str, Any]]:
        quote_map: dict[tuple[str, str], dict[str, Any]] = {}
        for start in range(0, len(symbol_configs), self.QUOTE_BATCH_SIZE):
            batch = symbol_configs[start : start + self.QUOTE_BATCH_SIZE]
            exchange_tokens: dict[str, list[str]] = {}
            for symbol_config in batch:
                exchange_tokens.setdefault(symbol_config.exchange, []).append(symbol_config.token)
            try:
                quote_map.update(self._fetch_quote_map(exchange_tokens))
            except Exception:
                logger.warning(
                    "Market quote batch failed for %s symbols; using cached fallback for that batch.",
                    len(batch),
                    exc_info=True,
                )
        return quote_map

    def refresh_market_cache(self, *, force: bool = False, watchlist_limit: int | None = None) -> dict[str, Any]:
        watchlist_limit = watchlist_limit or self.LIVE_WATCHLIST_LIMIT
        cached_indices = self.cache.get_json("live:indices", self._default_indices())
        cached_watchlist = self.cache.get_json("live:watchlist_prices", [])
        if not self._is_stale(force=force):
            return {
                "indices": cached_indices,
                "watchlist_prices": cached_watchlist,
            }

        with self._lock:
            if not self._is_stale(force=force):
                return {
                    "indices": self.cache.get_json("live:indices", cached_indices),
                    "watchlist_prices": self.cache.get_json("live:watchlist_prices", cached_watchlist),
                }

            watchlist = self.historical_fetcher.select_symbols(limit=watchlist_limit)
            index_exchange_tokens: dict[str, list[str]] = {}
            for target in INDEX_TARGETS.values():
                index_exchange_tokens.setdefault(target.exchange, []).append(target.token)

            index_quote_map: dict[tuple[str, str], dict[str, Any]] = {}
            try:
                index_quote_map = self._fetch_quote_map(index_exchange_tokens)
            except Exception:
                logger.warning("Index quote refresh failed; using cached index values.", exc_info=True)

            watchlist_quote_map = self._chunked_quote_map(watchlist)

            indices: dict[str, Any] = {}
            for key, target in INDEX_TARGETS.items():
                quote = {"data": index_quote_map.get((target.exchange, target.token), {})}
                normalized = self._normalize_quote(quote)
                fallback = cached_indices.get(key, {})
                fallback_value = float(fallback.get("value") or 0.0)
                use_live_quote = validate_quote_snapshot(
                    ltp=normalized["ltp"],
                    close=normalized["close"],
                    cached_ltp=fallback_value if fallback_value > 0 else None,
                )
                if normalized["ltp"] > 0 and not use_live_quote:
                    logger.warning("Rejected suspicious index quote for %s; using cached value.", key)
                if not use_live_quote and fallback_value > 0:
                    indices[key] = fallback
                    continue
                indices[key] = {
                    "value": normalized["ltp"],
                    "change": normalized["change"],
                    "change_pct": normalized["change_pct"],
                }

            watchlist_prices: list[dict[str, Any]] = []
            cached_watchlist_map = {
                str(row.get("symbol") or "").upper(): row
                for row in cached_watchlist
                if row.get("symbol")
            }
            for symbol_config in watchlist:
                quote = {"data": watchlist_quote_map.get((symbol_config.exchange, symbol_config.token), {})}
                normalized = self._normalize_quote(quote)
                cached_row = cached_watchlist_map.get(symbol_config.symbol.upper(), {})
                cached_ltp = float(cached_row.get("ltp") or 0.0)
                use_live_quote = validate_quote_snapshot(
                    ltp=normalized["ltp"],
                    close=normalized["close"],
                    cached_ltp=cached_ltp if cached_ltp > 0 else None,
                )
                if normalized["ltp"] > 0 and not use_live_quote:
                    logger.warning("Rejected suspicious live quote for %s; using cached fallback.", symbol_config.symbol)
                ltp = normalized["ltp"] if use_live_quote else cached_ltp
                close = normalized["close"] if use_live_quote and normalized["close"] > 0 else float(cached_row.get("close") or 0.0)
                change = normalized["change"] if use_live_quote else float(cached_row.get("change") or 0.0)
                change_pct = normalized["change_pct"] if use_live_quote else float(cached_row.get("change_pct") or 0.0)
                watchlist_prices.append(
                    {
                        "symbol": symbol_config.symbol,
                        "ltp": ltp,
                        "close": close,
                        "change": change,
                        "change_pct": change_pct,
                    }
                )

            refreshed_at = datetime.now(tz=settings.tzinfo).isoformat()
            self.cache.set_json("live:indices", indices, ttl=120)
            self.cache.set_json("live:watchlist_prices", watchlist_prices, ttl=120)
            self.cache.set_json("live:cache_state", {"refreshed_at": refreshed_at}, ttl=120)
            return {"indices": indices, "watchlist_prices": watchlist_prices}

    def fetch_quotes_for_symbols(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}

        symbol_map = self.historical_fetcher.load_symbol_map()
        selected = [symbol_map[symbol.upper()] for symbol in symbols if symbol.upper() in symbol_map]
        if not selected:
            return {}

        exchange_tokens: dict[str, list[str]] = {}
        for symbol_config in selected:
            exchange_tokens.setdefault(symbol_config.exchange, []).append(symbol_config.token)

        try:
            market_data = self.angel_client.get_market_data("OHLC", exchange_tokens)
        except Exception:
            cached_rows = self.cache.get_json("live:watchlist_prices", [])
            cached_prices = {
                str(row.get("symbol") or "").upper(): float(row.get("ltp") or 0.0)
                for row in cached_rows
                if row.get("symbol")
            }
            return {
                symbol_config.symbol: cached_prices[symbol_config.symbol]
                for symbol_config in selected
                if cached_prices.get(symbol_config.symbol)
            }
        quote_map = self._build_quote_map(market_data)
        prices: dict[str, float] = {}
        cached_rows = self.cache.get_json("live:watchlist_prices", [])
        cached_prices = {
            str(row.get("symbol") or "").upper(): float(row.get("ltp") or 0.0)
            for row in cached_rows
            if row.get("symbol")
        }
        for symbol_config in selected:
            quote = {"data": quote_map.get((symbol_config.exchange, symbol_config.token), {})}
            normalized = self._normalize_quote(quote)
            cached_ltp = cached_prices.get(symbol_config.symbol.upper())
            if validate_quote_snapshot(
                ltp=normalized["ltp"],
                close=normalized["close"],
                cached_ltp=cached_ltp if cached_ltp and cached_ltp > 0 else None,
            ):
                prices[symbol_config.symbol] = normalized["ltp"]
            elif cached_ltp and cached_ltp > 0:
                logger.warning("Rejected suspicious quote for %s; using cached price.", symbol_config.symbol)
                prices[symbol_config.symbol] = cached_ltp
        return prices


_service: MarketDataService | None = None
_service_lock = Lock()


def get_market_data_service() -> MarketDataService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = MarketDataService()
    return _service
