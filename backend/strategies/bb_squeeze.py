from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


class BollingerBandSqueezeStrategy(BaseStrategy):
    name = "Bollinger Band Squeeze"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        if self.is_intraday_context(context):
            return {
                "squeeze_factor": 0.95,
                "volume_multiplier": 1.8,
                "breakout_buffer_atr": 0.10,
                "stop_atr_mult": 0.35,
                "trend_col": "EMA_20",
            }
        return {
            "squeeze_factor": 1.0,
            "volume_multiplier": 1.5,
            "breakout_buffer_atr": 0.0,
            "stop_atr_mult": 0.25,
            "trend_col": "EMA_20",
        }

    def parameter_grid(self) -> list[dict | None]:
        return [
            {"squeeze_factor": 0.90, "volume_multiplier": 1.3, "breakout_buffer_atr": 0.00, "stop_atr_mult": 0.25, "trend_col": "EMA_20"},
            {"squeeze_factor": 1.00, "volume_multiplier": 1.5, "breakout_buffer_atr": 0.05, "stop_atr_mult": 0.35, "trend_col": "EMA_20"},
            {"squeeze_factor": 1.05, "volume_multiplier": 1.8, "breakout_buffer_atr": 0.10, "stop_atr_mult": 0.50, "trend_col": "SMA_50"},
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
        latest, _ = self._latest_rows(data)
        atr = self._value(latest, "ATR_14", float(latest["Close"]) * 0.02) or float(latest["Close"]) * 0.02
        trend_col = str(params.get("trend_col") or "EMA_20")
        trend_value = self._value(latest, trend_col, float(latest["Close"])) or float(latest["Close"])
        squeezed = latest["BB_Width"] <= latest["BB_Width_Avg_20"] * float(params.get("squeeze_factor", 1.0))
        breakout = latest["Close"] > (latest["BB_Upper"] + (float(params.get("breakout_buffer_atr", 0.0)) * atr))
        breakdown = latest["Close"] < (latest["BB_Lower"] - (float(params.get("breakout_buffer_atr", 0.0)) * atr))
        latest_volume = self._value(latest, "Volume")
        volume_sma = self._value(latest, "Volume_SMA_20", latest_volume if latest_volume is not None else 0.0)
        volume_ok = True if latest_volume is None or not volume_sma else latest_volume > float(params.get("volume_multiplier", 1.5)) * volume_sma
        if squeezed and breakout and volume_ok:
            return self._response(
                "BUY",
                float(latest["Close"]),
                float(min(latest["BB_Lower"], latest["Close"] - (float(params.get("stop_atr_mult", 0.25)) * atr))),
                pattern_name="bb_squeeze_breakout",
            )
        if squeezed and breakdown and volume_ok and latest["Close"] < trend_value:
            return self._response(
                "SELL",
                float(latest["Close"]),
                float(max(latest["BB_Upper"], latest["Close"] + (float(params.get("stop_atr_mult", 0.25)) * atr))),
                pattern_name="bb_failed_breakout",
            )
        return self._response("HOLD")
