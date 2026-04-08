from __future__ import annotations

import re
from threading import Lock
from typing import Any
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.config import get_settings


settings = get_settings()


class GlobalMarketClient:
    YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
    YAHOO_INTRADAY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1m&includePrePost=true"
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=6, pool_maxsize=6)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(self.DEFAULT_HEADERS)

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed != parsed:
            return None
        return parsed

    def _fetch_chart(self, ticker: str) -> dict[str, float]:
        url = self.YAHOO_CHART_URL.format(ticker=ticker)
        response = self.session.get(url, timeout=settings.global_risk_yahoo_timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        result = (((payload or {}).get("chart") or {}).get("result") or [None])[0] or {}
        quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
        closes = [self._coerce_float(value) for value in list(quote.get("close") or [])]
        valid_closes = [value for value in closes if value is not None and value > 0]
        if len(valid_closes) < 2:
            raise ValueError(f"Yahoo chart response for {ticker} did not contain two valid closes")
        prev_close = valid_closes[-2]
        latest_close = valid_closes[-1]
        change_pct = ((latest_close - prev_close) / prev_close) * 100.0
        return {
            "prev_close": round(prev_close, 4),
            "latest_close": round(latest_close, 4),
            "change_pct": round(change_pct, 4),
        }

    def _fetch_intraday_chart(self, ticker: str) -> dict[str, Any]:
        url = self.YAHOO_INTRADAY_URL.format(ticker=ticker)
        response = self.session.get(url, timeout=settings.global_risk_yahoo_timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        result = (((payload or {}).get("chart") or {}).get("result") or [None])[0] or {}
        meta = result.get("meta") or {}
        timestamps = list(result.get("timestamp") or [])
        quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
        closes = [self._coerce_float(value) for value in list(quote.get("close") or [])]

        latest_close: float | None = None
        latest_timestamp: int | None = None
        for index in range(min(len(timestamps), len(closes)) - 1, -1, -1):
            close = closes[index]
            if close is None or close <= 0:
                continue
            latest_close = close
            latest_timestamp = int(timestamps[index])
            break
        if latest_close is None:
            raise ValueError(f"Yahoo intraday chart response for {ticker} did not contain a valid live close")

        prev_close = (
            self._coerce_float(meta.get("chartPreviousClose"))
            or self._coerce_float(meta.get("previousClose"))
            or self._coerce_float(meta.get("regularMarketPreviousClose"))
        )
        if prev_close is None or prev_close <= 0:
            valid_closes = [value for value in closes if value is not None and value > 0]
            if len(valid_closes) < 2:
                raise ValueError(f"Yahoo intraday chart response for {ticker} did not contain a previous close")
            prev_close = valid_closes[-2]

        change = latest_close - prev_close
        change_pct = (change / prev_close) if prev_close else 0.0
        updated_at = None
        if latest_timestamp is not None:
            updated_at = datetime.fromtimestamp(latest_timestamp, tz=settings.tzinfo).isoformat()
        return {
            "value": round(latest_close, 4),
            "change": round(change, 4),
            "change_pct": round(change_pct, 6),
            "updated_at": updated_at,
            "source": "YAHOO_1M",
            "status": "ALT_FEED",
            "is_delayed": False,
        }

    @staticmethod
    def _coerce_number_from_text(value: str) -> float | None:
        cleaned = re.sub(r"[^0-9+\-.,]", "", value or "")
        if not cleaned:
            return None
        try:
            return float(cleaned.replace(",", ""))
        except ValueError:
            return None

    def _parse_public_quote_page(self, html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        lines = [line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()]
        text = " ".join(lines)
        quote_match = re.search(
            r"(?:Share Price\s+)?(?:NSE|BSE|MCX)\s+([0-9,]+(?:\.[0-9]+)?)\s+([+\-]?[0-9,]+(?:\.[0-9]+)?)\s*\(\s*([+\-]?[0-9]+(?:\.[0-9]+)?)\s*%\s*\)",
            text,
            flags=re.IGNORECASE,
        )
        value = self._coerce_number_from_text(quote_match.group(1)) if quote_match else None
        change = self._coerce_number_from_text(quote_match.group(2)) if quote_match else None
        change_pct_points = self._coerce_number_from_text(quote_match.group(3)) if quote_match else None
        updated_match = re.search(
            r"Last Updated on\s+([0-9]{2}\s+[A-Za-z]{3}\s+[0-9]{4})\s+at\s+([0-9]{2}:[0-9]{2}(?::[0-9]{2})?)",
            text,
            flags=re.IGNORECASE,
        )
        if value is None or change is None or change_pct_points is None:
            raise ValueError("Could not parse public quote page payload")

        updated_at = None
        if updated_match:
            updated_raw = f"{updated_match.group(1)} {updated_match.group(2)}"
            for fmt in ("%d %b %Y %H:%M:%S", "%d %b %Y %H:%M"):
                try:
                    parsed = datetime.strptime(updated_raw, fmt)
                    if parsed.year > 1980:
                        updated_at = parsed.replace(tzinfo=settings.tzinfo).isoformat()
                    break
                except ValueError:
                    continue

        return {
            "value": round(value, 4),
            "change": round(change, 4),
            "change_pct": round(change_pct_points / 100.0, 6),
            "updated_at": updated_at,
            "source": "DHAN_PUBLIC_PAGE",
            "status": "DELAYED_FALLBACK",
            "is_delayed": True,
        }

    def _fetch_public_quote(self, url: str) -> dict[str, Any]:
        response = self.session.get(url, timeout=settings.global_risk_yahoo_timeout_seconds)
        response.raise_for_status()
        return self._parse_public_quote_page(response.text)

    def fetch_sp500(self) -> dict[str, float]:
        return self._fetch_chart("%5EGSPC")

    def fetch_brent_crude(self) -> dict[str, float]:
        return self._fetch_chart("BZ%3DF")

    def fetch_usdinr(self) -> dict[str, float]:
        return self._fetch_chart("INR%3DX")

    def fetch_live_brent_crude(self) -> dict[str, Any]:
        return self._fetch_intraday_chart("BZ%3DF")

    def fetch_live_usdinr(self) -> dict[str, Any]:
        return self._fetch_intraday_chart("INR%3DX")

    def fetch_gift_nifty_public(self) -> dict[str, Any]:
        return self._fetch_public_quote("https://dhan.co/indices/gift-nifty-share-price/")

    def fetch_mcx_crude_public(self) -> dict[str, Any]:
        return self._fetch_public_quote("https://dhan.co/commodity/crude-oil-options-summary/")


_client: GlobalMarketClient | None = None
_client_lock = Lock()


def get_global_market_client() -> GlobalMarketClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = GlobalMarketClient()
    return _client


__all__ = ["GlobalMarketClient", "get_global_market_client"]
