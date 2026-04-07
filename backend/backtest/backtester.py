from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from math import isfinite
from threading import Lock, Thread
from datetime import timedelta
from multiprocessing import get_context
import argparse
import os
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select

from backend.config import get_settings
from backend.backtest.overfitting_detector import detect_overfitting
from backend.backtest.strategy_selector import StrategySelector
from backend.data.historical_fetcher import HistoricalFetcher
from backend.data.indicator_calculator import IndicatorCalculator
from backend.db.postgres import BacktestTrade, StockStrategyMap, get_config_value, session_scope, upsert_config_value
from backend.engine.position_sizer import calculate_size
from backend.engine.regime_detector import detect_regime
from backend.strategies.bb_squeeze import BollingerBandSqueezeStrategy
from backend.strategies.breakout_volume import BreakoutVolumeStrategy
from backend.strategies.ema_crossover import EMACrossoverStrategy
from backend.strategies.golden_cross import GoldenCrossStrategy
from backend.strategies.macd_momentum import MACDMomentumStrategy
from backend.strategies.news_driven import NewsDrivenMomentumStrategy
from backend.strategies.regime_aware_combined import RegimeAwareCombinedStrategy
from backend.strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from backend.strategies.base_strategy import BaseStrategy, StrategyContext

try:
    import vectorbt as vbt
except Exception:  # pragma: no cover
    vbt = None


settings = get_settings()
_backtest_lock = Lock()
_backtest_thread: Thread | None = None


@dataclass
class BacktestMetrics:
    total_return: float
    sharpe_ratio: float
    win_rate: float
    max_drawdown: float


