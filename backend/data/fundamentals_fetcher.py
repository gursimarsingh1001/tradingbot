from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.config import get_settings
from backend.data.data_quality import sanitize_fundamental_snapshot
from backend.data.historical_fetcher import PREFERRED_BATCH_SYMBOLS, HistoricalFetcher, SymbolConfig
from backend.engine.fundamental_engine import infer_sector_label


settings = get_settings()


@dataclass(slots=True)
class SnapshotFetchResult:
    symbol: str
    payload: dict[str, Any] | None
    error: str | None = None


class StockAnalysisFundamentalsFetcher:
    RATIOS_URL = "https://stockanalysis.com/quote/nse/{symbol}/financials/ratios/"
    FINANCIALS_URL = "https://stockanalysis.com/quote/nse/{symbol}/financials/"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36"

    def __init__(self, timeout_seconds: int = 20) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    @staticmethod
    def _clean_number(value: str | None) -> float | None:
        if value is None:
            return None
        text = value.strip().replace(",", "")
        if not text or text in {"-", "--", "—", "n/a", "N/A"}:
            return None
        try:
            if text.endswith("%"):
                return float(text[:-1])
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _extract_table_rows(html: str) -> dict[str, list[str]]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if table is None:
            return {}
        rows: dict[str, list[str]] = {}
        for row in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            rows[cells[0]] = cells[1:]
        return rows

    @staticmethod
    def _extract_period_date(rows: dict[str, list[str]]) -> str | None:
        values = rows.get("Period Ending") or []
        if not values:
            return None
        candidate = values[0]
        match = re.search(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", candidate)
        if match:
            return date_parser.parse(match.group(0)).date().isoformat()
        try:
            return date_parser.parse(candidate, fuzzy=True).date().isoformat()
        except (ValueError, TypeError):
            return None

    def _fetch_rows(self, url: str) -> dict[str, list[str]]:
        response = self.session.get(url, timeout=self.timeout_seconds)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return self._extract_table_rows(response.text)

    def fetch_snapshot(self, symbol_config: SymbolConfig) -> SnapshotFetchResult:
        encoded_symbol = quote(symbol_config.symbol, safe="")
        ratios_url = self.RATIOS_URL.format(symbol=encoded_symbol)
        financials_url = self.FINANCIALS_URL.format(symbol=encoded_symbol)
        try:
            ratio_rows = self._fetch_rows(ratios_url)
            financial_rows = self._fetch_rows(financials_url)
            if not ratio_rows or not financial_rows:
                return SnapshotFetchResult(symbol=symbol_config.symbol, payload=None, error="missing_source_tables")
            as_of_date = self._extract_period_date(financial_rows) or self._extract_period_date(ratio_rows)
            if as_of_date is None:
                as_of_date = datetime.now(UTC).date().isoformat()
            sector = infer_sector_label(
                symbol_config.symbol,
                symbol_config.company_name,
                symbol_config.sector,
            )
            payload = {
                "symbol": symbol_config.symbol,
                "companyName": symbol_config.company_name,
                "sector": sector,
                "asOfDate": as_of_date,
                "revenueGrowthPct": self._clean_number((financial_rows.get("Revenue Growth (YoY)") or [None])[0]),
                "profitGrowthPct": self._clean_number((financial_rows.get("Net Income Growth") or [None])[0]),
                "roe": self._clean_number((ratio_rows.get("Return on Equity (ROE)") or [None])[0]),
                "roce": self._clean_number((ratio_rows.get("Return on Capital Employed (ROCE)") or [None])[0]),
                "debtToEquity": self._clean_number((ratio_rows.get("Debt / Equity Ratio") or [None])[0]),
                "currentRatio": self._clean_number((ratio_rows.get("Current Ratio") or [None])[0]),
                "operatingMargin": self._clean_number((financial_rows.get("Operating Margin") or [None])[0]),
                "netMargin": self._clean_number((financial_rows.get("Profit Margin") or [None])[0]),
                "promoterHolding": None,
                "pledgedPct": None,
                "peRatio": self._clean_number((ratio_rows.get("PE Ratio") or [None])[0]),
                "pbRatio": self._clean_number((ratio_rows.get("PB Ratio") or [None])[0]),
                "dividendYield": self._clean_number((ratio_rows.get("Dividend Yield") or [None])[0]),
                "source": "stockanalysis.com",
                "notes": f"Loaded from {financials_url} and {ratios_url}",
            }
            payload = sanitize_fundamental_snapshot(payload)
            if payload is None:
                return SnapshotFetchResult(symbol=symbol_config.symbol, payload=None, error="no_metrics_found")
            return SnapshotFetchResult(symbol=symbol_config.symbol, payload=payload)
        except Exception as exc:
            return SnapshotFetchResult(symbol=symbol_config.symbol, payload=None, error=str(exc))

    @staticmethod
    def _priority_order(symbols: list[SymbolConfig]) -> list[SymbolConfig]:
        preferred_priority = {symbol.upper(): index for index, symbol in enumerate(PREFERRED_BATCH_SYMBOLS)}
        return sorted(
            symbols,
            key=lambda config: (
                0 if config.symbol.upper() in preferred_priority else 1,
                preferred_priority.get(config.symbol.upper(), 9999),
                config.symbol,
            ),
        )

    @staticmethod
    def _merge_payloads(
        *,
        output_path: Path,
        symbol_payloads: dict[str, dict[str, Any]],
    ) -> None:
        existing_payload: list[dict[str, Any]] = []
        if output_path.exists():
            try:
                existing_payload = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing_payload = []
        snapshot_map = {
            str(item.get("symbol") or "").upper(): item
            for item in existing_payload
            if isinstance(item, dict) and item.get("symbol")
        }
        snapshot_map.update(symbol_payloads)
        merged = sorted(snapshot_map.values(), key=lambda item: str(item.get("symbol") or ""))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    def load_and_write_for_symbol_configs(
        self,
        symbol_configs: list[SymbolConfig],
        *,
        workers: int = 6,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        output_path = output_path or settings.fundamentals_config_path
        ordered_symbols = self._priority_order(symbol_configs)
        loaded = 0
        failed: dict[str, str] = {}
        payloads: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {executor.submit(self.fetch_snapshot, config): config.symbol for config in ordered_symbols}
            for future in as_completed(futures):
                result = future.result()
                if result.payload is not None:
                    payloads[result.symbol.upper()] = result.payload
                    loaded += 1
                elif result.error:
                    failed[result.symbol] = result.error

        self._merge_payloads(output_path=output_path, symbol_payloads=payloads)
        return {
            "loaded": loaded,
            "failed": len(failed),
            "total_requested": len(ordered_symbols),
            "output_path": str(output_path),
            "failed_examples": dict(list(failed.items())[:10]),
            "requested_symbols": [config.symbol for config in ordered_symbols],
        }

    def load_and_write(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        workers: int = 6,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        output_path = output_path or settings.fundamentals_config_path
        fetcher = HistoricalFetcher()
        symbols = self._priority_order(fetcher.load_symbols())
        if offset > 0:
            symbols = symbols[offset:]
        if limit is not None and limit > 0:
            symbols = symbols[:limit]
        return self.load_and_write_for_symbol_configs(symbols, workers=workers, output_path=output_path)
