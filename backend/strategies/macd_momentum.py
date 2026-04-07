from __future__ import annotations

from datetime import datetime

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


class MACDMomentumStrategy(BaseStrategy):
    name = "MACD Momentum"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        if self.is_intraday_context(context):
            return {
                "trend_col": "EMA_20",
                "hist_atr_ratio_min": 0.05,
                "hist_slope_min": 0.01,
                "stop_atr_mult": 1.5,
            }
        return {
            "trend_col": "SMA_50",
            "hist_atr_ratio_min": 0.02,
            "hist_slope_min": 0.0,
            "stop_atr_mult": 2.0,
        }

    def parameter_grid(self) -> list[dict[str, Any]]:
        return [
            {"trend_col": "EMA_20", "hist_atr_ratio_min": 0.02, "hist_slope_min": 0.00, "stop_atr_mult": 1.8},
            {"trend_col": "SMA_50", "hist_atr_ratio_min": 0.03, "hist_slope_min": 0.00, "stop_atr_mult": 2.0},
            {"trend_col": "SMA_50", "hist_atr_ratio_min": 0.05, "hist_slope_min": 0.01, "stop_atr_mult": 2.2},
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
        hist_floor = atr * float(params.get("hist_atr_ratio_min", 0.0))
        slope_floor = atr * float(params.get("hist_slope_min", 0.0))
        trend_col = str(params.get("trend_col") or "SMA_50")
        trend_value = self._value(latest, trend_col, self._value(latest, "SMA_50", float(latest["Close"]))) or float(latest["Close"])
        buy = (
            previous["MACD"] <= previous["MACD_Signal"]
            and latest["MACD"] > latest["MACD_Signal"]
            and latest["MACD_Hist"] >= hist_floor
            and latest["MACD_Hist"] >= previous["MACD_Hist"] + slope_floor
            and latest["Close"] > trend_value
        )
        sell = (
            previous["MACD"] >= previous["MACD_Signal"]
            and latest["MACD"] < latest["MACD_Signal"]
            and latest["MACD_Hist"] <= -hist_floor
            and latest["MACD_Hist"] <= previous["MACD_Hist"] - slope_floor
            and latest["Close"] < trend_value
        )
        stop_atr_mult = float(params.get("stop_atr_mult", 2.0))
        if buy:
            return self._response(
                "BUY",
                float(latest["Close"]),
                float(latest["Close"] - stop_atr_mult * atr),
                pattern_name="macd_bull_cross",
            )
        if sell:
            return self._response(
                "SELL",
                float(latest["Close"]),
                float(latest["Close"] + stop_atr_mult * atr),
                pattern_name="macd_bear_cross",
            )
        return self._response("HOLD")
