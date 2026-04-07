from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.config import get_settings
from backend.data.angel_one_client import get_angel_one_client


def load_symbol_config(symbol: str) -> dict:
    settings = get_settings()
    path = Path(settings.symbols_config_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload:
        if item["symbol"].upper() == symbol.upper():
            return item
    raise ValueError(f"Symbol {symbol} not found in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test Angel One authentication and candle fetch.")
    parser.add_argument("--symbol", default="RELIANCE", help="NSE symbol present in nifty500_symbols.json")
    parser.add_argument("--days", type=int, default=30, help="How many recent calendar days to fetch")
    args = parser.parse_args()

    settings = get_settings()
    symbol_config = load_symbol_config(args.symbol)
    client = get_angel_one_client()

    session = client.authenticate()
    to_date = datetime.now(tz=settings.tzinfo)
    from_date = to_date - timedelta(days=args.days)
    candles = client.get_historical_candles(
        symbol_config["token"],
        exchange=symbol_config.get("exchange", "NSE"),
        interval="ONE_DAY",
        from_date=from_date,
        to_date=to_date,
    )

    response = {
        "authenticated": True,
        "symbol": symbol_config["symbol"],
        "token": symbol_config["token"],
        "rows": int(len(candles)),
        "from": candles.index.min().isoformat() if not candles.empty else None,
        "to": candles.index.max().isoformat() if not candles.empty else None,
        "last_close": float(candles["Close"].iloc[-1]) if not candles.empty else None,
        "feed_token_received": bool(session.feed_token),
    }
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()