class WalkForwardBacktester:
    DESIRED_TRAIN_BARS = 504
    DESIRED_TEST_BARS = 126
    DESIRED_STEP_BARS = 126
    MIN_TRAIN_BARS = 90
    MIN_TEST_BARS = 20
    MIN_TOTAL_BARS = MIN_TRAIN_BARS + MIN_TEST_BARS

    def __init__(self) -> None:
        self.strategies: dict[str, BaseStrategy] = {
            "EMA Crossover": EMACrossoverStrategy(),
            "Golden Cross": GoldenCrossStrategy(),
            "RSI Mean Reversion": RSIMeanReversionStrategy(),
            "MACD Momentum": MACDMomentumStrategy(),
            "Bollinger Band Squeeze": BollingerBandSqueezeStrategy(),
            "Breakout with Volume": BreakoutVolumeStrategy(),
            "News-Driven Momentum": NewsDrivenMomentumStrategy(),
            "Combined Regime-Aware": RegimeAwareCombinedStrategy(),
        }

    @property
    def minimum_required_bars(self) -> int:
        return self.MIN_TOTAL_BARS

    def build_walk_forward_windows(self, df: pd.DataFrame) -> list[tuple[int, int, int, int]]:
        total_bars = len(df)
        if total_bars < self.minimum_required_bars:
            return []

        train_bars = min(self.DESIRED_TRAIN_BARS, max(self.MIN_TRAIN_BARS, int(total_bars * 0.75)))
        test_bars = min(self.DESIRED_TEST_BARS, max(self.MIN_TEST_BARS, int(total_bars * 0.2)))
        remaining_bars = total_bars - train_bars

        if remaining_bars < self.MIN_TEST_BARS:
            train_bars = total_bars - self.MIN_TEST_BARS
            test_bars = self.MIN_TEST_BARS
        elif remaining_bars < test_bars:
            test_bars = remaining_bars

        if train_bars < self.MIN_TRAIN_BARS or test_bars < self.MIN_TEST_BARS:
            return []

        step_bars = min(self.DESIRED_STEP_BARS, max(self.MIN_TEST_BARS, test_bars))
        windows: list[tuple[int, int, int, int]] = []
        cursor = train_bars

        while cursor + test_bars <= total_bars:
            train_start = cursor - train_bars
            train_end = cursor
            test_start = cursor
            test_end = cursor + test_bars
            windows.append((train_start, train_end, test_start, test_end))
            cursor += step_bars

        if not windows and total_bars >= train_bars + test_bars:
            windows.append((0, train_bars, train_bars, total_bars))

        return windows

    def _simulate_window(
        self,
        symbol: str,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        *,
        params: dict[str, Any],
        news_scores: pd.Series | None = None,
    ) -> list[dict[str, Any]]:
        trades: list[dict[str, Any]] = []
        open_position: dict[str, Any] | None = None

        for index in range(1, len(df)):
            signal_date = df.index[index - 1]
            trade_date = df.index[index]
            history = df.iloc[:index]
            if len(history) < 2:
                continue
            context = StrategyContext(
                news_score=float(news_scores.loc[signal_date]) if news_scores is not None and signal_date in news_scores.index else 0.0,
                regime=detect_regime(history),
                signal_type=strategy.signal_type,
                timeframe="DAILY",
            )
            try:
                signal = strategy.generate_signal(history, signal_date, context=context, params=params)
            except (KeyError, TypeError, ValueError):
                signal = {
                    "signal": "HOLD",
                    "entry_price": None,
                    "stop_loss": None,
                    "target_1": None,
                    "target_2": None,
                    "target_3": None,
                }
            current_row = df.iloc[index]

            if open_position is None and signal["signal"] in {"BUY", "SELL"}:
                direction = signal["signal"]
                entry_price = float(current_row["Open"] * (1.001 if direction == "BUY" else 0.999))
                signal_type = str(signal.get("signal_type") or strategy.signal_type or "INTRADAY").upper()
                shares = self._position_shares(
                    entry_price=entry_price,
                    stop_loss=float(signal["stop_loss"]),
                    signal_type=signal_type,
                    regime=context.regime,
                )
                if shares <= 0:
                    continue
                open_position = {
                    "direction": direction,
                    "entry_date": trade_date,
                    "entry_price": entry_price,
                    "stop_loss": signal["stop_loss"],
                    "target_3": signal["target_3"],
                    "shares": shares,
                    "signal_type": signal_type,
                    "strategy_name": strategy.name,
                    "news_score_at_entry": context.news_score,
                    "regime_at_entry": context.regime,
                    "pattern_at_entry": signal.get("pattern_name"),
                }
                continue

            if open_position is None:
                continue

            direction = open_position["direction"]
            should_close = False
            exit_reason = "SIGNAL_EXIT"
            exit_price = float(current_row["Close"])

            if direction == "BUY":
                if current_row["Low"] <= open_position["stop_loss"]:
                    should_close = True
                    exit_reason = "STOP_HIT"
                    exit_price = float(open_position["stop_loss"])
                elif current_row["High"] >= open_position["target_3"]:
                    should_close = True
                    exit_reason = "TARGET_3"
                    exit_price = float(open_position["target_3"])
                elif signal["signal"] == "SELL":
                    should_close = True
                    exit_price = float(current_row["Open"] * 0.999)
            else:
                if current_row["High"] >= open_position["stop_loss"]:
                    should_close = True
                    exit_reason = "STOP_HIT"
                    exit_price = float(open_position["stop_loss"])
                elif current_row["Low"] <= open_position["target_3"]:
                    should_close = True
                    exit_reason = "TARGET_3"
                    exit_price = float(open_position["target_3"])
                elif signal["signal"] == "BUY":
                    should_close = True
                    exit_price = float(current_row["Open"] * 1.001)

            if not should_close:
                continue

            entry_price = open_position["entry_price"]
            shares = int(open_position["shares"])
            gross_pnl = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
            gross_pnl_rupees = gross_pnl * shares
            total_cost_rupees = self._transaction_costs(
                entry_price,
                exit_price,
                shares,
                direction=direction,
                signal_type=open_position["signal_type"],
            )
            pnl_rupees = gross_pnl_rupees - total_cost_rupees
            notional = entry_price * shares
            net_pnl_pct = (pnl_rupees / notional) if notional else 0.0
            trades.append(
                {
                    "stock_symbol": symbol,
                    "strategy_name": open_position["strategy_name"],
                    "entry_date": open_position["entry_date"].date(),
                    "exit_date": trade_date.date(),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "shares": shares,
                    "pnl_rupees": pnl_rupees,
                    "pnl_pct": net_pnl_pct * 100,
                    "news_score_at_entry": open_position["news_score_at_entry"],
                    "regime_at_entry": open_position["regime_at_entry"],
                    "pattern_at_entry": open_position["pattern_at_entry"],
                    "quarter_at_entry": f"Q{((open_position['entry_date'].month - 1) // 3) + 1}",
                    "holding_days": (trade_date - open_position["entry_date"]).days,
                    "exit_reason": exit_reason,
                }
            )
            open_position = None
        return trades

    def _score_trade_list(self, trades: list[dict[str, Any]]) -> float:
        if not trades:
            return float("-inf")
        returns = pd.Series([trade["pnl_pct"] / 100 for trade in trades])
        return float(returns.mean())

    def _compute_metrics(self, trades: list[dict[str, Any]]) -> BacktestMetrics:
        if not trades:
            return BacktestMetrics(total_return=0.0, sharpe_ratio=0.0, win_rate=0.0, max_drawdown=0.0)
        returns = pd.Series([trade["pnl_pct"] / 100 for trade in trades], dtype=float)
        equity = (1 + returns).cumprod()
        total_return = float(equity.iloc[-1] - 1)
        win_rate = float((returns > 0).mean())
        running_max = equity.cummax()
        max_drawdown = float(((equity - running_max) / running_max).min())
        if vbt is not None:
            sharpe_ratio = float(returns.vbt.returns(freq="d").sharpe_ratio())
        else:
            sharpe_ratio = float((returns.mean() / returns.std()) * (252 ** 0.5)) if returns.std() else 0.0
        return BacktestMetrics(
            total_return=total_return,
            sharpe_ratio=sharpe_ratio,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
        )

    @staticmethod
    def _stt_rate(signal_type: str) -> float:
        return settings.backtest_intraday_stt_rate if signal_type == "INTRADAY" else settings.backtest_delivery_stt_rate

    @staticmethod
    def _stamp_duty_rate(signal_type: str) -> float:
        return (
            settings.backtest_intraday_stamp_duty_rate
            if signal_type == "INTRADAY"
            else settings.backtest_delivery_stamp_duty_rate
        )

    @classmethod
    def _transaction_costs(
        cls,
        entry_price: float,
        exit_price: float,
        shares: int,
        *,
        direction: str,
        signal_type: str,
    ) -> float:
        turnover_entry = entry_price * shares
        turnover_exit = exit_price * shares
        brokerage = settings.backtest_brokerage_per_order * 2
        turnover_total = turnover_entry + turnover_exit
        exchange_charge = turnover_total * settings.backtest_exchange_charge_rate
        sebi_charge = turnover_total * settings.backtest_sebi_charge_rate
        gst = (brokerage + exchange_charge + sebi_charge) * settings.backtest_gst_rate
        sell_turnover = turnover_exit if direction == "BUY" else turnover_entry
        buy_turnover = turnover_entry if direction == "BUY" else turnover_exit
        stt = sell_turnover * cls._stt_rate(signal_type)
        stamp_duty = buy_turnover * cls._stamp_duty_rate(signal_type)
        return brokerage + exchange_charge + sebi_charge + gst + stt + stamp_duty

    @staticmethod
    def _portfolio_value_for_signal_type(signal_type: str) -> float:
        allocation_pct = settings.paper_investment_allocation_pct if signal_type == "INVESTMENT" else settings.paper_intraday_allocation_pct
        return max(settings.paper_portfolio_value * allocation_pct, 1.0)

    @staticmethod
    def _leverage_multiplier_for_signal_type(signal_type: str) -> float:
        return 5.0 if signal_type == "INTRADAY" else 1.0

    @classmethod
    def _position_shares(cls, *, entry_price: float, stop_loss: float, signal_type: str, regime: str | None = None) -> int:
        atr = abs(entry_price - stop_loss) / 2 or (entry_price * 0.01)
        confidence = float(settings.default_recommendation_confidence)
        if not isfinite(confidence):
            confidence = 70.0
        return calculate_size(
            confidence=confidence,
            atr=atr,
            portfolio_value=cls._portfolio_value_for_signal_type(signal_type),
            entry_price=entry_price,
            regime=regime,
            leverage_multiplier=cls._leverage_multiplier_for_signal_type(signal_type),
        )

    def run_for_stock(self, symbol: str, df: pd.DataFrame, news_scores: pd.Series | None = None) -> dict[str, BacktestMetrics]:
        windows = self.build_walk_forward_windows(df)
        if not windows:
            raise RuntimeError(
                f"Insufficient historical data for adaptive walk-forward ({len(df)} bars available, "
                f"need at least {self.minimum_required_bars})"
            )
        metrics: dict[str, BacktestMetrics] = {}
        with session_scope() as session:
            session.execute(delete(BacktestTrade).where(BacktestTrade.stock_symbol == symbol))

        for strategy_name, strategy in self.strategies.items():
            all_test_trades: list[dict[str, Any]] = []
            for train_start, train_end, test_start, test_end in windows:
                train_df = df.iloc[train_start:train_end]
                test_df = df.iloc[test_start:test_end]
                if len(train_df) < 50 or len(test_df) < 10:
                    continue

                best_params = strategy.parameter_grid()[0]
                best_score = float("-inf")
                best_train_score = float("-inf")
                for params in strategy.parameter_grid():
                    candidate_trades = self._simulate_window(symbol, strategy, train_df, params=params, news_scores=news_scores)
                    score = self._score_trade_list(candidate_trades)
                    if score > best_score:
                        best_score = score
                        best_params = params
                        best_train_score = score

                window_test_trades = self._simulate_window(symbol, strategy, test_df, params=best_params, news_scores=news_scores)
                if not window_test_trades:
                    continue
                test_score = self._score_trade_list(window_test_trades)
                if detect_overfitting(best_train_score, test_score):
                    continue
                all_test_trades.extend(window_test_trades)

            metrics[strategy_name] = self._compute_metrics(all_test_trades)
            with session_scope() as session:
                for trade in all_test_trades:
                    session.add(BacktestTrade(**trade))
        return metrics


