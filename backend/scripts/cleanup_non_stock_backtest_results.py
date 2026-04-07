from __future__ import annotations

import argparse
import json

from sqlalchemy import delete, select

from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.postgres import BacktestTrade, StockStrategyMap, session_scope


def discover_non_stock_symbols() -> tuple[list[str], list[str]]:
    fetcher = HistoricalFetcher()
    symbols = fetcher.load_symbols()
    pure_stock_symbols = {
        config.symbol.upper()
        for config in symbols
        if fetcher.is_backtest_candidate(config)
    }
    all_symbols = {config.symbol.upper() for config in symbols}
    rejected_symbols = sorted(all_symbols - pure_stock_symbols)
    return sorted(pure_stock_symbols), rejected_symbols


def cleanup_backtest_results(*, apply_changes: bool) -> dict[str, object]:
    _, rejected_symbols = discover_non_stock_symbols()
    if not rejected_symbols:
        return {
            "ok": True,
            "rejectedUniverseSymbols": [],
            "backtestTradesRemoved": 0,
            "strategyMapRowsRemoved": 0,
            "applied": apply_changes,
        }

    with session_scope() as session:
        mapped_symbols = set(session.scalars(select(StockStrategyMap.symbol)).all())
        touched_symbols = sorted(symbol for symbol in rejected_symbols if symbol in mapped_symbols)

        backtest_trade_count = session.query(BacktestTrade).filter(BacktestTrade.stock_symbol.in_(rejected_symbols)).count()
        strategy_map_count = session.query(StockStrategyMap).filter(StockStrategyMap.symbol.in_(rejected_symbols)).count()

        if apply_changes:
            session.execute(delete(BacktestTrade).where(BacktestTrade.stock_symbol.in_(rejected_symbols)))
            session.execute(delete(StockStrategyMap).where(StockStrategyMap.symbol.in_(rejected_symbols)))

    return {
        "ok": True,
        "rejectedUniverseSymbols": rejected_symbols,
        "mappedRejectedSymbols": touched_symbols,
        "backtestTradesRemoved": backtest_trade_count,
        "strategyMapRowsRemoved": strategy_map_count,
        "applied": apply_changes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report or remove ETF/non-stock symbols from backtest tables based on the pure-stock universe rules."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete non-stock rows from backtest_trades and stock_strategy_map. Default is dry-run only.",
    )
    args = parser.parse_args()
    result = cleanup_backtest_results(apply_changes=args.apply)
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
