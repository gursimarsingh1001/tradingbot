from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from backend.config import get_settings
from backend.data.universe_filters import is_pure_nse_stock


DEFAULT_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"


def load_existing_metadata(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    metadata: dict[str, dict[str, Any]] = {}
    for item in payload:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        metadata[symbol] = {
            "companyName": item.get("companyName"),
            "sector": item.get("sector"),
        }
    return metadata


def load_master_records(*, source_url: str | None, source_file: Path | None) -> list[dict[str, Any]]:
    if source_file is not None:
        return json.loads(source_file.read_text(encoding="utf-8"))
    if not source_url:
        raise ValueError("A source URL or source file is required.")
    with urlopen(source_url) as response:
        return json.loads(response.read().decode("utf-8"))


def is_equity_record(record: dict[str, Any]) -> bool:
    symbol = str(record.get("symbol") or "").upper()
    name = str(record.get("name") or "").upper()
    if "-" not in symbol:
        return False
    _, series = symbol.rsplit("-", 1)
    return is_pure_nse_stock(
        symbol=name or symbol.rsplit("-", 1)[0],
        company_name=name or symbol.rsplit("-", 1)[0],
        trading_symbol=symbol,
        exchange=str(record.get("exch_seg") or "").upper(),
        series=series,
        instrument_type=str(record.get("instrumenttype") or ""),
    )


def build_universe(records: list[dict[str, Any]], existing_metadata: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}

    for record in records:
        if not is_equity_record(record):
            continue
        trading_symbol = str(record["symbol"])
        base_symbol, series = trading_symbol.rsplit("-", 1)
        key = str(record.get("name") or base_symbol).upper()
        current = selected.get(key)
        if current is not None:
            continue
        cached = existing_metadata.get(key, {})
        selected[key] = {
            "symbol": key,
            "token": str(record["token"]),
            "companyName": cached.get("companyName") or str(record.get("name") or base_symbol),
            "exchange": "NSE",
            "sector": cached.get("sector"),
            "tradingSymbol": trading_symbol,
            "series": series,
            "lotSize": int(float(record.get("lotsize") or 1)),
            "tickSize": float(record.get("tick_size") or 0.0),
        }

    universe = []
    for item in sorted(selected.values(), key=lambda row: row["symbol"]):
        universe.append(item)
    return universe


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Generate the full NSE equity universe from Angel One instrument master.")
    parser.add_argument("--source-url", default=DEFAULT_MASTER_URL, help="Instrument master URL")
    parser.add_argument("--source-file", type=Path, help="Optional local instrument master file")
    parser.add_argument(
        "--output",
        type=Path,
        default=settings.symbols_config_path,
        help="Output JSON path for the NSE universe",
    )
    args = parser.parse_args()

    records = load_master_records(source_url=args.source_url, source_file=args.source_file)
    existing_metadata = load_existing_metadata(args.output)
    universe = build_universe(records, existing_metadata)
    args.output.write_text(json.dumps(universe, indent=2), encoding="utf-8")
    print(
        {
            "output": str(args.output),
            "count": len(universe),
            "sample": universe[:5],
        }
    )


if __name__ == "__main__":
    main()
