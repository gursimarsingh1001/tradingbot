from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any


MANUAL_NAME_ALIASES: dict[str, str] = {
    "CDSL": "ICDSLTD",
}


def normalize_symbol_key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def load_nifty500_isin_map(csv_path: Path) -> dict[str, str]:
    if not csv_path.exists():
        return {}
    isin_by_symbol: dict[str, str] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = str(row.get("Symbol") or "").strip().upper()
            isin = str(row.get("ISIN Code") or "").strip().upper()
            if symbol and isin:
                isin_by_symbol[symbol] = isin
    return isin_by_symbol


def load_openapi_bse_rows(master_path: Path) -> list[dict[str, Any]]:
    if not master_path.exists():
        return []
    payload = json.loads(master_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get("exch_seg") or "").upper() != "BSE":
            continue
        instrument_type = str(item.get("instrumenttype") or "").strip().upper()
        if instrument_type == "AMXIDX":
            continue
        token = str(item.get("token") or "").strip()
        if not token:
            continue
        rows.append(item)
    return rows


def build_bse_symbol_mappings(
    symbols_payload: list[dict[str, Any]],
    bse_rows: list[dict[str, Any]],
    isin_by_symbol: dict[str, str] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    isin_by_symbol = isin_by_symbol or {}
    by_exact_name = {
        str(row.get("name") or "").upper(): row
        for row in bse_rows
        if row.get("name")
    }
    by_exact_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in bse_rows
        if row.get("symbol")
    }
    by_norm_name = {
        normalize_symbol_key(row.get("name")): row
        for row in bse_rows
        if row.get("name")
    }
    by_norm_symbol = {
        normalize_symbol_key(row.get("symbol")): row
        for row in bse_rows
        if row.get("symbol")
    }

    mappings: list[dict[str, str]] = []
    unresolved: list[str] = []
    for item in symbols_payload:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        trading_symbol = (
            str(item.get("tradingSymbol") or "")
            .strip()
            .upper()
            .replace("-EQ", "")
            .replace("-BE", "")
            .replace("-BZ", "")
            .replace("-SM", "")
        )
        alias = MANUAL_NAME_ALIASES.get(symbol)
        candidates = (
            by_exact_name.get(symbol),
            by_exact_symbol.get(symbol),
            by_exact_name.get(trading_symbol),
            by_exact_symbol.get(trading_symbol),
            by_exact_name.get(alias) if alias else None,
            by_exact_symbol.get(alias) if alias else None,
            by_norm_name.get(normalize_symbol_key(symbol)),
            by_norm_symbol.get(normalize_symbol_key(symbol)),
            by_norm_name.get(normalize_symbol_key(trading_symbol)),
            by_norm_symbol.get(normalize_symbol_key(trading_symbol)),
            by_norm_name.get(normalize_symbol_key(alias)) if alias else None,
            by_norm_symbol.get(normalize_symbol_key(alias)) if alias else None,
        )
        row = next((candidate for candidate in candidates if candidate), None)
        if row is None:
            unresolved.append(symbol)
            continue
        mappings.append(
            {
                "symbol": symbol,
                "bseScripcode": str(row.get("token") or "").strip(),
                "isin": isin_by_symbol.get(symbol, ""),
                "canonicalExchange": "BSE",
            }
        )

    mappings.sort(key=lambda item: item["symbol"])
    unresolved.sort()
    return mappings, unresolved

