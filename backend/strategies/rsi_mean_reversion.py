from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


class RSIMeanReversionStrategy(BaseStrategy):
    name = "RSI Mean Reversion"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        if self.is_intraday_context(context):
            return {"oversold": 28, "exit": 58, "stop_window": 3, "sell_stop_atr_mult": 0.8}
        return {"oversold": 35, "exit": 65, "stop_window": 5, "sell_stop_atr_mult": 1.0}

    def parameter_grid(self) -> list[dict[str, Any]]:
        return [
            {"oversold": 25, "exit": 60, "stop_window": 4, "sell_stop_atr_mult": 0.8},
            {"oversold": 30, "exit": 62, "stop_window": 4, "sell_stop_atr_mult": 0.9},
            {"oversold": 35, "exit": 65, "stop_window": 5, "sell_stop_atr_mult": 1.0},
            {"oversold": 40, "exit": 68, "stop_window": 6, "sell_stop_atr_mult": 1.1},
        ]

    def generate_signal(
        self,
        df: pd.DataFrame,
        date: datetime | pd.Timestamp | None = None,
        *,
        context: StrategyContext | None = None,
        params: dict[str, Any] | None = None,
    ) -> SignalDict:
        context = context or StrategyContext()
        params = self.resolve_params(context, params)
        data = self._frame_until(df, date)
        latest, previous = self._latest_rows(data)
        oversold = params["oversold"]
        exit_level = params["exit"]
        stop_window = int(params.get("stop_window", 5))
        reversal = previous["RSI_14"] < oversold and latest["RSI_14"] > oversold
        if reversal:
            stop = self._recent_swing_low(data, window=stop_window)
            return self._response(
                "BUY",
                float(latest["Close"]),
                stop,
                pattern_name="rsi_reversal",
            )
        if latest["RSI_14"] >= exit_level:
            atr = self._value(latest, "ATR_14", float(latest["Close"]) * 0.02) or float(latest["Close"]) * 0.02
            return self._response(
                "SELL",
                float(latest["Close"]),
                float(latest["Close"] + (float(params.get("sell_stop_atr_mult", 1.0)) * atr)),
                pattern_name="rsi_overbought_exit",
            )
        return self._response("HOLD")
