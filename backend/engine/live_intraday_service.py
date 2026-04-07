from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock, Thread
from typing import Any

import pandas as pd

from backend.config import get_settings
from backend.data.angel_one_client import get_angel_one_client
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.redis_client import get_cache
from backend.engine.market_data_service import INDEX_TARGETS


settings = get_settings()

EXCHANGE_NAME_TO_TYPE = {"NSE": 1, "BSE": 3}


class LiveIntradayService:
    INTRADAY_LOOKBACK_DAYS = 7
    INTRADAY_CANDLE_LIMIT = 600
    STREAM_MODE = 3

    def __init__(self) -> None:
        self.cache = get_cache()
        self.angel_client = get_angel_one_client()
        self.historical_fetcher = HistoricalFetcher(angel_client=self.angel_client)
        self._stream_lock = Lock()
        self._stream_thread: Thread | None = None
        self._stream_symbols: set[str] = set()
        self._token_lookup: dict[tuple[int, str], str] = {}
        self._last_trade_sync_at: dict[str, datetime] = {}
        self._trade_sync_interval_seconds = 1.0
        self._paper_trader = None

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed != parsed:
            return default
        return parsed

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_stream_price(value: Any) -> float:
        try:
            parsed = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if parsed <= 0:
            return 0.0
        return round(parsed / 100.0, 2)

    @staticmethod
    def _bucket_time(timestamp: datetime) -> datetime:
        return timestamp.replace(minute=(timestamp.minute // 5) * 5, second=0, microsecond=0)

    def _parse_stream_timestamp(self, message: dict[str, Any]) -> datetime:
        raw_value = (
            message.get("exchange_timestamp")
            or message.get("last_traded_timestamp")
            or message.get("packet_received_time")
            or 0
        )
        try:
            numeric = int(raw_value)
        except (TypeError, ValueError):
            numeric = 0
        if numeric <= 0:
            return datetime.now(tz=settings.tzinfo)
        if numeric > 10**12:
            numeric = numeric / 1000
        return datetime.fromtimestamp(numeric, tz=settings.tzinfo)

    @staticmethod
    def _intraday_candles_key(symbol: str) -> str:
        return f"live:intraday:candles:{symbol.upper()}"

    @staticmethod
    def _intraday_meta_key(symbol: str) -> str:
        return f"live:intraday:meta:{symbol.upper()}"

    @staticmethod
    def _latest_price_key(symbol: str) -> str:
        return f"live:intraday:last_price:{symbol.upper()}"

    def _paper_trader_instance(self):
        if self._paper_trader is None:
            from backend.engine.paper_trader_v2 import PaperTrader

            self._paper_trader = PaperTrader()
        return self._paper_trader

    def _touch_live_cache_state(self, timestamp: datetime) -> None:
        self.cache.set_json("live:cache_state", {"refreshed_at": timestamp.isoformat()}, ttl=120)

    def _load_intraday_candles(self, symbol: str) -> list[dict[str, Any]]:
        return self.cache.get_json(self._intraday_candles_key(symbol), [])

    def _store_intraday_candles(self, symbol: str, candles: list[dict[str, Any]]) -> None:
        self.cache.set_json(self._intraday_candles_key(symbol), candles[-self.INTRADAY_CANDLE_LIMIT :], ttl=60 * 60 * 12)

    def _serialize_candle_frame(self, frame: pd.DataFrame) -> list[dict[str, Any]]:
        working = frame.copy()
        if working.empty:
            return []
        if working.index.tz is None:
            working.index = working.index.tz_localize(settings.tzinfo)
        else:
            working.index = working.index.tz_convert(settings.tzinfo)
        records: list[dict[str, Any]] = []
        for index, row in working.iterrows():
            records.append(
                {
                    "date": index.isoformat(),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": self._safe_int(row.get("Volume"), 0),
                }
            )
        return records

    def seed_intraday_history(self, symbols: list[str], *, force: bool = False) -> None:
        symbol_map = self.historical_fetcher.load_symbol_map()
        now = datetime.now(tz=settings.tzinfo)
        active_bucket = self._bucket_time(now)
        for symbol in sorted({item.upper() for item in symbols if item}):
            cached = self._load_intraday_candles(symbol)
            if cached and not force:
                last_date = cached[-1].get("date")
                if last_date:
                    try:
                        last_timestamp = datetime.fromisoformat(last_date)
                    except ValueError:
                        last_timestamp = None
                    if last_timestamp is not None and last_timestamp.date() == now.date():
                        continue
            symbol_config = symbol_map.get(symbol)
            if symbol_config is None:
                continue
            try:
                frame = self.angel_client.get_historical_candles(
                    symbol_config.token,
                    exchange=symbol_config.exchange,
                    interval="FIVE_MINUTE",
                    from_date=now - timedelta(days=self.INTRADAY_LOOKBACK_DAYS),
                    to_date=now,
                )
            except Exception:
                continue
            if frame.empty:
                continue
            if frame.index.tz is None:
                frame.index = frame.index.tz_localize(settings.tzinfo)
            else:
                frame.index = frame.index.tz_convert(settings.tzinfo)
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
            if not frame.empty and self._bucket_time(frame.index[-1]) == active_bucket:
                frame = frame.iloc[:-1]
            candles = self._serialize_candle_frame(frame)
            if candles:
                self._store_intraday_candles(symbol, candles)
                self.cache.set_json(
                    self._intraday_meta_key(symbol),
                    {"seeded_at": now.isoformat(), "session_date": now.date().isoformat()},
                    ttl=60 * 60 * 12,
                )

    def get_intraday_frame(self, symbol: str) -> pd.DataFrame:
        candles = self._load_intraday_candles(symbol)
        if not candles:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        frame = pd.DataFrame(
            [
                {
                    "Datetime": item["date"],
                    "Open": item["open"],
                    "High": item["high"],
                    "Low": item["low"],
                    "Close": item["close"],
                    "Volume": item.get("volume", 0),
                }
                for item in candles
            ]
        )
        frame["Datetime"] = pd.to_datetime(frame["Datetime"])
        return frame.set_index("Datetime").sort_index()

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        prices: dict[str, float] = {}
        for symbol in sorted({item.upper() for item in symbols if item}):
            cached = self.cache.get_json(self._latest_price_key(symbol), None)
            if not isinstance(cached, dict):
                continue
            price = self._safe_float(cached.get("ltp"))
            if price > 0:
                prices[symbol] = price
        return prices

    def _update_watchlist_price_cache(self, symbol: str, *, ltp: float, close: float) -> None:
        watchlist = self.cache.get_json("live:watchlist_prices", [])
        updated = False
        change = ltp - close if close else 0.0
        change_pct = (change / close) if close else 0.0
        for row in watchlist:
            if str(row.get("symbol") or "").upper() != symbol.upper():
                continue
            row["ltp"] = ltp
            row["close"] = close
            row["change"] = change
            row["change_pct"] = change_pct
            updated = True
            break
        if updated:
            self.cache.set_json("live:watchlist_prices", watchlist, ttl=120)
            self._touch_live_cache_state(datetime.now(tz=settings.tzinfo))

    def _update_index_cache(self, symbol: str, *, ltp: float, close: float, timestamp: datetime) -> None:
        indices = self.cache.get_json(
            "live:indices",
            {
                "NIFTY50": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "BANKNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "SENSEX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "INDIA_VIX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            },
        )
        change = ltp - close if close else 0.0
        change_pct = (change / close) if close else 0.0
        indices[symbol.upper()] = {
            "value": round(ltp, 2),
            "change": round(change, 2),
            "change_pct": change_pct,
            "timestamp": timestamp.isoformat(),
        }
        self.cache.set_json("live:indices", indices, ttl=120)
        self._touch_live_cache_state(timestamp)

    def _maybe_process_trade_tick(self, symbol: str, *, ltp: float, timestamp: datetime) -> None:
        last_processed = self._last_trade_sync_at.get(symbol.upper())
        if last_processed is not None and (timestamp - last_processed).total_seconds() < self._trade_sync_interval_seconds:
            return
        self._last_trade_sync_at[symbol.upper()] = timestamp
        try:
            self._paper_trader_instance().process_realtime_price(symbol.upper(), ltp, now=timestamp)
        except Exception:
            return

    def _update_intraday_candle(self, symbol: str, message: dict[str, Any]) -> None:
        timestamp = self._parse_stream_timestamp(message)
        bucket = self._bucket_time(timestamp)
        ltp = self._normalize_stream_price(message.get("last_traded_price"))
        if ltp <= 0:
            return
        cumulative_day_volume = self._safe_int(message.get("volume_trade_for_the_day"))
        last_trade_qty = self._safe_int(message.get("last_traded_quantity"))

        candles = self._load_intraday_candles(symbol)
        metadata = self.cache.get_json(self._intraday_meta_key(symbol), {})
        last_day_volume = self._safe_int(metadata.get("last_day_volume"))
        session_date = str(metadata.get("session_date") or timestamp.date().isoformat())
        if session_date != timestamp.date().isoformat():
            last_day_volume = 0
            session_date = timestamp.date().isoformat()

        if cumulative_day_volume > 0 and last_day_volume > 0 and cumulative_day_volume >= last_day_volume:
            volume_delta = cumulative_day_volume - last_day_volume
        elif last_trade_qty > 0:
            volume_delta = last_trade_qty
        else:
            volume_delta = 0

        metadata["last_day_volume"] = cumulative_day_volume or last_day_volume
        metadata["session_date"] = session_date
        metadata["last_tick_at"] = timestamp.isoformat()
        metadata["last_price"] = ltp

        bucket_iso = bucket.isoformat()
        if candles and candles[-1].get("date") == bucket_iso:
            candle = candles[-1]
            candle["high"] = round(max(self._safe_float(candle.get("high")), ltp), 2)
            candle["low"] = round(min(self._safe_float(candle.get("low"), ltp), ltp), 2)
            candle["close"] = round(ltp, 2)
            candle["volume"] = self._safe_int(candle.get("volume")) + max(volume_delta, 0)
        else:
            candles.append(
                {
                    "date": bucket_iso,
                    "open": round(ltp, 2),
                    "high": round(ltp, 2),
                    "low": round(ltp, 2),
                    "close": round(ltp, 2),
                    "volume": max(volume_delta, 0),
                }
            )
        self._store_intraday_candles(symbol, candles)
        self.cache.set_json(self._intraday_meta_key(symbol), metadata, ttl=60 * 60 * 12)

        close = self._normalize_stream_price(message.get("closed_price"))
        self.cache.set_json(
            self._latest_price_key(symbol),
            {
                "symbol": symbol.upper(),
                "ltp": ltp,
                "close": close,
                "timestamp": timestamp.isoformat(),
            },
            ttl=60 * 60 * 12,
        )
        if close:
            self._update_watchlist_price_cache(symbol, ltp=ltp, close=close)
        self._touch_live_cache_state(timestamp)

    def _handle_stream_message(self, message: dict[str, Any]) -> None:
        exchange_type = self._safe_int(message.get("exchange_type"))
        token = str(message.get("token") or "")
        symbol = self._token_lookup.get((exchange_type, token))
        if symbol is None:
            return
        timestamp = self._parse_stream_timestamp(message)
        ltp = self._normalize_stream_price(message.get("last_traded_price"))
        close = self._normalize_stream_price(message.get("closed_price"))
        if symbol in INDEX_TARGETS:
            if ltp > 0:
                self._update_index_cache(symbol, ltp=ltp, close=close, timestamp=timestamp)
            return
        self._update_intraday_candle(symbol, message)
        if ltp > 0:
            self._maybe_process_trade_tick(symbol, ltp=ltp, timestamp=timestamp)

    def start_stream(self, symbols: list[str]) -> None:
        selected_symbols = sorted({item.upper() for item in symbols if item})
        symbol_map = self.historical_fetcher.load_symbol_map()
        grouped: dict[int, list[str]] = {}
        lookup: dict[tuple[int, str], str] = {}
        for symbol, target in INDEX_TARGETS.items():
            exchange_type = EXCHANGE_NAME_TO_TYPE.get(target.exchange.upper())
            if exchange_type is None:
                continue
            grouped.setdefault(exchange_type, []).append(target.token)
            lookup[(exchange_type, target.token)] = symbol
        for symbol in selected_symbols:
            config = symbol_map.get(symbol)
            if config is None:
                continue
            exchange_type = EXCHANGE_NAME_TO_TYPE.get(config.exchange.upper())
            if exchange_type is None:
                continue
            grouped.setdefault(exchange_type, []).append(config.token)
            lookup[(exchange_type, config.token)] = config.symbol
        if not grouped:
            return

        with self._stream_lock:
            if self._stream_thread is not None and self._stream_thread.is_alive() and set(selected_symbols) == self._stream_symbols:
                return
            if self._stream_thread is not None and self._stream_thread.is_alive():
                self.stop_stream()

            self._stream_symbols = set(selected_symbols)
            self._token_lookup = lookup
            token_list = [{"exchangeType": key, "tokens": value} for key, value in grouped.items()]

            def _runner() -> None:
                try:
                    self.angel_client.connect_market_stream(
                        token_list,
                        self._handle_stream_message,
                        correlation_id="trading-bot-live",
                        mode=self.STREAM_MODE,
                    )
                finally:
                    self.cache.set_json(
                        "live:intraday:stream_state",
                        {
                            "active": False,
                            "symbols": sorted(self._stream_symbols),
                            "updated_at": datetime.now(tz=settings.tzinfo).isoformat(),
                        },
                        ttl=60 * 60 * 12,
                    )

            self._stream_thread = Thread(target=_runner, daemon=True, name="angel-live-intraday")
            self._stream_thread.start()
            self.cache.set_json(
                "live:intraday:stream_state",
                {
                    "active": True,
                    "symbols": selected_symbols,
                    "updated_at": datetime.now(tz=settings.tzinfo).isoformat(),
                },
                ttl=60 * 60 * 12,
            )

    def stop_stream(self) -> None:
        with self._stream_lock:
            self.angel_client.disconnect_market_stream()
            self._stream_symbols = set()
            self._token_lookup = {}
            self.cache.set_json(
                "live:intraday:stream_state",
                {"active": False, "symbols": [], "updated_at": datetime.now(tz=settings.tzinfo).isoformat()},
                ttl=60 * 60 * 12,
            )

    def ensure_runtime(self, symbols: list[str], *, force_seed: bool = False) -> None:
        selected = sorted({item.upper() for item in symbols if item})
        if not selected:
            return
        self.seed_intraday_history(selected, force=force_seed)
        self.start_stream(selected)

    def set_active_signals(self, signals: list[dict[str, Any]]) -> None:
        self.cache.set_json("live:active_signals", signals, ttl=60 * 30)


_service: LiveIntradayService | None = None


def get_live_intraday_service() -> LiveIntradayService:
    global _service
    if _service is None:
        _service = LiveIntradayService()
    return _service
