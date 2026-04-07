from __future__ import annotations

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

    def _request_json(self, endpoint: str, *, params: dict[str, Any]) -> dict[str, Any]:
        self._rate_limiter.wait()
        url = f"{settings.official_bse_api_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        response = self.session.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    def fetch_stock_info(self, scripcode: str) -> dict[str, Any]:
        return self._request_json("StockReachGraph/GetStockInfo", params={"scripcode": scripcode})

    def fetch_financial_results(self, scripcode: str) -> dict[str, Any]:
        return self._request_json("CorporateAction/GetFinancialResult", params={"scripcode": scripcode})

    @staticmethod
    def extract_stock_info_metrics(symbol: str, scripcode: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": symbol.upper(),
            "bse_scripcode": scripcode,
            "market_cap": first_float(payload, ("marketCap", "marketcapitalisation", "mcap")),
            "pe_ratio": first_float(payload, ("pe", "peRatio", "peratio")),
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
                    "npa_pct": first_float(record, ("grossNpa", "npa", "grossNpaPct")),
                    "capital_adequacy_pct": first_float(record, ("capitalAdequacy", "crar", "capitalAdequacyRatio")),
                    "earnings_date": earnings_date,
                    "raw_payload": record,
                }
            )
        periods.sort(key=lambda item: item["period_end"], reverse=True)
        return periods


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
