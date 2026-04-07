from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


class GoldenCrossStrategy(BaseStrategy):
    name = "Golden Cross"
    signal_type = "INVESTMENT"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        return {"fast": 50, "slow": 200, "trailing_stop_pct": 0.10}

    def parameter_grid(self) -> list[dict[str, Any]]:
        return [
            {"fast": 40, "slow": 180, "trailing_stop_pct": 0.12},
            {"fast": 50, "slow": 200, "trailing_stop_pct": 0.10},
            {"fast": 55, "slow": 200, "trailing_stop_pct": 0.09},
        ]

    def generate_signal(
        self,
        df: pd.DataFrame,
        date: datetime | pd.Timestamp | None = None,
        *,
        context: StrategyContext | None = None,
        params: dict[str, Any] | None = None,
    ) -> SignalDict:
        params = self.resolve_params(context, params)
        data = self._frame_until(df, date)
        latest, previous = self._latest_rows(data)
        fast_length = int(params.get("fast", 50))
        slow_length = int(params.get("slow", 200))
        fast_col = f"SMA_{fast_length}"
        slow_col = f"SMA_{slow_length}"
        prev_fast = self._value(previous, fast_col, self._value(previous, "SMA_50", float(previous["Close"]))) or float(previous["Close"])
        prev_slow = self._value(previous, slow_col, self._value(previous, "SMA_200", float(previous["Close"]))) or float(previous["Close"])
        latest_fast = self._value(latest, fast_col, self._value(latest, "SMA_50", float(latest["Close"]))) or float(latest["Close"])
        latest_slow = self._value(latest, slow_col, self._value(latest, "SMA_200", float(latest["Close"]))) or float(latest["Close"])
        buy_cross = prev_fast <= prev_slow and latest_fast > latest_slow
        sell_cross = prev_fast >= prev_slow and latest_fast < latest_slow
        trailing_stop_pct = float(params.get("trailing_stop_pct", 0.10))
        trailing_stop = float(latest["Close"] * (1.0 - trailing_stop_pct))
        if buy_cross:
            result = self._response(
                "BUY",
                float(latest["Close"]),
                trailing_stop,
                signal_type=self.signal_type,
                pattern_name="golden_cross",
            )
            result["target_1"] = float(latest["Close"] * 1.05)
            result["target_2"] = float(latest["Close"] * 1.10)
            result["target_3"] = float(latest["Close"] * 1.15)
            return result
        if sell_cross:
            return self._response(
                "SELL",
                float(latest["Close"]),
                float(latest["Close"] * 1.10),
                signal_type=self.signal_type,
                pattern_name="death_cross",
            )
        return self._response("HOLD", signal_type=self.signal_type)