def _set_progress(*, active: bool, progress: int, message: str) -> None:
    with session_scope() as session:
        upsert_config_value(
            session,
            "backtest_progress",
            {
                "active": active,
                "progress": progress,
                "message": message,
            },
        )


def _count_symbol_trades(symbol: str) -> int:
    with session_scope() as session:
        count = session.scalar(
            select(func.count()).select_from(BacktestTrade).where(BacktestTrade.stock_symbol == symbol)
        )
    return int(count or 0)


def _upsert_no_trade_symbol(symbol: str) -> None:
    with session_scope() as session:
        record = session.get(StockStrategyMap, symbol) or StockStrategyMap(symbol=symbol)
        record.best_strategy = None
        record.sharpe_ratio = 0.0
        record.win_rate = 0.0
        record.max_drawdown = 0.0
        record.total_return = 0.0
        record.composite_score = None
        record.regime_performed_best = None
        record.avg_holding_days = 0
        record.sentiment_direction_best = None
        record.best_quarter = None
        record.last_updated = pd.Timestamp.now(tz=settings.tzinfo).to_pydatetime()
        session.merge(record)


def _process_symbol_backtest(symbol: str) -> dict[str, Any]:
    fetcher = HistoricalFetcher()
    backtester = WalkForwardBacktester()
    symbol_map = fetcher.load_symbol_map()
    symbol_config = symbol_map.get(symbol.upper())
    if symbol_config is None:
        return {"symbol": symbol, "ok": False, "error": "Symbol not found in config"}

    try:
        stop = pd.Timestamp.now(tz=settings.tzinfo).to_pydatetime()
        start = stop - timedelta(days=3660)
        frame = fetcher.influx_store.query_symbol_history(symbol_config.symbol, start=start, stop=stop)
        if not frame.empty:
            frame = IndicatorCalculator.enrich(frame)
        else:
            frame = fetcher.fetch_symbol_frame(symbol_config)

        if frame.empty or len(frame) < backtester.minimum_required_bars:
            raise RuntimeError(
                f"Insufficient historical data ({len(frame)} bars available, "
                f"need at least {backtester.minimum_required_bars})"
            )

        backtester.run_for_stock(symbol_config.symbol, frame)
        trade_count = _count_symbol_trades(symbol_config.symbol)
        had_no_trades = trade_count == 0
        if had_no_trades:
            _upsert_no_trade_symbol(symbol_config.symbol)
        return {
            "symbol": symbol_config.symbol,
            "ok": True,
            "had_no_trades": had_no_trades,
            "trade_count": trade_count,
        }
    except Exception as exc:
        return {"symbol": symbol_config.symbol, "ok": False, "error": str(exc)}


