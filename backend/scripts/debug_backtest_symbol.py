from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select

from backend.backtest.backtester import WalkForwardBacktester
from backend.data.historical_fetcher import HistoricalFetcher
from backend.db.postgres import BacktestTrade, session_scope


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug a single symbol through the backtest pipeline.")
    parser.add_argument("symbol", help="Symbol to debug")
    args = parser.parse_args()

    fetcher = HistoricalFetcher()
    backtester = WalkForwardBacktester()
    symbol_map = fetcher.load_symbol_map()
    symbol_config = symbol_map.get(args.symbol.upper())
    if symbol_config is None:
        raise SystemExit(json.dumps({"ok": False, "error": "symbol_not_found"}))

    frame = fetcher.fetch_symbol_frame(symbol_config)
    result: dict[str, object] = {
        "symbol": symbol_config.symbol,
        "bars": int(len(frame)),
        "minimumRequiredBars": backtester.minimum_required_bars,
        "windowCount": len(backtester.build_walk_forward_windows(frame)) if not frame.empty else 0,
    }

    if frame.empty:
        result["ok"] = False
        result["error"] = "empty_history"
        print(json.dumps(result, default=str))
        return

    if len(frame) < backtester.minimum_required_bars:
        result["ok"] = False
        result["error"] = "insufficient_history"
        print(json.dumps(result, default=str))
        return

    try:
        metrics = backtester.run_for_stock(symbol_config.symbol, frame)
        result["ok"] = True
        result["metrics"] = {
            strategy_name: {
                "totalReturn": metric.total_return,
                "sharpeRatio": metric.sharpe_ratio,
                "winRate": metric.win_rate,
                "maxDrawdown": metric.max_drawdown,
            }
            for strategy_name, metric in metrics.items()
        }
        with session_scope() as session:
            trade_count = session.scalar(
                select(func.count()).select_from(BacktestTrade).where(BacktestTrade.stock_symbol == symbol_config.symbol)
            )
        result["tradeCount"] = int(trade_count or 0)
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)

    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
