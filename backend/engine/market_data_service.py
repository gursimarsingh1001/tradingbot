from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any

from backend.config import get_settings
from backend.data.angel_one_client import get_angel_one_client
from backend.data.dhan_client import get_dhan_client
from backend.data.global_market_client import get_global_market_client
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.redis_client import get_cache


settings = get_settings()


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
    "FINNIFTY": QuoteTarget(symbol="FINNIFTY", token="99926037", exchange="NSE", trading_symbol="Nifty Fin Service"),
}

DHAN_BENCHMARK_TARGETS: dict[str, QuoteTarget] = {
    "GIFTNIFTY": QuoteTarget(symbol="GIFTNIFTY", token="DHAN_GIFTNIFTY", exchange="IDX_I", trading_symbol="GIFT Nifty"),
    "MCX_CRUDE": QuoteTarget(symbol="CRUDEOIL", token="DHAN_MCX_CRUDE", exchange="MCX_COMM", trading_symbol="MCX Crude"),
}


class MarketDataService:
    CACHE_REFRESH_SECONDS = 5
    BENCHMARK_REFRESH_SECONDS = 1
    EXTERNAL_REFRESH_SECONDS = 10
    LIVE_WATCHLIST_LIMIT = 47

    def __init__(self) -> None:
        self.cache = get_cache()
        self.angel_client = get_angel_one_client()
        self.dhan_client = get_dhan_client()
        self.global_market_client = get_global_market_client()
        self.historical_fetcher = HistoricalFetcher(angel_client=self.angel_client)
        self._lock = Lock()

    @staticmethod
    def _default_indices() -> dict[str, Any]:
        return {
            "NIFTY50": {"value": 0.0, "change": 0.0, "change_pct": 0.0, "label": "Nifty 50", "status": "SYNCING"},
            "BANKNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0, "label": "Nifty Bank", "status": "SYNCING"},
            "SENSEX": {"value": 0.0, "change": 0.0, "change_pct": 0.0, "label": "Sensex", "status": "SYNCING"},
            "FINNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0, "label": "Fin Nifty", "status": "SYNCING"},
            "GIFTNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0, "label": "GIFT Nifty", "status": "SYNCING"},
            "MCX_CRUDE": {"value": 0.0, "change": 0.0, "change_pct": 0.0, "label": "MCX Crude", "status": "SYNCING"},
            "BRENT_CRUDE": {"value": 0.0, "change": 0.0, "change_pct": 0.0, "label": "Brent Crude", "status": "SYNCING"},
            "USDINR": {"value": 0.0, "change": 0.0, "change_pct": 0.0, "label": "USD/INR", "status": "SYNCING"},
        }

    def _is_key_stale(self, state_key: str, *, threshold_seconds: int, force: bool = False) -> bool:
        if force:
            return True
        state = self.cache.get_json(state_key, {})
        refreshed_at = state.get("refreshed_at")
        if not refreshed_at:
            return True
        try:
            last_refresh = datetime.fromisoformat(refreshed_at)
        except ValueError:
            return True
        age = (datetime.now(tz=settings.tzinfo) - last_refresh).total_seconds()
        return age >= threshold_seconds

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
        return self._is_key_stale("live:cache_state", threshold_seconds=self.CACHE_REFRESH_SECONDS, force=force)

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

    @staticmethod
    def _benchmark_payload(
        *,
        label: str,
        value: float,
        change: float,
        change_pct: float,
        source: str,
        status: str,
        updated_at: str | None = None,
        is_delayed: bool = False,
    ) -> dict[str, Any]:
        return {
            "label": label,
            "value": float(value),
            "change": float(change),
            "change_pct": float(change_pct),
            "source": source,
            "status": status,
            "updated_at": updated_at,
            "is_delayed": bool(is_delayed),
        }

    def _build_primary_index_quotes(self, cached_indices: dict[str, Any]) -> dict[str, Any]:
        exchange_tokens: dict[str, list[str]] = {}
        for target in INDEX_TARGETS.values():
            exchange_tokens.setdefault(target.exchange, []).append(target.token)
        market_data = self.angel_client.get_market_data("OHLC", exchange_tokens)
        quote_map = self._build_quote_map(market_data)

        indices: dict[str, Any] = {}
        refreshed_at = datetime.now(tz=settings.tzinfo).isoformat()
        for key, target in INDEX_TARGETS.items():
            quote = {"data": quote_map.get((target.exchange, target.token), {})}
            normalized = self._normalize_quote(quote)
            fallback = cached_indices.get(key, {})
            if normalized["ltp"] <= 0 and float(fallback.get("value") or 0.0) > 0:
                indices[key] = fallback
                continue
            indices[key] = self._benchmark_payload(
                label=target.trading_symbol,
                value=normalized["ltp"],
                change=normalized["change"],
                change_pct=normalized["change_pct"],
                source="ANGEL_ONE",
                status="LIVE",
                updated_at=refreshed_at,
            )
        return indices

    def _build_dhan_benchmark_quotes(self, cached_indices: dict[str, Any]) -> dict[str, Any]:
        market_data = self.dhan_client.get_market_data(list(DHAN_BENCHMARK_TARGETS.values()))
        quote_map = self._build_quote_map(market_data)

        indices: dict[str, Any] = {}
        refreshed_at = datetime.now(tz=settings.tzinfo).isoformat()
        for key, target in DHAN_BENCHMARK_TARGETS.items():
            quote = {"data": quote_map.get((target.exchange, target.token), {})}
            normalized = self._normalize_quote(quote)
            fallback = cached_indices.get(key, {})
            if normalized["ltp"] <= 0 and float(fallback.get("value") or 0.0) > 0:
                indices[key] = fallback
                continue
            if normalized["ltp"] <= 0:
                continue
            indices[key] = self._benchmark_payload(
                label=target.trading_symbol,
                value=normalized["ltp"],
                change=normalized["change"],
                change_pct=normalized["change_pct"],
                source="DHAN_OHLC",
                status="LIVE",
                updated_at=refreshed_at,
            )
        return indices

    def _build_external_benchmark_quotes(self, cached_indices: dict[str, Any]) -> dict[str, Any]:
        external: dict[str, Any] = {}
        fetchers = {
            "GIFTNIFTY": (self.global_market_client.fetch_gift_nifty_public, "GIFT Nifty"),
            "MCX_CRUDE": (self.global_market_client.fetch_mcx_crude_public, "MCX Crude"),
            "BRENT_CRUDE": (self.global_market_client.fetch_live_brent_crude, "Brent Crude"),
            "USDINR": (self.global_market_client.fetch_live_usdinr, "USD/INR"),
        }
        for key, (fetcher, label) in fetchers.items():
            existing = cached_indices.get(key, {})
            if (
                float(existing.get("value") or 0.0) > 0
                and not bool(existing.get("is_delayed"))
                and str(existing.get("status") or "").upper() == "LIVE"
            ):
                continue
            try:
                snapshot = fetcher()
                external[key] = self._benchmark_payload(
                    label=label,
                    value=float(snapshot.get("value") or 0.0),
                    change=float(snapshot.get("change") or 0.0),
                    change_pct=float(snapshot.get("change_pct") or 0.0),
                    source=str(snapshot.get("source") or "ALT_FEED"),
                    status=str(snapshot.get("status") or "ALT_FEED"),
                    updated_at=snapshot.get("updated_at"),
                    is_delayed=bool(snapshot.get("is_delayed")),
                )
            except Exception:
                fallback = cached_indices.get(key)
                if fallback:
                    external[key] = fallback
        return external

    def refresh_live_benchmarks(self, *, force: bool = False) -> dict[str, Any]:
        cached_indices = self.cache.get_json("live:benchmarks", self._default_indices())
        primary_stale = self._is_key_stale("live:benchmark_state", threshold_seconds=self.BENCHMARK_REFRESH_SECONDS, force=force)
        external_stale = self._is_key_stale("live:benchmark_external_state", threshold_seconds=self.EXTERNAL_REFRESH_SECONDS, force=force)

        if not primary_stale and not external_stale:
            return {"indices": cached_indices}

        with self._lock:
            cached_indices = self.cache.get_json("live:benchmarks", cached_indices)
            primary_stale = self._is_key_stale("live:benchmark_state", threshold_seconds=self.BENCHMARK_REFRESH_SECONDS, force=force)
            external_stale = self._is_key_stale("live:benchmark_external_state", threshold_seconds=self.EXTERNAL_REFRESH_SECONDS, force=force)
            merged = dict(cached_indices)

            if primary_stale:
                try:
                    merged.update(self._build_primary_index_quotes(cached_indices))
                except Exception as angel_exc:
                    try:
                        merged.update(self._build_dhan_benchmark_quotes(cached_indices))
                    except Exception:
                        print(f"Primary live benchmark refresh failed: {angel_exc}")
                self.cache.set_json(
                    "live:benchmark_state",
                    {"refreshed_at": datetime.now(tz=settings.tzinfo).isoformat()},
                    ttl=120,
                )

            if external_stale:
                try:
                    dhan_quotes = self._build_dhan_benchmark_quotes(merged)
                    if dhan_quotes:
                        merged.update(dhan_quotes)
                except Exception:
                    pass
                merged.update(self._build_external_benchmark_quotes(merged))
                self.cache.set_json(
                    "live:benchmark_external_state",
                    {"refreshed_at": datetime.now(tz=settings.tzinfo).isoformat()},
                    ttl=300,
                )

            self.cache.set_json("live:benchmarks", merged, ttl=300)
            self.cache.set_json("live:indices", merged, ttl=300)
            return {"indices": merged}

    def refresh_market_cache(self, *, force: bool = False, watchlist_limit: int | None = None) -> dict[str, Any]:
        watchlist_limit = watchlist_limit or self.LIVE_WATCHLIST_LIMIT
        cached_indices = self.refresh_live_benchmarks(force=force).get("indices", self._default_indices())
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
            exchange_tokens: dict[str, list[str]] = {}
            for symbol_config in watchlist:
                exchange_tokens.setdefault(symbol_config.exchange, []).append(symbol_config.token)

            try:
                market_data = self.angel_client.get_market_data("OHLC", exchange_tokens)
            except Exception as e:
                try:
                    market_data = self.dhan_client.get_market_data(watchlist)
                    if not market_data or not market_data.get("data", {}).get("fetched", []):
                        raise Exception("Dhan returned empty data")
                except Exception as e2:
                    print(f"AngelOne & Dhan both failed in refresh_market_cache: {e} -> {e2}")
                    return {
                        "indices": self.cache.get_json("live:indices", cached_indices),
                        "watchlist_prices": self.cache.get_json("live:watchlist_prices", cached_watchlist),
                    }
            quote_map = self._build_quote_map(market_data)

            watchlist_prices: list[dict[str, Any]] = []
            cached_watchlist_map = {
                str(row.get("symbol") or "").upper(): row
                for row in cached_watchlist
                if row.get("symbol")
            }
            for symbol_config in watchlist:
                quote = {"data": quote_map.get((symbol_config.exchange, symbol_config.token), {})}
                normalized = self._normalize_quote(quote)
                cached_row = cached_watchlist_map.get(symbol_config.symbol.upper(), {})
                ltp = normalized["ltp"] if normalized["ltp"] > 0 else float(cached_row.get("ltp") or 0.0)
                close = normalized["close"] if normalized["close"] > 0 else float(cached_row.get("close") or 0.0)
                change = normalized["change"] if normalized["ltp"] > 0 else float(cached_row.get("change") or 0.0)
                change_pct = normalized["change_pct"] if normalized["ltp"] > 0 else float(cached_row.get("change_pct") or 0.0)
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
            self.cache.set_json("live:watchlist_prices", watchlist_prices, ttl=120)
            self.cache.set_json("live:cache_state", {"refreshed_at": refreshed_at}, ttl=120)
            return {"indices": cached_indices, "watchlist_prices": watchlist_prices}

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
        except Exception as e:
            try:
                market_data = self.dhan_client.get_market_data(selected)
                if not market_data or not market_data.get("data", {}).get("fetched", []):
                    raise Exception("Dhan returned empty data")
            except Exception as e2:
                print(f"AngelOne & Dhan both failed in fetch_quotes_for_symbols: {e} -> {e2}")
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
        for symbol_config in selected:
            quote = {"data": quote_map.get((symbol_config.exchange, symbol_config.token), {})}
            normalized = self._normalize_quote(quote)
            if normalized["ltp"]:
                prices[symbol_config.symbol] = normalized["ltp"]
        return prices


_service: MarketDataService | None = None


def get_market_data_service() -> MarketDataService:
    global _service
    if _service is None:
        _service = MarketDataService()
    return _service
