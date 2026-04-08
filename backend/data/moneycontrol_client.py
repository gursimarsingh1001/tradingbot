from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from threading import Lock
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.config import get_settings
from backend.data.official_api_common import SimpleRateLimiter, parse_date


settings = get_settings()


@dataclass(slots=True)
class MoneycontrolEarningsEvent:
    symbol: str
    company_name: str | None
    earnings_date: date | None
    source_url: str | None
    raw_payload: dict[str, Any]


class MoneycontrolClient:
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.moneycontrol.com/",
    }

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        retries = Retry(
            total=2,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=4, pool_maxsize=4)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(self.DEFAULT_HEADERS)
        self._rate_limiter = SimpleRateLimiter(1.0)

    def _calendar_url(self, active_date: date) -> str:
        return f"https://www.moneycontrol.com/markets/earnings/results-calendar?activeDate={active_date.isoformat()}&id=All&name=All"

    def fetch_results_calendar_day(self, active_date: date) -> list[MoneycontrolEarningsEvent]:
        url = self._calendar_url(active_date)
        self._rate_limiter.wait()
        response = self.session.get(url, timeout=15)
        response.raise_for_status()
        return self.parse_results_calendar_html(response.text, active_date=active_date, source_url=url)

    def fetch_results_calendar_range(self, from_date: date, to_date: date) -> list[MoneycontrolEarningsEvent]:
        if to_date < from_date:
            return []
        cursor = from_date
        events: list[MoneycontrolEarningsEvent] = []
        while cursor <= to_date:
            events.extend(self.fetch_results_calendar_day(cursor))
            cursor += timedelta(days=1)
        return events

    def fetch_earnings_calendar(
        self,
        company_name: str,
        *,
        symbol: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> MoneycontrolEarningsEvent | None:
        from_date = from_date or date.today()
        to_date = to_date or (from_date + timedelta(days=7))
        symbol_key = self._normalize_match_key(symbol or "")
        company_key = self._normalize_match_key(company_name)
        for event in self.fetch_results_calendar_range(from_date, to_date):
            raw_symbol_key = self._normalize_match_key(event.raw_payload.get("scId"))
            raw_short_key = self._normalize_match_key(event.raw_payload.get("stockShortName"))
            raw_name_key = self._normalize_match_key(event.company_name)
            if symbol_key and symbol_key in {raw_symbol_key, raw_short_key}:
                return event
            if company_key and company_key == raw_name_key:
                return event
        return None

    @staticmethod
    def _extract_first_date(text: str) -> date | None:
        patterns = (
            r"\b\d{4}-\d{2}-\d{2}\b",
            r"\b\d{2}-\d{2}-\d{4}\b",
            r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b",
            r"\b[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            parsed = parse_date(match.group(0))
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _normalize_match_key(value: Any) -> str:
        text = str(value or "").upper()
        text = re.sub(r"\b(LIMITED|LTD)\b", "", text)
        return re.sub(r"[^A-Z0-9]+", "", text)

    @classmethod
    def parse_results_calendar_html(
        cls,
        html: str,
        *,
        active_date: date,
        source_url: str | None = None,
    ) -> list[MoneycontrolEarningsEvent]:
        soup = BeautifulSoup(html, "html.parser")
        node = soup.find("script", id="__NEXT_DATA__")
        if node is None or not node.string:
            return []
        try:
            payload = json.loads(node.string)
        except ValueError:
            return []
        records = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("resultCalendarData", {})
            .get("tableData", {})
            .get("list", [])
        )
        if not isinstance(records, list):
            return []
        events: list[MoneycontrolEarningsEvent] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            company_name = str(item.get("stockName") or "").strip() or None
            sc_id = str(item.get("scId") or item.get("stockShortName") or company_name or "").strip().upper()
            if not sc_id or company_name is None:
                continue
            event_date = active_date
            parsed = cls._extract_first_date(str(item.get("date") or ""))
            if parsed is not None:
                event_date = parsed.replace(year=active_date.year) if parsed.year != active_date.year else parsed
            events.append(
                MoneycontrolEarningsEvent(
                    symbol=sc_id,
                    company_name=company_name,
                    earnings_date=event_date,
                    source_url=source_url,
                    raw_payload=dict(item),
                )
            )
        return events


_client: MoneycontrolClient | None = None
_client_lock = Lock()


def get_moneycontrol_client() -> MoneycontrolClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MoneycontrolClient()
    return _client


__all__ = ["MoneycontrolClient", "MoneycontrolEarningsEvent", "get_moneycontrol_client"]
