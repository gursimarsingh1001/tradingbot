from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.config import get_settings
from backend.data.official_api_common import SimpleRateLimiter, first_date, first_float, first_text, iter_dict_records, normalize_key


settings = get_settings()


class BSEClient:
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
    }

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(self.DEFAULT_HEADERS)
        self._rate_limiter = SimpleRateLimiter(settings.official_bse_rate_limit_seconds)
        self._unavailable_until: datetime | None = None
        self._unavailable_reason: str | None = None

    def _mark_temporarily_unavailable(self, reason: str, *, minutes: int = 20) -> None:
        self._unavailable_until = datetime.now(tz=settings.tzinfo) + timedelta(minutes=minutes)
        self._unavailable_reason = reason

    def _ensure_available(self) -> None:
        if self._unavailable_until is None:
            return
        now = datetime.now(tz=settings.tzinfo)
        if now >= self._unavailable_until:
            self._unavailable_until = None
            self._unavailable_reason = None
            return
        raise RuntimeError(self._unavailable_reason or "BSE client temporarily unavailable")

    def _request_json(self, endpoint: str, *, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_available()
        self._rate_limiter.wait()
        url = f"{settings.official_bse_api_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        response = self.session.get(url, params=params, timeout=20)
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        if "json" not in content_type:
            snippet = response.text[:160].replace("\n", " ").replace("\r", " ").strip()
            reason = f"BSE API returned non-JSON response ({content_type or 'unknown'}): {snippet}"
            self._mark_temporarily_unavailable(reason)
            raise RuntimeError(reason)
        try:
            payload = response.json()
        except ValueError as exc:
            snippet = response.text[:160].replace("\n", " ").replace("\r", " ").strip()
            reason = f"BSE API returned invalid JSON: {snippet}"
            self._mark_temporarily_unavailable(reason)
            raise RuntimeError(reason) from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    def fetch_stock_info(self, scripcode: str) -> dict[str, Any]:
        return self._request_json("StockReachGraph/GetStockInfo", params={"scripcode": scripcode})

    def fetch_financial_results(self, scripcode: str) -> dict[str, Any]:
        return self._request_json("CorporateAction/GetFinancialResult", params={"scripcode": scripcode})

    def fetch_upcoming_board_meetings(
        self,
        scripcode: str,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"scripcode": scripcode}
        if from_date:
            params["fromdate"] = from_date
        if to_date:
            params["todate"] = to_date
        return self._request_json("CorporateAction/GetBoardMeetingData", params=params)

    @staticmethod
    def extract_stock_info_metrics(symbol: str, scripcode: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": symbol.upper(),
            "bse_scripcode": scripcode,
            "market_cap": first_float(payload, ("marketCap", "marketcapitalisation", "mcap")),
            "pe_ratio": first_float(payload, ("pe", "peRatio", "peratio")),
            "pb_ratio": first_float(payload, ("pb", "pbRatio", "priceToBook", "pbv")),
            "dividend_yield": first_float(payload, ("dividendYield", "dividendyield")),
            "week_52_high": first_float(payload, ("high52", "fiftytwoweekhigh", "yearHigh")),
            "week_52_low": first_float(payload, ("low52", "fiftytwoweeklow", "yearLow")),
        }

    @staticmethod
    def extract_financial_periods(symbol: str, scripcode: str, payload: dict[str, Any], *, period_type: str) -> list[dict[str, Any]]:
        periods: list[dict[str, Any]] = []
        seen_keys: set[tuple[Any, str]] = set()
        normalized_period_type = period_type.upper()
        for record in iter_dict_records(payload):
            record_keys = {normalize_key(str(key)) for key in record.keys()}
            if not record_keys.intersection({"netprofit", "netincome", "revenue", "sales", "eps", "resultdate", "announcementdate"}):
                continue
            period_end = first_date(record, ("periodEnd", "quarterEnding", "yearEnding", "date", "quarter"))
            earnings_date = first_date(record, ("earningsDate", "resultDate", "announcementDate"))
            unique_key = (period_end or earnings_date, normalized_period_type)
            if unique_key[0] is None or unique_key in seen_keys:
                continue
            seen_keys.add(unique_key)
            periods.append(
                {
                    "symbol": symbol.upper(),
                    "bse_scripcode": scripcode,
                    "period_type": normalized_period_type,
                    "period_end": period_end or earnings_date,
                    "fiscal_label": first_text(record, ("quarter", "period", "fiscalLabel", "resultType")),
                    "revenue": first_float(record, ("revenue", "sales", "income", "totalIncome")),
                    "net_profit": first_float(record, ("netProfit", "pat", "profitAfterTax", "netIncome")),
                    "operating_profit": first_float(record, ("operatingProfit", "ebitda", "operatingIncome")),
                    "ebit": first_float(record, ("ebit", "operatingProfit")),
                    "eps_basic": first_float(record, ("eps", "earningPerShare", "basicEps")),
                    "total_assets": first_float(record, ("totalAssets", "assets", "totalAsset", "totalasset")),
                    "npa_pct": first_float(record, ("grossNpa", "npa", "grossNpaPct")),
                    "capital_adequacy_pct": first_float(record, ("capitalAdequacy", "crar", "capitalAdequacyRatio")),
                    "earnings_date": earnings_date,
                    "raw_payload": record,
                }
            )
        periods.sort(key=lambda item: item["period_end"], reverse=True)
        return periods

    @staticmethod
    def extract_board_meeting_events(symbol: str, scripcode: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        seen_keys: set[tuple[Any, str]] = set()
        earnings_aliases = (
            "results",
            "financial result",
            "quarterly results",
            "annual results",
            "audited results",
            "unaudited results",
        )
        for record in iter_dict_records(payload):
            purpose = first_text(record, ("purpose", "meetingType", "subject", "agenda", "description", "notice"))
            if not purpose:
                continue
            normalized_purpose = purpose.lower()
            if not any(alias in normalized_purpose for alias in earnings_aliases):
                continue
            meeting_date = first_date(record, ("meetingDate", "date", "boardMeetingDate", "meetingDt", "bm_date"))
            if meeting_date is None:
                continue
            key = (meeting_date, normalized_purpose)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(
                {
                    "symbol": symbol.upper(),
                    "bse_scripcode": scripcode,
                    "meeting_date": meeting_date,
                    "purpose": purpose,
                    "action_type": "BOARD_MEETING_RESULTS",
                    "description": first_text(record, ("description", "remarks", "notice")) or purpose,
                    "raw_payload": record,
                }
            )
        events.sort(key=lambda item: item["meeting_date"])
        return events


_client: BSEClient | None = None
_client_lock = Lock()


def get_bse_client() -> BSEClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = BSEClient()
    return _client


__all__ = ["BSEClient", "get_bse_client"]
