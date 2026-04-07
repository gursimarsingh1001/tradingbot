from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any
import math

import pandas as pd


SignalDict = dict[str, Any]


@dataclass
class StrategyContext:
    news_score: float = 0.0
    regime: str = "RANGING"
    signal_type: str = "INTRADAY"
    timeframe: str = "DAILY"


class BaseStrategy(ABC):
    name = "Base Strategy"
    signal_type = "INTRADAY"

    def parameter_grid(self) -> list[dict[str, Any]]:
        return [self.default_parameters()]

    def default_parameters(self, context: StrategyContext | None = None) -> dict[str, Any]:
        return {}

    def resolve_params(
        self,
        context: StrategyContext | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = dict(self.default_parameters(context))
        if params:
            resolved.update(params)
        return resolved

    @staticmethod
    def is_intraday_context(context: StrategyContext | None = None) -> bool:
        timeframe = str((context.timeframe if context is not None else "DAILY") or "DAILY").upper()
        return timeframe == "INTRADAY"

    @abstractmethod
    def generate_signal(
        self,
        df: pd.DataFrame,
        date: datetime | pd.Timestamp | None = None,
        *,
        context: StrategyContext | None = None,
        params: dict[str, Any] | None = None,
    ) -> SignalDict:
        raise NotImplementedError

    def _frame_until(self, df: pd.DataFrame, date: datetime | pd.Timestamp | None) -> pd.DataFrame:
        if date is None:
            return df.copy()
        date = pd.Timestamp(date)
        return df.loc[df.index <= date].copy()

    def _latest_rows(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        if len(df) < 2:
            raise ValueError("At least two rows are required to generate a signal.")
        return df.iloc[-1], df.iloc[-2]

    def _targets(self, entry_price: float, stop_loss: float, direction: str = "BUY") -> tuple[float, float, float]:
        risk = abs(entry_price - stop_loss)
        if risk == 0:
            risk = entry_price * 0.01
        if direction == "BUY":
            return (entry_price + risk, entry_price + 2 * risk, entry_price + 3 * risk)
        return (entry_price - risk, entry_price - 2 * risk, entry_price - 3 * risk)

    def _value(self, row: pd.Series, column: str, default: float | None = None) -> float | None:
        value = row.get(column, default)
        if value is None or pd.isna(value):
            return default
        return float(value)

    def _response(
        self,
        signal: str,
        entry_price: float | None = None,
        stop_loss: float | None = None,
        *,
        strategy_name: str | None = None,
        pattern_name: str | None = None,
        signal_type: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SignalDict:
        strategy_name = strategy_name or self.name
        signal_type = signal_type or self.signal_type
        invalid_entry = entry_price is None or (isinstance(entry_price, (int, float)) and not math.isfinite(float(entry_price)))
        invalid_stop = stop_loss is None or (isinstance(stop_loss, (int, float)) and not math.isfinite(float(stop_loss)))
        if signal == "HOLD" or invalid_entry or invalid_stop:
            return {
                "signal": "HOLD",
                "entry_price": None,
                "stop_loss": None,
                "target_1": None,
                "target_2": None,
                "target_3": None,
                "strategy_name": strategy_name,
                "signal_type": signal_type,
                "pattern_name": pattern_name,
                "meta": meta or {},
            }
        target_1, target_2, target_3 = self._targets(entry_price, stop_loss, direction=signal)
        return {
            "signal": signal,
            "entry_price": float(entry_price),
            "stop_loss": float(stop_loss),
            "target_1": float(target_1),
            "target_2": float(target_2),
            "target_3": float(target_3),
            "strategy_name": strategy_name,
            "signal_type": signal_type,
            "pattern_name": pattern_name,
            "meta": meta or {},
        }

    def _recent_swing_low(self, df: pd.DataFrame, window: int = 5) -> float:
        return float(df["Low"].tail(window).min())

    def _recent_swing_high(self, df: pd.DataFrame, window: int = 5) -> float:
        return float(df["High"].tail(window).max())
