from __future__ import annotations

from datetime import datetime

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


class SupportResistanceStrategy(BaseStrategy):
    name = "Support and Resistance"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        if self.is_intraday_context(context):
            return {
                "lookback": 20,
                "proximity_pct": 0.01,
                "stop_buffer_pct": 0.005,
                "target_extension_atr": 0.5,
                "buy_rsi_max": 40.0,
                "sell_rsi_min": 60.0,
                "volume_multiplier": 1.0,
                "min_confirmations": 2,
            }
        return {
            "lookback": 63,
            "proximity_pct": 0.012,
            "stop_buffer_pct": 0.01,
            "target_extension_atr": 1.0,
            "buy_rsi_max": 40.0,
            "sell_rsi_min": 60.0,
            "volume_multiplier": 1.0,
            "min_confirmations": 2,
        }

    def parameter_grid(self) -> list[dict[str, Any]]:
        return [
            {"lookback": 20, "proximity_pct": 0.010, "stop_buffer_pct": 0.006, "target_extension_atr": 0.5, "buy_rsi_max": 40.0, "sell_rsi_min": 60.0, "volume_multiplier": 1.0, "min_confirmations": 2},
            {"lookback": 63, "proximity_pct": 0.012, "stop_buffer_pct": 0.010, "target_extension_atr": 0.75, "buy_rsi_max": 40.0, "sell_rsi_min": 60.0, "volume_multiplier": 1.0, "min_confirmations": 2},
            {"lookback": 63, "proximity_pct": 0.014, "stop_buffer_pct": 0.012, "target_extension_atr": 1.0, "buy_rsi_max": 38.0, "sell_rsi_min": 62.0, "volume_multiplier": 1.05, "min_confirmations": 2},
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
        lookback = int(params.get("lookback", 63))
        support = self._value(latest, f"Low_{lookback}", self._value(latest, "Low_63", float(latest["Low"]))) or float(latest["Low"])
        resistance = self._value(latest, f"High_{lookback}", self._value(latest, "High_63", float(latest["High"]))) or float(latest["High"])
        proximity_pct = float(params.get("proximity_pct", 0.02))
        stop_buffer_pct = float(params.get("stop_buffer_pct", 0.01))
        atr = self._value(latest, "ATR_14", float(latest["Close"]) * 0.02) or float(latest["Close"]) * 0.02
        rsi = self._value(latest, "RSI_14", 50.0) or 50.0
        latest_volume = self._value(latest, "Volume")
        volume_sma = self._value(latest, "Volume_SMA_20", latest_volume if latest_volume is not None else 0.0)
        volume_ok = True if latest_volume is None or not volume_sma else latest_volume >= float(params.get("volume_multiplier", 1.0)) * volume_sma
        near_support = abs(latest["Close"] - support) / latest["Close"] <= proximity_pct
        bullish_pattern = any(latest[col] > 0 for col in ["HAMMER", "ENGULFING", "MORNING_STAR", "THREE_WHITE"])
        buy_rsi_ok = rsi < float(params.get("buy_rsi_max", 40.0))
        bullish_confirmations = int(bullish_pattern) + int(buy_rsi_ok) + int(volume_ok)
        if near_support and bullish_pattern and bullish_confirmations >= int(params.get("min_confirmations", 2)):
            result = self._response(
                "BUY",
                float(latest["Close"]),
                float(support * (1.0 - stop_buffer_pct)),
                pattern_name="support_bounce",
            )
            result["target_1"] = resistance
            result["target_2"] = resistance + (float(params.get("target_extension_atr", 1.0)) * atr)
            result["target_3"] = resistance + (float(params.get("target_extension_atr", 1.0)) * 2 * atr)
            return result
        near_resistance = abs(resistance - latest["Close"]) / latest["Close"] <= proximity_pct
        bearish_pattern = any(latest[col] < 0 for col in ["EVENING_STAR", "SHOOTING_ST", "DARK_CLOUD", "THREE_BLACK"])
        sell_rsi_ok = rsi > float(params.get("sell_rsi_min", 60.0))
        bearish_confirmations = int(bearish_pattern) + int(sell_rsi_ok) + int(volume_ok)
        if near_resistance and bearish_pattern and bearish_confirmations >= int(params.get("min_confirmations", 2)):
            result = self._response(
                "SELL",
                float(latest["Close"]),
                float(resistance * (1.0 + stop_buffer_pct)),
                pattern_name="resistance_reversal",
            )
            result["target_1"] = support
            result["target_2"] = max(support - (float(params.get("target_extension_atr", 1.0)) * atr), 0.01)
            result["target_3"] = max(support - (float(params.get("target_extension_atr", 1.0)) * 2 * atr), 0.01)
            return result
        return self._response("HOLD")
