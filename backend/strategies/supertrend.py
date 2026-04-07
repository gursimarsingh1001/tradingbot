from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


class SupertrendStrategy(BaseStrategy):
    name = "Supertrend"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        if self.is_intraday_context(context):
            return {
                "adx_min": 18.0,
                "volume_multiplier": 1.3,
                "price_buffer_atr": 0.10,
                "stop_buffer_atr": 0.25,
                "trend_col": "EMA_20",
            }
        return {
            "adx_min": 18.0,
            "volume_multiplier": 1.3,
            "price_buffer_atr": 0.00,
            "stop_buffer_atr": 0.10,
            "trend_col": "SMA_50",
        }

    def parameter_grid(self) -> list[dict[str, Any]]:
        return [
            {"adx_min": 18.0, "volume_multiplier": 1.2, "price_buffer_atr": 0.00, "stop_buffer_atr": 0.10, "trend_col": "EMA_20"},
            {"adx_min": 18.0, "volume_multiplier": 1.3, "price_buffer_atr": 0.05, "stop_buffer_atr": 0.15, "trend_col": "EMA_20"},
            {"adx_min": 20.0, "volume_multiplier": 1.5, "price_buffer_atr": 0.10, "stop_buffer_atr": 0.25, "trend_col": "SMA_50"},
        ]

    def generate_signal(
        self,
        df: pd.DataFrame,
        date: datetime | pd.Timestamp | None = None,
        *,
        context: StrategyContext | None = None,
        params: dict | None = None,
    ) -> SignalDict:
        context = context or StrategyContext()
        params = self.resolve_params(context, params)
        data = self._frame_until(df, date)
        latest, previous = self._latest_rows(data)
        atr = self._value(latest, "ATR_14", float(latest["Close"]) * 0.02) or float(latest["Close"]) * 0.02
        adx_min = float(params.get("adx_min", 16.0))
        price_buffer = float(params.get("price_buffer_atr", 0.0)) * atr
        stop_buffer = float(params.get("stop_buffer_atr", 0.0)) * atr
        trend_col = str(params.get("trend_col") or "SMA_50")
        trend_value = self._value(latest, trend_col, float(latest["Close"])) or float(latest["Close"])
        adx = self._value(latest, "ADX", 0.0) or 0.0
        latest_volume = self._value(latest, "Volume")
        volume_sma = self._value(latest, "Volume_SMA_20", latest_volume if latest_volume is not None else 0.0)
        volume_ok = True if latest_volume is None or not volume_sma else latest_volume > float(params.get("volume_multiplier", 1.3)) * volume_sma
        flip_up = (
            previous["Close"] <= previous["Supertrend"]
            and latest["Close"] > latest["Supertrend"] + price_buffer
            and adx >= adx_min
            and volume_ok
            and latest["Close"] > trend_value
        )
        flip_down = (
            previous["Close"] >= previous["Supertrend"]
            and latest["Close"] < latest["Supertrend"] - price_buffer
            and adx >= adx_min
            and volume_ok
            and latest["Close"] < trend_value
        )
        if flip_up:
            return self._response(
                "BUY",
                float(latest["Close"]),
                float(latest["Supertrend"] - stop_buffer),
                pattern_name="supertrend_flip_up",
            )
        if flip_down:
            return self._response(
                "SELL",
                float(latest["Close"]),
                float(latest["Supertrend"] + stop_buffer),
                pattern_name="supertrend_flip_down",
            )
        return self._response("HOLD")
