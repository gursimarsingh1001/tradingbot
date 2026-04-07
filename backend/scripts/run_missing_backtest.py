from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from multiprocessing import get_context
from typing import Any

from sqlalchemy import select

from backend.config import get_settings
from backend.backtest.backtester import (
    WalkForwardBacktester,
    _default_backtest_workers,
    _process_symbol_backtest,
    _set_progress,
)
from backend.backtest.strategy_selector import StrategySelector
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.postgres import StockStrategyMap, session_scope


def _load_existing_symbols() -> set[str]:
    with session_scope() as session:
        return {symbol for symbol in session.scalars(select(StockStrategyMap.symbol)).all()}


def _process_missing_symbol(symbol: str) -> dict[str, Any]:
    return _process_symbol_backtest(symbol)


def run_missing_backtest(*, limit: int | None = None, max_workers: int | None = None) -> dict[str, Any]:
    fetcher = HistoricalFetcher()
    selector = StrategySelector()
    existing_symbols = _load_existing_symbols()
    pending_symbols = [
        config
        for config in fetcher.load_symbols()
        if fetcher.is_backtest_candidate(config) and config.symbol not in existing_symbols
    ]

    if limit is not None:
        pending_symbols = pending_symbols[:limit]

    total = len(pending_symbols)
    completed = 0
    failures: list[dict[str, str]] = []
    skipped_insufficient: list[dict[str, str]] = []
    no_trade_symbols = 0
    worker_count = min(total, max_workers or _default_backtest_workers(total, None))
    selector_interval = max(4, min(max(worker_count * 4, 4), 48))
    selector_pending = 0

    if total == 0:
        _set_progress(active=False, progress=100, message="No missing symbols left to backtest.")
        return {"ok": True, "processed": 0, "failures": [], "summary": selector.run()}

    _set_progress(
        active=True,
        progress=0,
        message=f"Starting parallel backfill for {total} missing symbols using {worker_count} workers",
    )

    with ProcessPoolExecutor(max_workers=worker_count, mp_context=get_context("spawn")) as executor:
        future_to_symbol = {
            executor.submit(_process_missing_symbol, symbol_config.symbol): symbol_config.symbol
            for symbol_config in pending_symbols
        }

        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"symbol": symbol, "ok": False, "error": str(exc)}

            completed += 1
            if result["ok"]:
                if result.get("had_no_trades"):
                    no_trade_symbols += 1
                    message = (
                        f"Backfilled {completed} of {total}: {result['symbol']} "
                        f"(no valid trades)"
                    )
                else:
                    selector_pending += 1
                    if selector_pending >= selector_interval:
                        selector.run()
                        selector_pending = 0
                    message = f"Backfilled {completed} of {total}: {result['symbol']}"
            else:
                error = str(result["error"])
                bucket = skipped_insufficient if "Insufficient historical data" in error else failures
                bucket.append({"symbol": result["symbol"], "error": error})
                prefix = "Skipped" if bucket is skipped_insufficient else "Failed"
                message = f"{prefix} {result['symbol']}: {error}"

            _set_progress(
                active=True,
                progress=int((completed / total) * 100),
                message=message,
            )

    summary = selector.run()
    processed = total - len(failures) - len(skipped_insufficient)
    message = f"Backfill completed {processed} of {total} eligible symbols"
    if no_trade_symbols:
        message += f"; {no_trade_symbols} had no valid trades"
    if skipped_insufficient:
        message += f"; {len(skipped_insufficient)} skipped for insufficient history"
    if failures:
        message += f"; {len(failures)} failed"
    _set_progress(active=False, progress=100, message=message)
    return {
        "ok": True,
        "processed": processed,
        "skipped_insufficient": skipped_insufficient,
        "failures": failures,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest only the symbols missing from stock_strategy_map.")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on the number of missing symbols to process.")
    parser.add_argument("--workers", type=int, default=None, help="Optional worker override.")
    args = parser.parse_args()
    result = run_missing_backtest(limit=args.limit, max_workers=args.workers)
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
