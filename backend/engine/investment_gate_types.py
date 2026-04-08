from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class GateResult:
    passed: bool
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InvestmentGateDecision:
    symbol: str
    as_of_date: date
    phase2_label: str
    phase2_votes_yes: int
    decision: str
    all_gates_passed: bool
    market_health_passed: bool
    sector_strength_passed: bool
    earnings_proximity_passed: bool
    promoter_passed: bool
    entry_trigger_passed: bool
    failure_reasons: list[str] = field(default_factory=list)
    debug_payload: dict[str, Any] = field(default_factory=dict)


__all__ = ["GateResult", "InvestmentGateDecision"]