def run_selected_backtest_batch(*, limit: int | None = 50) -> dict[str, Any]:
    fetcher = HistoricalFetcher()
    selector = StrategySelector()
    backtester = WalkForwardBacktester()
    symbols = fetcher.select_symbols(limit=limit)
    total = len(symbols)
    completed = 0
    failures: list[dict[str, str]] = []
    no_trade_symbols = 0

    if total == 0:
        _set_progress(active=False, progress=0, message="No symbols available for backtest.")
        return {"ok": False, "processed": 0, "failures": [{"symbol": "ALL", "error": "No symbols available"}]}

    with session_scope() as session:
        session.execute(delete(BacktestTrade))
        session.execute(delete(StockStrategyMap))
        upsert_config_value(session, "global_best_strategy", {"name": None, "composite_score": None})
        upsert_config_value(
            session,
            "backtest_progress",
            {
                "active": True,
                "progress": 0,
                "message": f"Starting {'full-NSE' if limit is None else limit}-stock batch for {total} symbols",
            },
        )

    for symbol_config in symbols:
        try:
            frame = fetcher.fetch_symbol_frame(symbol_config)
            if frame.empty or len(frame) < backtester.minimum_required_bars:
                raise RuntimeError(
                    f"Insufficient historical data ({len(frame)} bars available, "
                    f"need at least {backtester.minimum_required_bars})"
                )
            backtester.run_for_stock(symbol_config.symbol, frame)
            if _count_symbol_trades(symbol_config.symbol) == 0:
                _upsert_no_trade_symbol(symbol_config.symbol)
                no_trade_symbols += 1
            completed += 1
            _set_progress(
                active=True,
                progress=int((completed / total) * 100),
                message=f"Backtested {completed} of {total}: {symbol_config.symbol}",
            )
        except Exception as exc:
            failures.append({"symbol": symbol_config.symbol, "error": str(exc)})
            completed += 1
            _set_progress(
                active=True,
                progress=int((completed / total) * 100),
                message=f"Skipped {symbol_config.symbol}: {exc}",
            )

    summary = selector.run()
    message = f"Completed {total - len(failures)} of {total} stocks"
    if no_trade_symbols:
        message += f"; {no_trade_symbols} had no valid trades"
    if failures:
        message += f"; {len(failures)} failed"
    _set_progress(active=False, progress=100, message=message)
    return {"ok": True, "processed": total - len(failures), "failures": failures, "summary": summary}


