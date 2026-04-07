from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from backend.backtest.backtester import WalkForwardBacktester, _set_progress
from backend.config import get_settings
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.postgres import StockStrategyMap, session_scope


def _load_existing_symbols() -> set[str]:
    with session_scope() as session:
        return {symbol for symbol in session.scalars(select(StockStrategyMap.symbol)).all()}


def warm_backtest_history_cache(*, limit: int | None = None, sleep_seconds: float = 2.0) -> dict[str, Any]:
    settings = get_settings()
    fetcher = HistoricalFetcher()
    backtester = WalkForwardBacktester()
    existing_symbols = _load_existing_symbols()

    pending_symbols = [
        config
        for config in fetcher.load_symbols()
        if fetcher.is_backtest_candidate(config) and config.symbol not in existing_symbols
    ]
    if limit is not None:
        pending_symbols = pending_symbols[:limit]

    total = len(pending_symbols)
    if total == 0:
        _set_progress(active=False, progress=100, message="No eligible symbols need history warm-up.")
        return {"ok": True, "processed": 0, "hydrated": 0, "already_cached": 0, "skipped": []}

    stop = datetime.now(tz=settings.tzinfo)
    start = stop - timedelta(days=3660)

    hydrated = 0
    already_cached = 0
    skipped: list[dict[str, str]] = []

    _set_progress(
        active=True,
        progress=0,
        message=f"Warming history cache for {total} eligible symbols",
    )

    for index, config in enumerate(pending_symbols, start=1):
        try:
            cached = fetcher.influx_store.query_symbol_history(config.symbol, start=start, stop=stop)
            if len(cached) >= backtester.minimum_required_bars:
                already_cached += 1
                _set_progress(
                    active=True,
                    progress=int((index / total) * 100),
                    message=f"History ready {index} of {total}: {config.symbol}",
                )
                continue

            frame = fetcher.fetch_symbol_frame(config)
            if frame.empty or len(frame) < backtester.minimum_required_bars:
                skipped.append(
                    {
                        "symbol": config.symbol,
                        "error": (
                            f"Insufficient historical data ({len(frame)} bars available, "
                            f"need at least {backtester.minimum_required_bars})"
                        ),
                    }
                )
                _set_progress(
                    active=True,
                    progress=int((index / total) * 100),
                    message=f"Skipped {config.symbol}: insufficient history",
                )
            else:
                hydrated += 1
                _set_progress(
                    active=True,
                    progress=int((index / total) * 100),
                    message=f"Hydrated {index} of {total}: {config.symbol}",
                )
            time.sleep(max(sleep_seconds, 0.0))
        except Exception as exc:
            skipped.append({"symbol": config.symbol, "error": str(exc)})
            _set_progress(
                active=True,
                progress=int((index / total) * 100),
                message=f"Warm-up failed {config.symbol}: {exc}",
            )
            time.sleep(max(sleep_seconds, 0.0))

    message = f"History warm-up completed for {total} eligible symbols"
    if hydrated:
        message += f"; {hydrated} hydrated"
    if already_cached:
        message += f"; {already_cached} already cached"
    if skipped:
        message += f"; {len(skipped)} skipped"
    _set_progress(active=False, progress=100, message=message)
    return {
        "ok": True,
        "processed": total,
        "hydrated": hydrated,
        "already_cached": already_cached,
        "skipped": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Warm the historical-data cache for eligible backtest symbols.")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on symbols to warm.")
    parser.add_argument("--sleep", type=float, default=2.0, help="Sleep between API fetches in seconds.")
    args = parser.parse_args()
    result = warm_backtest_history_cache(limit=args.limit, sleep_seconds=args.sleep)
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
