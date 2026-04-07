from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from backend.backtest.backtester import WalkForwardBacktester
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.postgres import StockStrategyMap, session_scope
from backend.config import get_settings


def main() -> None:
    settings = get_settings()
    fetcher = HistoricalFetcher()
    backtester = WalkForwardBacktester()
    stop = datetime.now(tz=settings.tzinfo)
    start = stop - timedelta(days=3660)

    with session_scope() as session:
        completed_symbols = {symbol for symbol in session.scalars(select(StockStrategyMap.symbol)).all()}

    missing = [config for config in fetcher.load_symbols() if config.symbol not in completed_symbols]
    reasons: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = {}

    for symbol_config in missing:
        try:
            frame = fetcher.influx_store.query_symbol_history(symbol_config.symbol, start=start, stop=stop)
            if frame.empty:
                key = "no_cached_history"
                payload = {"symbol": symbol_config.symbol, "bars": 0}
            elif len(frame) < backtester.minimum_required_bars:
                key = "insufficient_cached_history"
                payload = {"symbol": symbol_config.symbol, "bars": int(len(frame))}
            elif not backtester.build_walk_forward_windows(frame):
                key = "no_cached_walk_forward_windows"
                payload = {"symbol": symbol_config.symbol, "bars": int(len(frame))}
            else:
                key = "cached_history_available_but_missing_strategy_map"
                payload = {"symbol": symbol_config.symbol, "bars": int(len(frame))}
            reasons[key] += 1
            samples.setdefault(key, [])
            if len(samples[key]) < 10:
                samples[key].append(payload)
        except Exception as exc:
            key = f"exception:{type(exc).__name__}"
            reasons[key] += 1
            samples.setdefault(key, [])
            if len(samples[key]) < 10:
                samples[key].append({"symbol": symbol_config.symbol, "error": str(exc)})

    print(
        json.dumps(
            {
                "missingCount": len(missing),
                "minimumRequiredBars": backtester.minimum_required_bars,
                "reasons": dict(reasons),
                "samples": samples,
            },
            default=str,
        )
    )


if __name__ == "__main__":
    main()
