from __future__ import annotations

import math
from datetime import datetime

import pandas as pd
from sqlalchemy import select

from backend.config import get_settings
from backend.db.postgres import BacktestTrade, StockStrategyMap, session_scope, upsert_config_value


class StrategySelector:
    MIN_MEANINGFUL_TRADES = 5
    MAX_RETURN_FOR_SCORING = 2.0
    MIN_MEANINGFUL_TRADES_BY_STRATEGY = {"Golden Cross": 3}
    DISABLED_STRATEGIES = {"Support and Resistance", "Supertrend"}
    _settings = get_settings()

    @staticmethod
    def _profit_factor(returns: pd.Series) -> float:
        gross_profit = float(returns[returns > 0].sum())
        gross_loss = float(abs(returns[returns < 0].sum()))
        if gross_loss == 0:
            return 3.0 if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @staticmethod
    def _payoff_ratio(returns: pd.Series) -> float:
        avg_win = float(returns[returns > 0].mean() or 0.0)
        avg_loss = float(abs(returns[returns < 0].mean() or 0.0))
        if avg_loss == 0:
            return 3.0 if avg_win > 0 else 0.0
        return avg_win / avg_loss

    @staticmethod
    def _finite_or_default(value: float, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default
        return numeric if math.isfinite(numeric) else default

    @classmethod
    def _clamp_unit(cls, value: float) -> float:
        numeric = cls._finite_or_default(value)
        return max(0.0, min(1.0, numeric))

    @classmethod
    def _sanitize_returns(cls, returns: pd.Series) -> pd.Series:
        numeric = pd.to_numeric(returns, errors="coerce")
        if numeric.empty:
            return pd.Series(dtype=float)
        numeric = numeric[numeric.notna()]
        numeric = numeric[numeric.map(math.isfinite)]
        return numeric.astype(float)

    @classmethod
    def _min_trades_for_strategy(cls, strategy_name: str) -> int:
        return int(cls.MIN_MEANINGFUL_TRADES_BY_STRATEGY.get(strategy_name, cls.MIN_MEANINGFUL_TRADES))

    @classmethod
    def _composite_score(
        cls,
        *,
        total_return: float,
        sharpe: float,
        max_drawdown: float,
        returns: pd.Series,
        trade_count: int,
    ) -> tuple[float, float, float, float]:
        profit_factor = cls._profit_factor(returns)
        payoff_ratio = cls._payoff_ratio(returns)
        expectancy = cls._finite_or_default(returns.mean() or 0.0)
        bounded_total_return = max(
            -cls.MAX_RETURN_FOR_SCORING,
            min(cls.MAX_RETURN_FOR_SCORING, cls._finite_or_default(total_return)),
        )
        sharpe_norm = cls._clamp_unit(cls._finite_or_default(sharpe) / 3.0)
        return_norm = cls._clamp_unit(bounded_total_return / 0.5)
        drawdown_norm = cls._clamp_unit(1.0 - abs(cls._finite_or_default(max_drawdown)))
        win_rate_norm = cls._clamp_unit(float((returns > 0).mean()))
        trade_confidence = cls._clamp_unit(trade_count / 20.0)

        composite_score = (
            0.30 * sharpe_norm
            + 0.25 * win_rate_norm
            + 0.20 * drawdown_norm
            + 0.15 * return_norm
            + 0.10 * trade_confidence
        )
        return cls._clamp_unit(composite_score), profit_factor, payoff_ratio, expectancy

    def run(self) -> dict[str, str | int | float | None]:
        with session_scope() as session:
            trades = session.scalars(select(BacktestTrade)).all()
        frame = pd.DataFrame(
            [
                {
                    "stock_symbol": row.stock_symbol,
                    "strategy_name": row.strategy_name,
                    "pnl_pct": row.pnl_pct or 0.0,
                    "trade_count": 1,
                    "holding_days": row.holding_days or 0,
                    "regime_at_entry": row.regime_at_entry,
                    "news_score_at_entry": row.news_score_at_entry or 0.0,
                    "quarter_at_entry": row.quarter_at_entry,
                }
                for row in trades
            ]
        )
        if frame.empty:
            return {"globalBestStrategy": None, "stocksCovered": 0, "medianSharpeRatio": 0.0}
        frame = frame[~frame["strategy_name"].isin(self.DISABLED_STRATEGIES)].copy()
        if frame.empty:
            return {"globalBestStrategy": None, "stocksCovered": 0, "medianSharpeRatio": 0.0}

        summaries = []
        for (symbol, strategy_name), group in frame.groupby(["stock_symbol", "strategy_name"]):
            returns = self._sanitize_returns(group["pnl_pct"] / 100)
            if returns.empty:
                continue
            total_return = float((1 + returns).prod() - 1)
            total_return = max(-self.MAX_RETURN_FOR_SCORING, min(self.MAX_RETURN_FOR_SCORING, total_return))
            volatility = float(returns.std() or 0.0)
            sharpe = float((returns.mean() / volatility) * (252 ** 0.5)) if volatility else 0.0
            win_rate = float((returns > 0).mean())
            equity = (1 + returns).cumprod()
            running_max = equity.cummax()
            max_drawdown = float(((equity - running_max) / running_max).min()) if not equity.empty else 0.0
            trade_count = int(len(group))
            composite_score, profit_factor, payoff_ratio, expectancy = self._composite_score(
                total_return=total_return,
                sharpe=sharpe,
                max_drawdown=max_drawdown,
                returns=returns,
                trade_count=trade_count,
            )
            summaries.append(
                {
                    "symbol": symbol,
                    "strategy_name": strategy_name,
                    "trade_count": trade_count,
                    "sharpe_ratio": sharpe,
                    "win_rate": win_rate,
                    "max_drawdown": max_drawdown,
                    "total_return": total_return,
                    "composite_score": composite_score,
                    "profit_factor": profit_factor,
                    "payoff_ratio": payoff_ratio,
                    "expectancy": expectancy,
                    "regime_performed_best": group["regime_at_entry"].mode().iloc[0] if not group["regime_at_entry"].mode().empty else None,
                    "avg_holding_days": int(group["holding_days"].mean()),
                    "sentiment_direction_best": "POSITIVE" if group["news_score_at_entry"].mean() > 0 else "NEGATIVE" if group["news_score_at_entry"].mean() < 0 else "NEUTRAL",
                    "best_quarter": group["quarter_at_entry"].mode().iloc[0] if not group["quarter_at_entry"].mode().empty else None,
                }
            )

        summary_df = pd.DataFrame(summaries)
        selected_sharpes: list[float] = []
        covered_symbols = 0
        with session_scope() as session:
            for symbol, group in summary_df.groupby("symbol"):
                record = session.get(StockStrategyMap, symbol) or StockStrategyMap(symbol=symbol)
                eligible = group[group.apply(lambda row: int(row["trade_count"]) >= self._min_trades_for_strategy(str(row["strategy_name"])), axis=1)]
                eligible = eligible[eligible["composite_score"].map(math.isfinite)]
                if eligible.empty:
                    record.best_strategy = None
                    record.sharpe_ratio = None
                    record.win_rate = None
                    record.max_drawdown = None
                    record.total_return = None
                    record.composite_score = None
                    record.regime_performed_best = None
                    record.avg_holding_days = None
                    record.sentiment_direction_best = None
                    record.best_quarter = None
                    record.last_updated = datetime.now(tz=self._settings.tzinfo)
                    session.merge(record)
                    continue

                best = eligible.sort_values("composite_score", ascending=False).iloc[0]
                selected_sharpes.append(self._finite_or_default(best["sharpe_ratio"]))
                covered_symbols += 1
                record.best_strategy = best["strategy_name"]
                record.sharpe_ratio = self._finite_or_default(best["sharpe_ratio"])
                record.win_rate = self._clamp_unit(best["win_rate"])
                record.max_drawdown = self._finite_or_default(best["max_drawdown"])
                record.total_return = self._finite_or_default(best["total_return"])
                record.composite_score = self._clamp_unit(best["composite_score"])
                record.regime_performed_best = best["regime_performed_best"]
                record.avg_holding_days = int(best["avg_holding_days"])
                record.sentiment_direction_best = best["sentiment_direction_best"]
                record.best_quarter = best["best_quarter"]
                record.last_updated = datetime.now(tz=self._settings.tzinfo)
                session.merge(record)

            eligible_summary = summary_df[
                summary_df.apply(
                    lambda row: int(row["trade_count"]) >= self._min_trades_for_strategy(str(row["strategy_name"])),
                    axis=1,
                )
            ]
            eligible_summary = eligible_summary[eligible_summary["composite_score"].map(math.isfinite)]
            summary_source = eligible_summary if not eligible_summary.empty else summary_df
            global_summary = (
                summary_source.groupby("strategy_name")[["composite_score", "sharpe_ratio"]]
                .mean()
                .reset_index()
                .sort_values("composite_score", ascending=False)
            )
            winner = global_summary.iloc[0]
            upsert_config_value(
                session,
                "global_best_strategy",
                {"name": winner["strategy_name"], "composite_score": float(winner["composite_score"])},
            )

        return {
            "globalBestStrategy": str(winner["strategy_name"]),
            "stocksCovered": covered_symbols,
            "medianSharpeRatio": float(pd.Series(selected_sharpes).median()) if selected_sharpes else 0.0,
        }
