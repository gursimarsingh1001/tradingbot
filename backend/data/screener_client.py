from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.config import get_settings
from backend.data.official_api_common import SimpleRateLimiter, coerce_float, normalize_key, parse_date


settings = get_settings()


@dataclass(slots=True)
class ScreenerCompanyData:
    symbol: str
    company_name: str | None = None
    screener_slug: str | None = None
    source_url: str | None = None
    fetched_at: str | None = None
    top_ratios: dict[str, float | None] = field(default_factory=dict)
    quarterly_ttm: dict[str, float | None] = field(default_factory=dict)
    annual_latest: dict[str, float | None] = field(default_factory=dict)
    annual_previous: dict[str, float | None] = field(default_factory=dict)
    balance_sheet: dict[str, float | None] = field(default_factory=dict)
    cash_flow: dict[str, float | None] = field(default_factory=dict)
    ratios: dict[str, float | None] = field(default_factory=dict)
    shareholding_latest: dict[str, float | None] = field(default_factory=dict)
    shareholding_previous: dict[str, float | None] = field(default_factory=dict)
    computed: dict[str, float | None] = field(default_factory=dict)
    raw_sections: dict[str, Any] = field(default_factory=dict)

    def to_cache_payload(self) -> dict[str, Any]:
        return asdict(self)

    def to_flat_dict(self) -> dict[str, Any]:
        latest_revenue = self.annual_latest.get("revenue")
        previous_revenue = self.annual_previous.get("revenue")
        latest_profit = self.annual_latest.get("net_profit")
        previous_profit = self.annual_previous.get("net_profit")
        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "screener_slug": self.screener_slug,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "market_cap": self.top_ratios.get("market_cap"),
            "pe_ratio": self.top_ratios.get("pe_ratio"),
            "pb_ratio": self.top_ratios.get("pb_ratio"),
            "book_value": self.top_ratios.get("book_value"),
            "dividend_yield": self.top_ratios.get("dividend_yield"),
            "roe": self.top_ratios.get("roe"),
            "roce": self.top_ratios.get("roce"),
            "face_value": self.top_ratios.get("face_value"),
            "revenue_ttm": self.quarterly_ttm.get("revenue_ttm"),
            "net_profit_ttm": self.quarterly_ttm.get("net_profit_ttm"),
            "eps_ttm": self.quarterly_ttm.get("eps_ttm"),
            "operating_profit_ttm": self.quarterly_ttm.get("operating_profit_ttm"),
            "operating_margin_ttm": self.quarterly_ttm.get("operating_margin_ttm"),
            "latest_annual_revenue": latest_revenue,
            "previous_annual_revenue": previous_revenue,
            "latest_annual_net_profit": latest_profit,
            "previous_annual_net_profit": previous_profit,
            "latest_annual_operating_margin": self.annual_latest.get("operating_margin"),
            "previous_annual_operating_margin": self.annual_previous.get("operating_margin"),
            "total_assets": self.balance_sheet.get("total_assets"),
            "total_debt": self.balance_sheet.get("total_debt"),
            "share_capital": self.balance_sheet.get("share_capital"),
            "reserves": self.balance_sheet.get("reserves"),
            "current_assets": self.balance_sheet.get("current_assets"),
            "current_liabilities": self.balance_sheet.get("current_liabilities"),
            "operating_cash_flow": self.cash_flow.get("operating_cash_flow"),
            "debt_equity": self.ratios.get("debt_equity"),
            "current_ratio": self.ratios.get("current_ratio"),
            "interest_coverage": self.ratios.get("interest_coverage"),
            "promoter_holding": self.shareholding_latest.get("promoter_holding"),
            "promoter_holding_previous": self.shareholding_previous.get("promoter_holding"),
            "fii_holding": self.shareholding_latest.get("fii_holding"),
            "fii_holding_previous": self.shareholding_previous.get("fii_holding"),
            "dii_holding": self.shareholding_latest.get("dii_holding"),
            "dii_holding_previous": self.shareholding_previous.get("dii_holding"),
            "promoter_holding_change_pct": self.computed.get("promoter_holding_change_pct"),
            "asset_turnover": self.computed.get("asset_turnover"),
            "roa": self.computed.get("roa"),
            "gross_margin": self.computed.get("gross_margin"),
            "shares_outstanding": self.computed.get("shares_outstanding"),
            "eps_growth_3y_cagr": self.computed.get("eps_growth_3y_cagr"),
        }


