from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


class GlobalRiskThresholds:
    VIX_VELOCITY_CAUTION = 20.0
    VIX_VELOCITY_BLOCK = 40.0
    NIFTY_GAP_CAUTION = -1.5
    NIFTY_GAP_BLOCK = -3.0
    FII_CONSECUTIVE_SELL_DAYS = 5
    FII_HEAVY_SELL_SINGLE_DAY = -5000.0
    FII_CUMULATIVE_5DAY_BLOCK = -20000.0
    SP500_CAUTION = -2.0
    SP500_BLOCK = -4.0
    CRUDE_SPIKE_CAUTION = 5.0
    CRUDE_SPIKE_BLOCK = 10.0
    USDINR_CAUTION = 0.8
    USDINR_BLOCK = 2.0


@dataclass(slots=True)
class SignalResult:
    name: str
    severity: str
    value: float | None
    threshold: float
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GlobalRiskResult:
    as_of_date: date
    scan_type: str
    risk_level: str
    position_size_multiplier: float
    signals: list[SignalResult]
    active_caution_count: int
    active_block_count: int
    summary_message: str


__all__ = ["GlobalRiskResult", "GlobalRiskThresholds", "SignalResult"]