def _default_backtest_workers(total: int, limit: int | None) -> int:
    cpu_count = max(os.cpu_count() or 1, 1)
    target_workers = max(1, int(cpu_count * settings.backtest_target_worker_cpu_fraction))
    if cpu_count > 1:
        target_workers = min(target_workers, cpu_count - 1)
    if limit is None or total > 1000:
        return min(total, max(4, min(target_workers, 10)))
    return min(total, target_workers)


def run_selected_backtest_batch_parallel(*, limit: int | None = 50, max_workers: int | None = None) -> dict[str, Any]:
    fetcher = HistoricalFetcher()
    selector = StrategySelector()
    symbols = fetcher.select_symbols(limit=limit)
    total = len(symbols)
    completed = 0
    failures: list[dict[str, str]] = []
    no_trade_symbols = 0
    worker_count = min(total, max_workers or _default_backtest_workers(total, limit))
    selector_interval = max(4, min(max(worker_count * 4, 4), 48))
    selector_pending = 0

    if total == 0:
        _set_progress(active=False, progress=0, message="No symbols available for backtest.")
        return {"ok": False, "processed": 0, "failures": [{"symbol": "ALL", "error": "No symbols available"}]}

    with session_scope() as session:
        session.execute(delete(BacktestTrade))
        session.execute(delete(StockStrategyMap))
        upsert_config_value(session, "global_best_strategy", {"name": None, "composite_score": None})
        upsert_config_value(
            session,
            "backtest_progress",
            {
                "active": True,
                "progress": 0,
                "message": (
                    f"Starting parallel {'full-NSE' if limit is None else limit}-stock batch "
                    f"for {total} symbols using {worker_count} workers"
                ),
            },
        )

    with ProcessPoolExecutor(max_workers=worker_count, mp_context=get_context("spawn")) as executor:
        future_to_symbol = {
            executor.submit(_process_symbol_backtest, symbol_config.symbol): symbol_config.symbol
            for symbol_config in symbols
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
                    message = f"Backtested {completed} of {total}: {result['symbol']} (no valid trades)"
                else:
                    selector_pending += 1
                    if selector_pending >= selector_interval:
                        selector.run()
                        selector_pending = 0
                    message = f"Backtested {completed} of {total}: {result['symbol']}"
            else:
                failures.append({"symbol": result["symbol"], "error": result["error"]})
                message = f"Skipped {result['symbol']}: {result['error']}"

            _set_progress(
                active=True,
                progress=int((completed / total) * 100),
                message=message,
            )

    summary = selector.run()
    message = f"Completed {total - len(failures)} of {total} stocks"
    if no_trade_symbols:
        message += f"; {no_trade_symbols} had no valid trades"
    if failures:
        message += f"; {len(failures)} failed"
    _set_progress(active=False, progress=100, message=message)
    return {"ok": True, "processed": total - len(failures), "failures": failures, "summary": summary}


def start_backtest_thread(*, limit: int | None = 50, max_workers: int | None = None) -> bool:
    global _backtest_thread

    def _runner() -> None:
        try:
            run_selected_backtest_batch_parallel(limit=limit, max_workers=max_workers)
        except Exception as exc:
            _set_progress(active=False, progress=0, message=f"Backtest failed: {exc}")
            raise

    with _backtest_lock:
        if _backtest_thread is not None and _backtest_thread.is_alive():
            return False
        _backtest_thread = Thread(target=_runner, daemon=True)
        _backtest_thread.start()
        return True


def resume_saved_backtest_if_needed() -> bool:
    global _backtest_thread

    with session_scope() as session:
        progress = get_config_value(session, "backtest_progress", {"active": False})
    if not isinstance(progress, dict) or not progress.get("active"):
        return False

    def _runner() -> None:
        try:
            from backend.scripts.run_missing_backtest import run_missing_backtest

            run_missing_backtest()
        except Exception as exc:
            _set_progress(active=False, progress=0, message=f"Backtest resume failed: {exc}")
            raise

    with _backtest_lock:
        if _backtest_thread is not None and _backtest_thread.is_alive():
            return False
        _backtest_thread = Thread(target=_runner, daemon=True)
        _backtest_thread.start()
        return True


def run_full_backtest_task(payload: dict[str, Any]) -> dict[str, Any]:
    raw_limit = payload.get("limit", 50)
    limit = int(raw_limit) if raw_limit not in (None, 0, "0") else None
    return run_selected_backtest_batch(limit=limit)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the walk-forward backtester.")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Number of symbols to backtest. Use 0 for the full eligible universe.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Override the parallel worker count used for the backtest.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    limit = None if args.limit in (None, 0) else args.limit
    result = run_selected_backtest_batch_parallel(limit=limit, max_workers=args.max_workers)
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
