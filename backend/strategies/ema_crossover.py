from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


class EMACrossoverStrategy(BaseStrategy):
    name = "EMA Crossover"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        if self.is_intraday_context(context):
            return {"fast": 8, "slow": 21, "trend_col": "EMA_20", "stop_atr_mult": 1.5, "volume_multiplier": 1.2}
        return {"fast": 9, "slow": 20, "trend_col": "SMA_50", "stop_atr_mult": 2.0, "volume_multiplier": 1.2}

    def parameter_grid(self) -> list[dict[str, Any]]:
        return [
            {"fast": 8, "slow": 20, "trend_col": "EMA_20", "stop_atr_mult": 1.6, "volume_multiplier": 1.15},
            {"fast": 9, "slow": 20, "trend_col": "SMA_50", "stop_atr_mult": 2.0, "volume_multiplier": 1.20},
            {"fast": 10, "slow": 21, "trend_col": "SMA_50", "stop_atr_mult": 2.2, "volume_multiplier": 1.30},
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

        fast_length = int(params.get("fast", 9))
        slow_length = int(params.get("slow", 20))
        fast_col = f"EMA_{fast_length}"
        slow_col = f"EMA_{slow_length}"
        trend_col = str(params.get("trend_col") or "SMA_50")

        previous_fast = self._value(previous, fast_col, self._value(previous, "EMA_9", float(previous["Close"]))) or float(previous["Close"])
        previous_slow = self._value(previous, slow_col, self._value(previous, "EMA_20", float(previous["Close"]))) or float(previous["Close"])
        latest_fast = self._value(latest, fast_col, self._value(latest, "EMA_9", float(latest["Close"]))) or float(latest["Close"])
        latest_slow = self._value(latest, slow_col, self._value(latest, "EMA_20", float(latest["Close"]))) or float(latest["Close"])

        buy_cross = previous_fast <= previous_slow and latest_fast > latest_slow
        sell_cross = previous_fast >= previous_slow and latest_fast < latest_slow
        trend_value = self._value(latest, trend_col, float(latest["Close"])) or float(latest["Close"])
        above_trend = float(latest["Close"]) > trend_value
        below_trend = float(latest["Close"]) < trend_value
        latest_volume = self._value(latest, "Volume")
        volume_sma = self._value(latest, "Volume_SMA_20", latest_volume if latest_volume is not None else 0.0)
        volume_ok = True if latest_volume is None or not volume_sma else latest_volume > float(params.get("volume_multiplier", 1.2)) * volume_sma
        atr = self._value(latest, "ATR_14", float(latest["Close"]) * 0.02) or float(latest["Close"]) * 0.02
        stop_atr_mult = float(params.get("stop_atr_mult", 2.0))

        if buy_cross and above_trend and volume_ok:
            return self._response(
                "BUY",
                float(latest["Close"]),
                float(latest["Close"] - stop_atr_mult * atr),
                pattern_name="ema_crossover",
            )
        if sell_cross and below_trend and volume_ok:
            return self._response(
                "SELL",
                float(latest["Close"]),
                float(latest["Close"] + stop_atr_mult * atr),
                pattern_name="ema_crossdown",
            )
        return self._response("HOLD")
