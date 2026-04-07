from __future__ import annotations

from datetime import datetime

import pandas as pd

from backend.config import get_settings
from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext


settings = get_settings()


class NewsDrivenMomentumStrategy(BaseStrategy):
    name = "News-Driven Momentum"

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        if self.is_intraday_context(context):
            return {
                "sentiment_threshold": max(settings.news_momentum_sentiment_threshold - 0.05, 0.30),
                "bull_rsi_low": 42,
                "bull_rsi_high": 72,
                "bear_rsi_low": 32,
                "bear_rsi_high": 50,
                "trend_col": "EMA_20",
                "stop_atr_mult": 1.5,
                "trend_buffer_pct": 0.003,
            }
        return {
            "sentiment_threshold": settings.news_momentum_sentiment_threshold,
            "bull_rsi_low": 45,
            "bull_rsi_high": 65,
            "bear_rsi_low": 35,
            "bear_rsi_high": 55,
            "trend_col": "EMA_20",
            "stop_atr_mult": 2.0,
            "trend_buffer_pct": 0.0,
        }

    def parameter_grid(self) -> list[dict[str, Any]]:
        base = settings.news_momentum_sentiment_threshold
        return [
            {"sentiment_threshold": max(base - 0.10, 0.20), "bull_rsi_low": 42, "bull_rsi_high": 72, "bear_rsi_low": 32, "bear_rsi_high": 55, "trend_col": "EMA_20", "stop_atr_mult": 1.6, "trend_buffer_pct": 0.004},
            {"sentiment_threshold": max(base - 0.05, 0.30), "bull_rsi_low": 44, "bull_rsi_high": 68, "bear_rsi_low": 34, "bear_rsi_high": 55, "trend_col": "EMA_20", "stop_atr_mult": 2.0, "trend_buffer_pct": 0.002},
            {"sentiment_threshold": base + 0.05, "bull_rsi_low": 45, "bull_rsi_high": 65, "bear_rsi_low": 32, "bear_rsi_high": 52, "trend_col": "SMA_50", "stop_atr_mult": 2.2, "trend_buffer_pct": 0.0},
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
        sentiment_threshold = float(params.get("sentiment_threshold", settings.news_momentum_sentiment_threshold))
        trend_col = str(params.get("trend_col") or "EMA_20")
        trend_buffer_pct = float(params.get("trend_buffer_pct", 0.0))
        trend_value = self._value(latest, trend_col, self._value(latest, "EMA_20", float(latest["Close"]))) or float(latest["Close"])
        bullish = (
            context.news_score >= sentiment_threshold
            and float(params.get("bull_rsi_low", 45)) <= latest["RSI_14"] <= float(params.get("bull_rsi_high", 65))
            and latest["Close"] >= trend_value * (1.0 - trend_buffer_pct)
        )
        bearish = (
            context.news_score <= -sentiment_threshold
            and float(params.get("bear_rsi_low", 35)) <= latest["RSI_14"] <= float(params.get("bear_rsi_high", 55))
            and latest["Close"] <= trend_value * (1.0 + trend_buffer_pct)
        )
        atr = self._value(latest, "ATR_14", float(latest["Close"]) * 0.02) or float(latest["Close"]) * 0.02
        stop_atr_mult = float(params.get("stop_atr_mult", 2.0))
        if bullish:
            return self._response(
                "BUY",
                float(latest["Close"]),
                float(latest["Close"] - stop_atr_mult * atr),
                pattern_name="news_momentum_bull",
            )
        if bearish:
            return self._response(
                "SELL",
                float(latest["Close"]),
                float(latest["Close"] + stop_atr_mult * atr),
                pattern_name="news_momentum_bear",
            )
        return self._response("HOLD")
