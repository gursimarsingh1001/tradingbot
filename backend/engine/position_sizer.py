from __future__ import annotations

from backend.config import get_settings
from backend.engine.regime_detector import regime_is_high_volatility


settings = get_settings()


def calculate_size(
    confidence: float,
    atr: float,
    portfolio_value: float,
    entry_price: float,
    regime: str | None = None,
    leverage_multiplier: float = 1.0,
    max_position_pct: float = 0.15,
    remaining_risk_amount: float | None = None,
    risk_per_trade_pct: float | None = None,
) -> int:
    risk_per_trade = max(float(risk_per_trade_pct if risk_per_trade_pct is not None else settings.paper_risk_per_trade_pct), 0.0)
    stop_distance = max(2.0 * atr, entry_price * 0.005)
    conf_mult = 1.0 if confidence >= 90 else 0.7 if confidence >= 70 else 0.4
    if regime_is_high_volatility(regime):
        conf_mult *= 0.5

    risk_amount = portfolio_value * risk_per_trade
    if remaining_risk_amount is not None:
        risk_amount = min(risk_amount, max(float(remaining_risk_amount), 0.0))
    if risk_amount <= 0:
        return 0
    shares = int((risk_amount / stop_distance) * conf_mult)
    max_notional = portfolio_value * max_position_pct * max(leverage_multiplier, 1.0)
    max_shares = int(max_notional / entry_price)
    return max(0, min(shares, max_shares))
