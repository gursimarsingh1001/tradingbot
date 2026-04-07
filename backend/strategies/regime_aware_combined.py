from __future__ import annotations

from datetime import datetime

import pandas as pd

from backend.engine.regime_detector import detect_regime, regime_is_high_volatility, regime_is_ranging, regime_is_trending
from backend.strategies.base_strategy import BaseStrategy, SignalDict, StrategyContext
from backend.strategies.macd_momentum import MACDMomentumStrategy
from backend.strategies.rsi_mean_reversion import RSIMeanReversionStrategy


class RegimeAwareCombinedStrategy(BaseStrategy):
    name = "Combined Regime-Aware"

    def __init__(self) -> None:
        self.macd_strategy = MACDMomentumStrategy()
        self.rsi_strategy = RSIMeanReversionStrategy()

    def generate_signal(
        self,
        df: pd.DataFrame,
        date: datetime | pd.Timestamp | None = None,
        *,
        context: StrategyContext | None = None,
        params: dict | None = None,
    ) -> SignalDict:
        data = self._frame_until(df, date)
        regime = detect_regime(data)
        context = context or StrategyContext(regime=regime)
        if regime_is_trending(regime):
            signal = self.macd_strategy.generate_signal(data, context=context)
            signal["meta"]["active_regime"] = regime
            return signal
        if regime_is_ranging(regime):
            signal = self.rsi_strategy.generate_signal(data, context=context)
            signal["meta"]["active_regime"] = regime
            return signal
        latest = data.iloc[-1]
        atr = self._value(latest, "ATR_14", float(latest["Close"]) * 0.02) or float(latest["Close"]) * 0.02
        return self._response(
            "HOLD",
            strategy_name=self.name,
            pattern_name="regime_high_volatility_pause" if regime_is_high_volatility(regime) else "regime_transition_pause",
            meta={
                "active_regime": regime,
                "tight_trailing_stop": float(latest["Close"] - atr),
            },
        )
