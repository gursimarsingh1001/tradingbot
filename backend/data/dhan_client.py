from __future__ import annotations

import re
import time
from threading import Lock
from typing import Any
from datetime import datetime, timedelta
import pandas as pd
import requests

from backend.config import get_settings

try:
    from dhanhq import dhanhq
except ImportError:
    dhanhq = None

settings = get_settings()

class DhanClient:
    QUOTE_URL = "https://api.dhan.co/v2/marketfeed/ohlc"

    def __init__(self) -> None:
        self._client = None
        self._lock = Lock()
        self._security_id_map: dict[str, dict[str, Any]] = {}
        self._map_loaded = False

    def authenticate(self) -> None:
        if dhanhq is None:
            raise RuntimeError("dhanhq is not installed in this environment.")
        if not settings.dhan_client_id or not settings.dhan_access_token:
            raise ValueError("Dhan credentials not configured.")
            
        with self._lock:
            if self._client is not None:
                return
            self._client = dhanhq(settings.dhan_client_id, settings.dhan_access_token)
            
    def _ensure_client(self):
        self.authenticate()
        if self._client is None:
            raise RuntimeError("Dhan client is not initialized.")
        return self._client

    @staticmethod
    def _normalize_alias(value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", value.upper()).strip()

    @classmethod
    def _symbol_aliases(cls, row: pd.Series) -> set[str]:
        aliases: set[str] = set()
        for column in ("SEM_TRADING_SYMBOL", "SEM_CUSTOM_SYMBOL", "SM_SYMBOL_NAME"):
            raw = str(row.get(column, "") or "").strip()
            if not raw:
                continue
            aliases.add(raw.upper())
            normalized = cls._normalize_alias(raw)
            if normalized:
                aliases.add(normalized)
        return aliases

    @staticmethod
    def _exchange_segment_for_row(exchange: str, is_index: bool) -> str:
        if is_index:
            return "IDX_I"
        if exchange == "NSE":
            return "NSE_EQ"
        if exchange == "BSE":
            return "BSE_EQ"
        if exchange == "MCX":
            return "MCX_COMM"
        return exchange

    @staticmethod
    def _instrument_type_for_row(series: str, instrument_name: str, is_index: bool) -> str:
        if is_index:
            return "INDEX"
        if series == "EQ" or instrument_name == "EQUITY":
            return "EQUITY"
        return instrument_name or "EQUITY"

    @staticmethod
    def _entry_priority(entry: dict[str, Any]) -> tuple[int, int]:
        instrument = str(entry.get("instrument_type") or "").upper()
        exchange = str(entry.get("exchange") or "").upper()
        if instrument == "INDEX":
            base = 5
        elif exchange == "NSE" and instrument == "EQUITY":
            base = 4
        elif exchange == "MCX" and instrument == "FUTCOM":
            base = 4
        elif exchange == "BSE" and instrument == "EQUITY":
            base = 3
        else:
            base = 1
        try:
            token_score = int(str(entry.get("token") or "0"))
        except ValueError:
            token_score = 0
        return (base, token_score)

    @staticmethod
    def _quote_headers() -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": settings.dhan_access_token or "",
            "client-id": settings.dhan_client_id or "",
        }
        
    def _load_security_map(self) -> None:
        with self._lock:
            if self._map_loaded:
                return
            try:
                df = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv', low_memory=False)
                df = df[
                    (
                        (df['SEM_EXM_EXCH_ID'] == 'NSE')
                        & ((df['SEM_SERIES'] == 'EQ') | (df['SEM_INSTRUMENT_NAME'] == 'INDEX'))
                    )
                    | ((df['SEM_EXM_EXCH_ID'] == 'BSE') & (df['SEM_INSTRUMENT_NAME'] == 'EQUITY'))
                    | ((df['SEM_EXM_EXCH_ID'] == 'MCX') & (df['SEM_INSTRUMENT_NAME'].isin(['FUTCOM', 'OPTFUT'])))
                ]
                for _, row in df.iterrows():
                    exchange = str(row.get('SEM_EXM_EXCH_ID', 'NSE') or 'NSE').strip().upper()
                    series = str(row.get('SEM_SERIES', '') or '').strip().upper()
                    instrument_name = str(row.get('SEM_INSTRUMENT_NAME', '') or '').strip().upper()
                    token = str(row['SEM_SMST_SECURITY_ID']).strip()
                    is_index = instrument_name == 'INDEX'
                    if not token:
                        continue
                    entry = {
                        "token": token,
                        "is_index": is_index,
                        "exchange": exchange,
                        "exchange_segment": self._exchange_segment_for_row(exchange, is_index),
                        "instrument_type": self._instrument_type_for_row(series, instrument_name, is_index),
                    }
                    for alias in self._symbol_aliases(row):
                        existing = self._security_id_map.get(alias)
                        if existing is None or self._entry_priority(entry) >= self._entry_priority(existing):
                            self._security_id_map[alias] = entry
                self._map_loaded = True
            except Exception as exc:
                print(f"[DhanClient] Failed to load security map: {exc}")

    def get_dhan_info(self, symbol: str) -> dict[str, Any] | None:
        self._load_security_map()
        direct_key = str(symbol or "").strip().upper()
        if direct_key and direct_key in self._security_id_map:
            return self._security_id_map[direct_key]
        normalized = self._normalize_alias(symbol or "")
        if normalized and normalized in self._security_id_map:
            return self._security_id_map[normalized]
        return None

    def get_historical_candles(
        self,
        symbol_token: str,  # This will be AngelOne token, we MUST ignore it and use symbol name! Wait, the API signature expects symbol_token!
        # Actually, to make it compatible, we will modify HistoricalFetcher to pass the raw `symbol` to get_historical_candles instead of or along with symbol_token!
        # Let's add symbol to the kwargs of this method.
        *,
        symbol: str = "",
        exchange: str = "NSE",
        interval: str = "ONE_DAY",
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> pd.DataFrame:
        if not symbol:
            raise ValueError("Dhan client requires 'symbol' for historical candles, as Angel Tokens are incompatible.")
            
        dhan_info = self.get_dhan_info(symbol)
        if not dhan_info:
            print(f"[DhanClient] No Dhan info found for {symbol}")
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            
        dhan_token = dhan_info["token"]
        exchange_segment = str(dhan_info.get("exchange_segment") or "NSE_EQ")
        instrument_type = str(dhan_info.get("instrument_type") or "EQUITY")

        to_date = to_date or datetime.now(tz=settings.tzinfo)
        from_date = from_date or (to_date - timedelta(days=3650))
        
        client = self._ensure_client()
        
        response = None
        for attempt in range(3):
            try:
                # Dhan historical daily API
                # get_historical_daily_data(security_id, exchange_segment, instrument_type, expiry_code, from_date, to_date)
                # exchange_segment: 'NSE_EQ', instrument_type: 'EQUITY'
                response = client.get_historical_daily_data(
                    symbol=dhan_token,
                    exchange_segment=exchange_segment,
                    instrument_type=instrument_type,
                    expiry_code=0,
                    from_date=from_date.strftime("%Y-%m-%d"),
                    to_date=to_date.strftime("%Y-%m-%d")
                )
                if response.get("status") == "success":
                    break
                # rate limit or timeout
                time.sleep(2 * (attempt + 1))
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))

        if not response or response.get("status") != "success":
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        data = response.get("data") or {}
        # Dhan returns data like: {'start_Time': [...], 'open': [...], 'high': [...], 'low': [...], 'close': [...], 'volume': [...]}
        if not data.get("start_Time"):
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        # Extract timestamps carefully
        raw_times = data.get("start_Time", [])
        if not raw_times:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            
        # Dhan may return epoch integer/float values or string arrays
        try:
            if isinstance(raw_times[0], (int, float)):
                datetime_series = pd.to_datetime(raw_times, unit='s')
            else:
                datetime_series = pd.to_datetime(raw_times)
        except Exception:
            datetime_series = pd.to_datetime(raw_times, errors='coerce')

        df = pd.DataFrame({
            "Datetime": datetime_series,
            "Open": data.get("open", []),
            "High": data.get("high", []),
            "Low": data.get("low", []),
            "Close": data.get("close", []),
            "Volume": data.get("volume", []),
        })
        
        if df.empty:
            return df
            
        numeric_cols = ["Open", "High", "Low", "Close", "Volume"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
        return df.set_index("Datetime").sort_index()

    def get_market_data(self, symbol_configs: list[Any]) -> dict[str, Any]:
        """Fetch quotes from Dhan and mock AngelOne's batch response structure."""
        self._ensure_client()
        grouped_payload: dict[str, list[int]] = {}
        token_lookup: dict[tuple[str, str], list[Any]] = {}
        for symbol_config in symbol_configs:
            dhan_info = self.get_dhan_info(getattr(symbol_config, "symbol", ""))
            if not dhan_info:
                continue
            exchange_segment = str(dhan_info.get("exchange_segment") or "")
            token = str(dhan_info.get("token") or "")
            if not exchange_segment or not token:
                continue
            grouped_payload.setdefault(exchange_segment, []).append(int(token))
            token_lookup.setdefault((exchange_segment, token), []).append(symbol_config)

        if not grouped_payload:
            return {"data": {"fetched": []}}

        response = requests.post(
            self.QUOTE_URL,
            headers=self._quote_headers(),
            json=grouped_payload,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status") or "").lower() != "success":
            raise RuntimeError(f"Dhan market quote failed: {payload}")

        fetched: list[dict[str, Any]] = []
        for exchange_segment, rows in (payload.get("data") or {}).items():
            if not isinstance(rows, dict):
                continue
            for token, quote in rows.items():
                bound_targets = token_lookup.get((str(exchange_segment), str(token)), [])
                if not bound_targets:
                    continue
                quote_data = quote or {}
                ohlc = quote_data.get("ohlc") or {}
                last_price = float(quote_data.get("last_price") or 0.0)
                close = float(ohlc.get("close") or 0.0)
                for symbol_config in bound_targets:
                    fetched.append(
                        {
                            "exchange": getattr(symbol_config, "exchange", ""),
                            "symbolToken": getattr(symbol_config, "token", ""),
                            "ltp": last_price,
                            "close": close,
                        }
                    )
        return {"data": {"fetched": fetched}}

    def health_check(self) -> bool:
        try:
            self.authenticate()
            return True
        except Exception:
            return False

_dhan_client: DhanClient | None = None

def get_dhan_client() -> DhanClient:
    global _dhan_client
    if _dhan_client is None:
        _dhan_client = DhanClient()
    return _dhan_client
