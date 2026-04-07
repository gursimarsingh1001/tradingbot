from __future__ import annotations

from datetime import datetime

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


class BreakoutVolumeStrategy(BaseStrategy):
    name = "Breakout with Volume"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        if self.is_intraday_context(context):
            return {
                "lookback": 20,
                "volume_multiplier": 1.4,
                "stop_atr_mult": 1.2,
                "breakout_buffer_atr": 0.05,
                "trend_col": "VWAP",
            }
        return {
            "lookback": 20,
            "volume_multiplier": 1.8,
            "stop_atr_mult": 1.5,
            "breakout_buffer_atr": 0.0,
            "trend_col": "SMA_50",
        }

    def parameter_grid(self) -> list[dict[str, Any]]:
        return [
            {"lookback": 20, "volume_multiplier": 1.5, "stop_atr_mult": 1.2, "breakout_buffer_atr": 0.0, "trend_col": "EMA_20"},
            {"lookback": 20, "volume_multiplier": 1.8, "stop_atr_mult": 1.5, "breakout_buffer_atr": 0.1, "trend_col": "SMA_50"},
            {"lookback": 63, "volume_multiplier": 1.6, "stop_atr_mult": 2.0, "breakout_buffer_atr": 0.0, "trend_col": "SMA_50"},
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
        lookback = int(params.get("lookback", 20))
        high_col = f"High_{lookback}"
        low_col = f"Low_{lookback}"
        atr = self._value(latest, "ATR_14", float(latest["Close"]) * 0.02) or float(latest["Close"]) * 0.02
        breakout_level = self._value(latest, high_col, self._value(latest, "High_20", float(latest["High"]))) or float(latest["High"])
        breakdown_level = self._value(latest, low_col, self._value(latest, "Low_20", float(latest["Low"]))) or float(latest["Low"])
        breakout = latest["Close"] > (breakout_level + (float(params.get("breakout_buffer_atr", 0.0)) * atr))
        breakdown = latest["Close"] < (breakdown_level - (float(params.get("breakout_buffer_atr", 0.0)) * atr))
        volume_ok = latest["Volume"] > float(params.get("volume_multiplier", 2.0)) * latest["Volume_SMA_20"]
        trend_col = str(params.get("trend_col") or "SMA_50")
        trend_value = self._value(latest, trend_col, float(latest["Close"])) or float(latest["Close"])
        if breakout and volume_ok:
            stop = breakout_level - float(params.get("stop_atr_mult", 1.5)) * atr
            if latest["Close"] <= trend_value:
                return self._response("HOLD")
            return self._response("BUY", float(latest["Close"]), float(stop), pattern_name="20d_breakout")
        if breakdown:
            if latest["Close"] >= trend_value:
                return self._response("HOLD")
            stop = breakdown_level + float(params.get("stop_atr_mult", 1.5)) * atr
            return self._response("SELL", float(latest["Close"]), float(stop), pattern_name="20d_breakdown")
        return self._response("HOLD")