class ScreenerClient:
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.screener.in/",
    }
    SECTION_ID_ALIASES = {
        "profit_loss": ("profit-loss",),
        "balance_sheet": ("balance-sheet",),
        "cash_flow": ("cash-flow",),
        "shareholding": ("shareholding",),
        "ratios": ("ratios",),
    }

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        retries = Retry(
            total=2,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=5, pool_maxsize=5)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(self.DEFAULT_HEADERS)
        self._rate_limiter = SimpleRateLimiter(1.0 / max(settings.screener_rate_limit_per_second, 0.001))
        self._random = random.Random(42)

    @staticmethod
    def _load_overrides(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if isinstance(payload, dict):
            return {str(key).upper(): str(value).strip() for key, value in payload.items() if str(value).strip()}
        if isinstance(payload, list):
            overrides: dict[str, str] = {}
            for item in payload:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").strip().upper()
                slug = str(item.get("slug") or item.get("screenerSlug") or "").strip()
                if symbol and slug:
                    overrides[symbol] = slug
            return overrides
        return {}

    def _slug_for_symbol(self, symbol: str) -> str:
        overrides = self._load_overrides(Path(settings.screener_symbol_override_path))
        override = overrides.get(symbol.upper())
        if override:
            return override
        return quote_plus(symbol.upper())

    def _candidate_urls(self, symbol: str) -> list[str]:
        slug = self._slug_for_symbol(symbol)
        base = "https://www.screener.in/company"
        return [
            f"{base}/{slug}/consolidated/",
            f"{base}/{slug}/",
        ]

    def _request_html(self, url: str) -> str:
        self._rate_limiter.wait()
        time.sleep(self._random.uniform(0.0, 0.2))
        response = self.session.get(url, timeout=settings.screener_timeout_seconds, allow_redirects=True)
        if response.status_code == 404:
            raise FileNotFoundError(url)
        response.raise_for_status()
        return response.text

    def fetch_company_data(self, symbol: str) -> ScreenerCompanyData:
        last_error: Exception | None = None
        for url in self._candidate_urls(symbol):
            try:
                html = self._request_html(url)
                return self.parse_company_page(symbol=symbol, html=html, source_url=url)
            except FileNotFoundError as exc:
                last_error = exc
                continue
            except Exception as exc:  # pragma: no cover - network failure pass-through
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Screener fetch failed for {symbol}")

    @staticmethod
    def _clean_label(value: str) -> str:
        return normalize_key(value.replace("+", " ").replace("%", " percent "))

    @classmethod
    def _label_matches(cls, label: str, aliases: tuple[str, ...]) -> bool:
        cleaned = cls._clean_label(label)
        return any(cleaned == cls._clean_label(alias) or cls._clean_label(alias) in cleaned for alias in aliases)

    @staticmethod
    def _text(node) -> str:
        return node.get_text(" ", strip=True) if node is not None else ""

    @classmethod
    def _table_matrix(cls, table) -> tuple[list[str], dict[str, list[str]]]:
        headers: list[str] = []
        rows: dict[str, list[str]] = {}
        if table is None:
            return headers, rows
        header_row = table.find("thead")
        if header_row is not None:
            headers = [cls._text(cell) for cell in header_row.find_all(["th", "td"])]
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = cls._text(cells[0])
            if not label or cls._clean_label(label) in {"", "particulars"}:
                continue
            values = [cls._text(cell) for cell in cells[1:]]
            rows[label] = values
            if not headers:
                headers = ["label"] + [f"col_{index}" for index in range(1, len(cells))]
        return headers, rows

    @classmethod
    def _find_section_table(cls, soup: BeautifulSoup, aliases: tuple[str, ...]):
        normalized_aliases = tuple(cls._clean_label(alias) for alias in aliases)
        for node in soup.find_all(["section", "div", "h2", "h3", "h4"]):
            text = cls._clean_label(cls._text(node))
            if not any(alias in text for alias in normalized_aliases):
                continue
            if node.name == "section":
                table = node.find("table")
                if table is not None:
                    return table
                table = node.find_next("table")
                if table is not None:
                    return table
        return None

    @classmethod
    def _find_section_table_by_id_or_alias(
        cls,
        soup: BeautifulSoup,
        *,
        section_ids: tuple[str, ...] = (),
        aliases: tuple[str, ...] = (),
    ):
        for section_id in section_ids:
            section = soup.find("section", id=section_id)
            if section is None:
                continue
            table = section.find("table")
            if table is not None:
                return table
        return cls._find_section_table(soup, aliases)

    @staticmethod
    def _parse_numeric_series(values: list[str]) -> list[float | None]:
        return [coerce_float(value) for value in values]

    @classmethod
    def _value_for_row(cls, rows: dict[str, list[str]], aliases: tuple[str, ...], *, index: int = 0) -> float | None:
        for label, values in rows.items():
            if not cls._label_matches(label, aliases):
                continue
            parsed = cls._parse_numeric_series(values)
            if index < len(parsed):
                return parsed[index]
        return None

    @classmethod
    def _ttm_value(cls, headers: list[str], values: list[str]) -> float | None:
        parsed = cls._parse_numeric_series(values)
        for index, header in enumerate(headers[1:]):
            if "ttm" in header.lower() and index < len(parsed):
                return parsed[index]
        available = [value for value in parsed[:4] if value is not None]
        if len(available) == 4:
            return sum(available)
        return None

    @classmethod
    def _row_ttm(cls, headers: list[str], rows: dict[str, list[str]], aliases: tuple[str, ...]) -> float | None:
        for label, values in rows.items():
            if cls._label_matches(label, aliases):
                return cls._ttm_value(headers, values)
        return None

    @classmethod
    def _row_series(cls, rows: dict[str, list[str]], aliases: tuple[str, ...]) -> list[float | None]:
        for label, values in rows.items():
            if cls._label_matches(label, aliases):
                return cls._parse_numeric_series(values)
        return []

    @staticmethod
    def _cagr(current: float | None, base: float | None, years: int) -> float | None:
        if current is None or base in (None, 0) or years <= 0:
            return None
        if current <= 0 or base <= 0:
            return None
        return (((current / base) ** (1 / years)) - 1) * 100.0

    @classmethod
    def _extract_top_ratios(cls, soup: BeautifulSoup) -> dict[str, float | None]:
        metrics: dict[str, float | None] = {
            "market_cap": None,
            "pe_ratio": None,
            "pb_ratio": None,
            "book_value": None,
            "dividend_yield": None,
            "roe": None,
            "roce": None,
            "face_value": None,
        }
        ratio_items = soup.select("#top-ratios li, ul#top-ratios li, div#top-ratios li, li.flex.flex-space-between")
        for item in ratio_items:
            label_node = item.select_one(".name") or item.find(["span", "small"])
            value_candidates = item.find_all(["span", "div"])
            value_node = item.select_one(".number") or (value_candidates[-1] if value_candidates else None)
            label = cls._text(label_node)
            value = coerce_float(cls._text(value_node))
            if value is None or not label:
                continue
            if cls._label_matches(label, ("market cap", "marketcap")):
                metrics["market_cap"] = value
            elif cls._label_matches(label, ("stock pe", "pe", "price to earning")):
                metrics["pe_ratio"] = value
            elif cls._label_matches(label, ("price to book value", "pb", "p b", "pbv")):
                metrics["pb_ratio"] = value
            elif cls._label_matches(label, ("book value",)):
                metrics["book_value"] = value
            elif cls._label_matches(label, ("dividend yield",)):
                metrics["dividend_yield"] = value
            elif cls._label_matches(label, ("roe", "return on equity")):
                metrics["roe"] = value
            elif cls._label_matches(label, ("roce", "return on capital employed")):
                metrics["roce"] = value
            elif cls._label_matches(label, ("face value",)):
                metrics["face_value"] = value
        return metrics

    @classmethod
    def _extract_quarterly_ttm(cls, soup: BeautifulSoup) -> tuple[dict[str, float | None], dict[str, Any]]:
        table = cls._find_section_table(soup, ("quarterly results", "quarterly"))
        headers, rows = cls._table_matrix(table)
        metrics = {
            "revenue_ttm": cls._row_ttm(headers, rows, ("sales", "revenue", "sales plus", "revenue from operations")),
            "net_profit_ttm": cls._row_ttm(headers, rows, ("net profit", "net profit plus", "profit after tax")),
            "eps_ttm": cls._row_ttm(headers, rows, ("eps in rs", "eps")),
            "operating_profit_ttm": cls._row_ttm(headers, rows, ("operating profit", "op profit", "ebitda")),
            "operating_margin_ttm": None,
        }
        if metrics["operating_profit_ttm"] is not None and metrics["revenue_ttm"] not in (None, 0):
            metrics["operating_margin_ttm"] = (metrics["operating_profit_ttm"] / metrics["revenue_ttm"]) * 100.0
        return metrics, {"headers": headers, "rows": rows}

    @classmethod
    def _extract_annual_metrics(cls, soup: BeautifulSoup) -> tuple[dict[str, float | None], dict[str, float | None], dict[str, Any]]:
        table = cls._find_section_table_by_id_or_alias(
            soup,
            section_ids=cls.SECTION_ID_ALIASES["profit_loss"],
            aliases=("profit loss", "profit and loss", "p&l"),
        )
        headers, rows = cls._table_matrix(table)
        eps_series = cls._row_series(rows, ("eps in rs", "eps"))
        latest = {
            "revenue": cls._value_for_row(rows, ("sales", "revenue", "sales plus"), index=0),
            "net_profit": cls._value_for_row(rows, ("net profit", "net profit plus", "profit after tax"), index=0),
            "operating_profit": cls._value_for_row(rows, ("operating profit", "op profit", "ebitda"), index=0),
            "operating_margin": cls._value_for_row(rows, ("opm percent", "operating margin", "opm"), index=0),
            "eps_basic": cls._value_for_row(rows, ("eps in rs", "eps"), index=0),
            "eps_growth_3y_cagr": cls._cagr(eps_series[0] if len(eps_series) > 0 else None, eps_series[3] if len(eps_series) > 3 else None, 3),
        }
        previous = {
            "revenue": cls._value_for_row(rows, ("sales", "revenue", "sales plus"), index=1),
            "net_profit": cls._value_for_row(rows, ("net profit", "net profit plus", "profit after tax"), index=1),
            "operating_profit": cls._value_for_row(rows, ("operating profit", "op profit", "ebitda"), index=1),
            "operating_margin": cls._value_for_row(rows, ("opm percent", "operating margin", "opm"), index=1),
            "eps_basic": cls._value_for_row(rows, ("eps in rs", "eps"), index=1),
        }
        return latest, previous, {"headers": headers, "rows": rows}

    @classmethod
    def _extract_balance_sheet(cls, soup: BeautifulSoup) -> tuple[dict[str, float | None], dict[str, Any]]:
        table = cls._find_section_table_by_id_or_alias(
            soup,
            section_ids=cls.SECTION_ID_ALIASES["balance_sheet"],
            aliases=("balance sheet",),
        )
        headers, rows = cls._table_matrix(table)
        metrics = {
            "total_assets": cls._value_for_row(rows, ("total assets", "assets"), index=0),
            "total_debt": cls._value_for_row(rows, ("borrowings", "borrowing", "total debt", "debt"), index=0),
            "share_capital": cls._value_for_row(rows, ("equity capital", "share capital"), index=0),
            "reserves": cls._value_for_row(rows, ("reserves", "reserves surplus"), index=0),
            "current_assets": cls._value_for_row(rows, ("current assets",), index=0),
            "current_liabilities": cls._value_for_row(rows, ("current liabilities",), index=0),
        }
        return metrics, {"headers": headers, "rows": rows}

    @classmethod
    def _extract_cash_flow(cls, soup: BeautifulSoup) -> tuple[dict[str, float | None], dict[str, Any]]:
        table = cls._find_section_table_by_id_or_alias(
            soup,
            section_ids=cls.SECTION_ID_ALIASES["cash_flow"],
            aliases=("cash flow",),
        )
        headers, rows = cls._table_matrix(table)
        metrics = {
            "operating_cash_flow": cls._value_for_row(
                rows,
                ("cash from operating activity", "cash from operations", "operating cash flow"),
                index=0,
            ),
        }
        return metrics, {"headers": headers, "rows": rows}

    @classmethod
    def _extract_ratio_metrics(cls, soup: BeautifulSoup) -> tuple[dict[str, float | None], dict[str, Any]]:
        table = cls._find_section_table_by_id_or_alias(
            soup,
            section_ids=cls.SECTION_ID_ALIASES["ratios"],
            aliases=("ratios",),
        )
        headers, rows = cls._table_matrix(table)
        metrics = {
            "debt_equity": cls._value_for_row(rows, ("debt to equity", "debt equity"), index=0),
            "current_ratio": cls._value_for_row(rows, ("current ratio",), index=0),
            "interest_coverage": cls._value_for_row(rows, ("interest coverage", "interest coverage ratio"), index=0),
            "book_value": cls._value_for_row(rows, ("book value", "book value rs"), index=0),
            "roe": cls._value_for_row(rows, ("return on equity percent", "roe"), index=0),
            "roce": cls._value_for_row(rows, ("return on capital employed percent", "roce"), index=0),
            "pb_ratio": cls._value_for_row(rows, ("price to book value", "pbv", "pb"), index=0),
        }
        return metrics, {"headers": headers, "rows": rows}

    @classmethod
    def _extract_shareholding(cls, soup: BeautifulSoup) -> tuple[dict[str, float | None], dict[str, float | None], dict[str, Any]]:
        table = cls._find_section_table_by_id_or_alias(
            soup,
            section_ids=cls.SECTION_ID_ALIASES["shareholding"],
            aliases=("shareholding pattern", "shareholding"),
        )
        headers, rows = cls._table_matrix(table)
        latest = {
            "promoter_holding": cls._value_for_row(rows, ("promoters", "promoter"), index=0),
            "fii_holding": cls._value_for_row(rows, ("fiis", "fii", "fpis", "fii/fpi"), index=0),
            "dii_holding": cls._value_for_row(rows, ("diis", "dii", "mutual funds"), index=0),
        }
        previous = {
            "promoter_holding": cls._value_for_row(rows, ("promoters", "promoter"), index=1),
            "fii_holding": cls._value_for_row(rows, ("fiis", "fii", "fpis", "fii/fpi"), index=1),
            "dii_holding": cls._value_for_row(rows, ("diis", "dii", "mutual funds"), index=1),
        }
        return latest, previous, {"headers": headers, "rows": rows}

    @staticmethod
    def _compute_metrics(
        *,
        top_ratios: dict[str, float | None],
        quarterly_ttm: dict[str, float | None],
        annual_latest: dict[str, float | None],
        annual_previous: dict[str, float | None],
        balance_sheet: dict[str, float | None],
        shareholding_latest: dict[str, float | None],
        shareholding_previous: dict[str, float | None],
    ) -> dict[str, float | None]:
        total_assets = balance_sheet.get("total_assets")
        latest_profit = annual_latest.get("net_profit") or quarterly_ttm.get("net_profit_ttm")
        latest_revenue = annual_latest.get("revenue") or quarterly_ttm.get("revenue_ttm")
        gross_margin_value = annual_latest.get("operating_margin")
        share_capital = balance_sheet.get("share_capital")
        face_value = top_ratios.get("face_value")
        shares_outstanding = None
        if share_capital not in (None, 0) and face_value not in (None, 0):
            shares_outstanding = (share_capital * 10_000_000.0) / face_value
        return {
            "asset_turnover": None if latest_revenue in (None, 0) or total_assets in (None, 0) else latest_revenue / total_assets,
            "roa": None if latest_profit in (None, 0) or total_assets in (None, 0) else latest_profit / total_assets,
            "gross_margin": None if gross_margin_value is None else gross_margin_value / 100.0,
            "shares_outstanding": shares_outstanding,
            "eps_growth_3y_cagr": annual_latest.get("eps_growth_3y_cagr"),
            "promoter_holding_change_pct": None
            if shareholding_latest.get("promoter_holding") is None or shareholding_previous.get("promoter_holding") is None
            else shareholding_latest["promoter_holding"] - shareholding_previous["promoter_holding"],
        }

    @classmethod
    def parse_company_page(
        cls,
        *,
        symbol: str,
        html: str,
        source_url: str | None = None,
        fetched_at: datetime | None = None,
    ) -> ScreenerCompanyData:
        soup = BeautifulSoup(html, "html.parser")
        title = soup.find("h1")
        company_name = cls._text(title) or symbol.upper()
        top_ratios = cls._extract_top_ratios(soup)
        ratio_metrics, ratio_raw = cls._extract_ratio_metrics(soup)
        quarterly_ttm, quarterly_raw = cls._extract_quarterly_ttm(soup)
        annual_latest, annual_previous, annual_raw = cls._extract_annual_metrics(soup)
        balance_sheet, balance_raw = cls._extract_balance_sheet(soup)
        cash_flow, cash_raw = cls._extract_cash_flow(soup)
        shareholding_latest, shareholding_previous, shareholding_raw = cls._extract_shareholding(soup)
        screener_slug = None
        if source_url:
            path_parts = [part for part in urlparse(source_url).path.split("/") if part]
            if path_parts:
                screener_slug = path_parts[-2] if path_parts[-1].lower() == "consolidated" and len(path_parts) >= 2 else path_parts[-1]
        merged_top_ratios = dict(top_ratios)
        for key in ("pb_ratio", "book_value", "roe", "roce"):
            if merged_top_ratios.get(key) is None and ratio_metrics.get(key) is not None:
                merged_top_ratios[key] = ratio_metrics.get(key)
        computed = cls._compute_metrics(
            top_ratios=merged_top_ratios,
            quarterly_ttm=quarterly_ttm,
            annual_latest=annual_latest,
            annual_previous=annual_previous,
            balance_sheet=balance_sheet,
            shareholding_latest=shareholding_latest,
            shareholding_previous=shareholding_previous,
        )
        return ScreenerCompanyData(
            symbol=symbol.upper(),
            company_name=company_name,
            screener_slug=screener_slug,
            source_url=source_url,
            fetched_at=(fetched_at or datetime.utcnow()).isoformat(),
            top_ratios=merged_top_ratios,
            quarterly_ttm=quarterly_ttm,
            annual_latest=annual_latest,
            annual_previous=annual_previous,
            balance_sheet=balance_sheet,
            cash_flow=cash_flow,
            ratios=ratio_metrics,
            shareholding_latest=shareholding_latest,
            shareholding_previous=shareholding_previous,
            computed=computed,
            raw_sections={
                "quarterly_results": quarterly_raw,
                "annual_profit_loss": annual_raw,
                "balance_sheet": balance_raw,
                "cash_flow": cash_raw,
                "ratios": ratio_raw,
                "shareholding": shareholding_raw,
            },
        )


_client: ScreenerClient | None = None
_client_lock = Lock()


def get_screener_client() -> ScreenerClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = ScreenerClient()
    return _client


__all__ = ["ScreenerClient", "ScreenerCompanyData", "get_screener_client"]
