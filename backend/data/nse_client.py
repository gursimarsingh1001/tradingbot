from __future__ import annotations

from datetime import date
from threading import Lock
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.config import get_settings
from backend.data.official_api_common import (
    SimpleRateLimiter,
    first_date,
    first_float,
    first_text,
    iter_dict_records,
    normalize_key,
)


settings = get_settings()


class NSEClient:
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    }

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(401, 403, 429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(self.DEFAULT_HEADERS)
        self._rate_limiter = SimpleRateLimiter(settings.official_nse_rate_limit_seconds)
        self._bootstrap_lock = Lock()
        self._bootstrapped = False

    def _bootstrap_session(self, *, force: bool = False) -> None:
        with self._bootstrap_lock:
            if self._bootstrapped and not force:
                return
            self._rate_limiter.wait()
            response = self.session.get(settings.official_nse_bootstrap_url, timeout=20)
            response.raise_for_status()
            self._bootstrapped = True

    def _request_json(self, endpoint: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._bootstrap_session()
        url = f"{settings.official_nse_api_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(2):
            self._rate_limiter.wait()
            response = self.session.get(url, params=params, timeout=20)
            if response.status_code in {401, 403} and attempt == 0:
                self._bootstrap_session(force=True)
                continue
            try:
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {"data": payload}
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    self._bootstrap_session(force=True)
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"NSE request failed for endpoint {endpoint}")

    def fetch_quote_equity(self, symbol: str) -> dict[str, Any]:
        return self._request_json("quote-equity", params={"symbol": symbol.upper()})

    def fetch_financial_results(self, symbol: str, *, period: str = "Quarterly") -> dict[str, Any]:
        return self._request_json(
            "corporates-financial-results",
            params={"index": "equities", "period": period, "symbol": symbol.upper()},
        )

    def fetch_shareholding(self, symbol: str) -> dict[str, Any]:
        return self._request_json(
            "corporates-shareholding",
            params={"index": "equities", "symbol": symbol.upper()},
        )

    def fetch_corporate_actions(self, symbol: str) -> dict[str, Any]:
        return self._request_json(
            "corporates-corporateActions",
            params={"index": "equities", "symbol": symbol.upper()},
        )

    def fetch_all_indices(self) -> dict[str, Any]:
        return self._request_json("allIndices")

    @staticmethod
    def extract_quote_metrics(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": symbol.upper(),
            "market_cap": first_float(payload, ("marketCap", "marketcap", "marketCapitalisation", "mcap")),
            "pe_ratio": first_float(payload, ("pe", "peRatio", "peratio", "p/e", "pdsectorpe")),
            "dividend_yield": first_float(payload, ("dividendYield", "divyield", "dividendyield")),
            "industry_pe": first_float(payload, ("industryPE", "industryPe", "pdsectorpe", "sectorPE")),
            "week_52_high": first_float(payload, ("high52", "weekHigh52", "yearHigh", "high52w")),
            "week_52_low": first_float(payload, ("low52", "weekLow52", "yearLow", "low52w")),
        }

    @staticmethod
    def extract_shareholding_snapshot(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        def _record_first(record: dict[str, Any], aliases: tuple[str, ...]) -> Any:
            alias_set = {normalize_key(alias) for alias in aliases}
            for key, value in record.items():
                if normalize_key(str(key)) in alias_set and value not in (None, ""):
                    return value
            return None

        def _record_text(record: dict[str, Any], aliases: tuple[str, ...]) -> str | None:
            value = _record_first(record, aliases)
            if value is None:
                return None
            text = str(value).strip()
            return text or None

        def _record_float(record: dict[str, Any], aliases: tuple[str, ...]) -> float | None:
            value = _record_first(record, aliases)
            if value is None:
                return None
            return first_float({aliases[0]: value}, (aliases[0],))

        def _record_date(record: dict[str, Any], aliases: tuple[str, ...]) -> date | None:
            value = _record_first(record, aliases)
            if value is None:
                return None
            return first_date({aliases[0]: value}, (aliases[0],))

        as_of_date = first_date(payload, ("asOfDate", "date", "quarterEnding", "reportDate"))
        promoter_holding = first_float(payload, ("promoterHolding", "promoterholding"))
        promoter_pledge = first_float(payload, ("promoterPledge", "promoterpledge", "pledgedPercent"))
        fii_holding = first_float(payload, ("fiiHolding", "foreignInstitutional", "fpiHolding", "foreignholding"))
        dii_holding = first_float(payload, ("diiHolding", "domesticInstitutional", "institutionalholding"))

        if any(value is not None for value in (promoter_holding, promoter_pledge, fii_holding, dii_holding)):
            return {
                "symbol": symbol.upper(),
                "as_of_date": as_of_date,
                "promoter_holding": promoter_holding,
                "promoter_pledge": promoter_pledge,
                "fii_holding": fii_holding,
                "dii_holding": dii_holding,
            }

        promoter_like: list[float] = []
        foreign_like: list[float] = []
        domestic_like: list[float] = []
        pledge_like: list[float] = []
        record_dates: list[date] = []
        for record in iter_dict_records(payload):
            category = (_record_text(record, ("category", "description", "shareholder", "name", "type")) or "").lower()
            value = _record_float(record, ("value", "holding", "holdingPercent", "percentage", "perc", "shareholding"))
            record_date = _record_date(record, ("asOfDate", "date", "quarterEnding", "reportDate"))
            if record_date is not None:
                record_dates.append(record_date)
            if not category or value is None:
                continue
            if "promoter" in category and "pledge" not in category:
                promoter_like.append(value)
            if "pledge" in category:
                pledge_like.append(value)
            if "fii" in category or "fpi" in category or "foreign" in category:
                foreign_like.append(value)
            if "dii" in category or "domestic" in category or "mutual" in category or "institution" in category:
                domestic_like.append(value)

        return {
            "symbol": symbol.upper(),
            "as_of_date": max(record_dates) if record_dates else as_of_date,
            "promoter_holding": sum(promoter_like) if promoter_like else None,
            "promoter_pledge": max(pledge_like) if pledge_like else None,
            "fii_holding": sum(foreign_like) if foreign_like else None,
            "dii_holding": sum(domestic_like) if domestic_like else None,
        }

    @staticmethod
    def extract_financial_periods(symbol: str, payload: dict[str, Any], *, period_type: str) -> list[dict[str, Any]]:
        periods: list[dict[str, Any]] = []
        seen_dates: set[date] = set()
        normalized_period_type = period_type.upper()
        for record in iter_dict_records(payload):
            period_end = first_date(record, ("periodEnd", "periodEnding", "endDate", "date", "quarterEnding", "fy"))
            if period_end is None or period_end in seen_dates:
                continue
            record_keys = {normalize_key(str(key)) for key in record.keys()}
            if not record_keys.intersection(
                {
                    "netprofit",
                    "netincome",
                    "totalincome",
                    "revenue",
                    "sales",
                    "eps",
                    "operatingprofit",
                    "operatingincome",
                }
            ):
                continue
            seen_dates.add(period_end)
            periods.append(
                {
                    "symbol": symbol.upper(),
                    "period_type": normalized_period_type,
                    "period_end": period_end,
                    "fiscal_label": first_text(record, ("quarter", "quarterName", "period", "fiscalLabel", "fyLabel")),
                    "revenue": first_float(record, ("revenue", "revenuefromoperations", "sales", "totalincome", "income")),
                    "net_profit": first_float(record, ("netProfit", "netprofit", "profitAfterTax", "pat", "netIncome")),
                    "operating_profit": first_float(record, ("operatingProfit", "ebitda", "operatingIncome")),
                    "ebit": first_float(record, ("ebit", "operatingProfit")),
                    "eps_basic": first_float(record, ("eps", "epsBasic", "earningpershare", "basicEps")),
                    "operating_cash_flow": first_float(record, ("operatingCashFlow", "cashFromOperations", "cfo")),
                    "total_debt": first_float(record, ("totalDebt", "borrowings", "debt")),
                    "shareholder_equity": first_float(record, ("shareholderEquity", "equity", "totalEquity", "netWorth")),
                    "capital_employed": first_float(record, ("capitalEmployed",)),
                    "current_assets": first_float(record, ("currentAssets",)),
                    "current_liabilities": first_float(record, ("currentLiabilities",)),
                    "gross_margin": first_float(record, ("grossMargin", "grossMarginPct")),
                    "asset_turnover": first_float(record, ("assetTurnover",)),
                    "roa": first_float(record, ("roa", "returnOnAssets")),
                    "shares_outstanding": first_float(record, ("sharesOutstanding", "numberOfShares", "shares")),
                    "earnings_date": first_date(record, ("earningsDate", "resultDate", "announcementDate")),
                    "raw_payload": record,
                }
            )
        periods.sort(key=lambda item: item["period_end"], reverse=True)
        return periods

    @staticmethod
    def extract_corporate_actions(symbol: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        seen: set[tuple[date, str]] = set()
        for record in iter_dict_records(payload):
            ex_date = first_date(record, ("exDate", "recordDate", "date"))
            action_type = first_text(record, ("purpose", "actionType", "type", "caType"))
            if ex_date is None or not action_type:
                continue
            key = (ex_date, action_type.upper())
            if key in seen:
                continue
            seen.add(key)
            actions.append(
                {
                    "symbol": symbol.upper(),
                    "ex_date": ex_date,
                    "action_type": action_type.upper(),
                    "description": first_text(record, ("description", "purpose", "remarks")),
                    "raw_payload": record,
                }
            )
        actions.sort(key=lambda item: item["ex_date"], reverse=True)
        return actions

    @staticmethod
    def extract_india_vix(payload: dict[str, Any]) -> float | None:
        for record in iter_dict_records(payload):
            name = (first_text(record, ("index", "indexSymbol", "name", "key")) or "").upper()
            if "VIX" not in name:
                continue
            value = first_float(record, ("last", "lastPrice", "ltp", "value", "indexValue"))
            if value is not None:
                return value
        return None


_client: NSEClient | None = None
_client_lock = Lock()


def get_nse_client() -> NSEClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = NSEClient()
    return _client


__all__ = ["NSEClient", "get_nse_client"]
