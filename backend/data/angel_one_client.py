from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import os
from pathlib import Path
import time
from threading import Lock
from typing import Any, Callable

import pandas as pd
import pyotp

from backend.config import get_settings
from backend.data.data_quality import validate_ohlcv_frame
from backend.logging_utils import get_logger

try:
    from SmartApi import SmartConnect
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
except Exception:  # pragma: no cover
    SmartConnect = None
    SmartWebSocketV2 = None


settings = get_settings()
logger = get_logger(__name__)


@dataclass
class AngelSession:
    auth_token: str
    refresh_token: str
    feed_token: str


class AngelOneClient:
    def __init__(self) -> None:
        self._client: SmartConnect | None = None
        self._session: AngelSession | None = None
        self._ws: SmartWebSocketV2 | None = None
        self._lock = Lock()

    @staticmethod
    def _is_invalid_token_payload(payload: Any) -> bool:
        if payload is None:
            return False
        text = str(payload).lower()
        return "invalid token" in text or "ag8001" in text

    def _invalidate_session_locked(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close_connection()
        except Exception:
            pass
        self._ws = None
        self._session = None
        self._client = None

    def invalidate_session(self) -> None:
        with self._lock:
            self._invalidate_session_locked()

    def authenticate(self, *, force_refresh: bool = False) -> AngelSession:
        if SmartConnect is None:
            raise RuntimeError("smartapi-python is not installed in this environment.")
        with self._lock:
            if force_refresh:
                self._invalidate_session_locked()
            if self._session is not None:
                return self._session
            smartapi_cwd = Path("/tmp/trading-bot-smartapi")
            smartapi_cwd.mkdir(parents=True, exist_ok=True)
            previous_cwd = Path.cwd()
            try:
                os.chdir(smartapi_cwd)
                self._client = SmartConnect(api_key=settings.angel_one_api_key)
            finally:
                os.chdir(previous_cwd)
            totp = pyotp.TOTP(settings.angel_one_totp_secret).now()
            data = self._client.generateSession(
                settings.angel_one_client_id,
                settings.angel_one_password,
                totp,
            )
            if not data or not data.get("status", True):
                raise RuntimeError(f"Angel One authentication failed: {data}")
            self._session = AngelSession(
                auth_token=data["data"]["jwtToken"],
                refresh_token=data["data"]["refreshToken"],
                feed_token=self._client.getfeedToken(),
            )
            logger.info("Angel One session established for client %s", settings.angel_one_client_id)
            return self._session

    def _ensure_client(self) -> SmartConnect:
        self.authenticate()
        if self._client is None:
            raise RuntimeError("Angel One client is not initialized.")
        return self._client

    def _call_with_reauth(self, operation: Callable[[SmartConnect], Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(2):
            client = self._ensure_client()
            try:
                response = operation(client)
            except Exception as exc:
                if attempt == 0 and self._is_invalid_token_payload(exc):
                    self.invalidate_session()
                    continue
                last_error = exc
                break

            if self._is_invalid_token_payload(response):
                if attempt == 0:
                    self.invalidate_session()
                    continue
                raise RuntimeError(f"Angel One request failed after session refresh: {response}")
            return response

        if last_error is not None:
            raise last_error
        raise RuntimeError("Angel One request failed and no response was returned.")

    def get_historical_candles(
        self,
        symbol_token: str,
        *,
        exchange: str = "NSE",
        interval: str = "ONE_DAY",
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> pd.DataFrame:
        to_date = to_date or datetime.now(tz=settings.tzinfo)
        from_date = from_date or (to_date - timedelta(days=3650))
        params = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M"),
        }
        response: dict[str, Any] | None = None
        rate_limit_markers = (
            "too many requests",
            "ab1019",
            "exceeding access rate",
            "access denied",
            "parse the json response",
        )
        for attempt in range(5):
            try:
                response = self._call_with_reauth(lambda client: client.getCandleData(params))
            except Exception as exc:
                if attempt == 4:
                    raise
                if not any(marker in str(exc).lower() for marker in rate_limit_markers):
                    raise
                time.sleep(min(20, 3 * (attempt + 1)))
                continue

            error_message = str((response or {}).get("message") or "")
            error_code = str((response or {}).get("errorcode") or "")
            if (
                any(marker in error_code.lower() for marker in rate_limit_markers)
                or any(marker in error_message.lower() for marker in rate_limit_markers)
            ):
                if attempt == 4:
                    break
                time.sleep(min(20, 3 * (attempt + 1)))
                continue
            break

        if not response:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        candle_rows = response.get("data") or []
        frame = pd.DataFrame(
            candle_rows,
            columns=["Datetime", "Open", "High", "Low", "Close", "Volume"],
        )
        if frame.empty:
            return frame
        frame["Datetime"] = pd.to_datetime(frame["Datetime"])
        numeric_cols = ["Open", "High", "Low", "Close", "Volume"]
        frame[numeric_cols] = frame[numeric_cols].apply(pd.to_numeric, errors="coerce")
        validated = validate_ohlcv_frame(frame.set_index("Datetime").sort_index())
        if validated.empty and not frame.empty:
            logger.warning("Rejected invalid OHLCV payload from Angel One for token %s", symbol_token)
        return validated

    def get_ltp(self, trading_symbol: str, symbol_token: str, exchange: str = "NSE") -> dict[str, Any]:
        return self._call_with_reauth(lambda client: client.ltpData(exchange, trading_symbol, symbol_token))

    def get_market_data(self, mode: str, exchange_tokens: dict[str, list[str]]) -> dict[str, Any]:
        return self._call_with_reauth(lambda client: client.getMarketData(mode, exchange_tokens))

    def connect_market_stream(
        self,
        token_list: list[dict[str, Any]],
        on_data: Callable[[dict[str, Any]], None],
        correlation_id: str = "trading-bot",
        mode: int = 1,
    ) -> None:
        session = self.authenticate()
        if SmartWebSocketV2 is None:
            raise RuntimeError("SmartWebSocketV2 is unavailable in this environment.")
        smartapi_ws_cwd = Path("/tmp/trading-bot-smartapi-ws")
        smartapi_ws_cwd.mkdir(parents=True, exist_ok=True)
        previous_cwd = Path.cwd()
        try:
            os.chdir(smartapi_ws_cwd)
            self._ws = SmartWebSocketV2(
                session.auth_token,
                settings.angel_one_api_key,
                settings.angel_one_client_id,
                session.feed_token,
            )
        finally:
            os.chdir(previous_cwd)

        def _on_data(_: Any, message: dict[str, Any]) -> None:
            on_data(message)

        def _on_open(_: Any) -> None:
            if self._ws is not None:
                self._ws.subscribe(correlation_id, mode, token_list)

        self._ws.on_data = _on_data
        self._ws.on_open = _on_open
        self._ws.on_error = lambda _, error: logger.warning("Angel WebSocket error: %s", error)
        self._ws.on_close = lambda _: logger.info("Angel WebSocket closed")
        self._ws.connect()

    def disconnect_market_stream(self) -> None:
        if self._ws is not None:
            self._ws.close_connection()
            self._ws = None

    def health_check(self) -> bool:
        try:
            self.authenticate()
            return True
        except Exception:
            return False


_client: AngelOneClient | None = None
_client_lock = Lock()


def get_angel_one_client() -> AngelOneClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = AngelOneClient()
    return _client
