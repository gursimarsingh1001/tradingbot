from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from backend.config import get_settings
from backend.db.redis_client import get_cache


settings = get_settings()
_vix_cache: dict[str, Any] = {"value": 0.0, "updated_at": None}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(parsed):
        return default
    return parsed


def _cached_vix_change_pct() -> float:
    now = datetime.now(tz=settings.tzinfo)
    updated_at = _vix_cache.get("updated_at")
    if isinstance(updated_at, datetime) and (now - updated_at).total_seconds() < 60:
        return float(_vix_cache.get("value") or 0.0)
    try:
        cache = get_cache()
        indices = cache.get_json("live:indices", {}) or {}
    except Exception:
        return float(_vix_cache.get("value") or 0.0)
    vix_row = indices.get("INDIA_VIX") or indices.get("INDIAVIX") or {}
    vix_value = max(_safe_float(vix_row.get("change_pct"), 0.0), 0.0)
    _vix_cache["value"] = vix_value
    _vix_cache["updated_at"] = now
    return vix_value


def _trend_bias(close: float, ema_20: float, sma_50: float, sma_200: float) -> str:
    bullish_checks = sum(
        [
            1 if close > ema_20 else 0,
            1 if ema_20 > sma_50 else 0,
            1 if sma_50 > sma_200 else 0,
        ]
    )
    bearish_checks = sum(
        [
            1 if close < ema_20 else 0,
            1 if ema_20 < sma_50 else 0,
            1 if sma_50 < sma_200 else 0,
        ]
    )
    if bullish_checks >= 2 and bullish_checks > bearish_checks:
        return "BULL"
    if bearish_checks >= 2 and bearish_checks > bullish_checks:
        return "BEAR"
    return "NEUTRAL"


def _slope(df: pd.DataFrame, column: str, lookback: int) -> float:
    if column not in df.columns or len(df) <= lookback:
        return 0.0
    current = _safe_float(df.iloc[-1].get(column), 0.0)
    previous = _safe_float(df.iloc[-1 - lookback].get(column), current)
    if previous == 0:
        return 0.0
    return (current - previous) / abs(previous)


def regime_is_high_volatility(regime: str | None) -> bool:
    return str(regime or "").upper().startswith("HIGH_VOLATILITY")


def regime_is_ranging(regime: str | None) -> bool:
    upper = str(regime or "").upper()
    return upper in {"RANGING", "TRANSITION"}


def regime_trend_direction(regime: str | None) -> str | None:
    upper = str(regime or "").upper()
    if "BULL" in upper:
        return "BULL"
    if "BEAR" in upper:
        return "BEAR"
    return None


def regime_is_trending(regime: str | None) -> bool:
    upper = str(regime or "").upper()
    return upper.startswith("TRENDING_")


def detect_regime(df: pd.DataFrame) -> str:
    if df.empty:
        return "RANGING"

    latest = df.iloc[-1]
    close = _safe_float(latest.get("Close"), 0.0)
    ema_20 = _safe_float(latest.get("EMA_20"), close)
    sma_50 = _safe_float(latest.get("SMA_50"), ema_20 or close)
    sma_200 = _safe_float(latest.get("SMA_200"), sma_50 or close)
    adx_value = _safe_float(latest.get("ADX"), 0.0)
    atr_value = _safe_float(latest.get("ATR_14"), 0.0)

    if "ATR_20_AVG" in df.columns:
        atr_avg = _safe_float(latest.get("ATR_20_AVG"), atr_value)
    elif "ATR_14" in df.columns:
        atr_avg = _safe_float(df["ATR_14"].rolling(20).mean().iloc[-1], atr_value)
    else:
        atr_avg = atr_value

    atr_ratio = (atr_value / max(atr_avg, 0.01)) if atr_avg else 0.0
    vix_change_pct = _cached_vix_change_pct()
    bias = _trend_bias(close, ema_20, sma_50, sma_200)
    sma_50_slope = _slope(df, "SMA_50", settings.regime_trend_slope_lookback)

    high_volatility = (
        atr_ratio >= settings.regime_high_volatility_atr_ratio
        or vix_change_pct >= settings.regime_vix_high_volatility_change_pct
    )

    if high_volatility and bias == "BULL":
        return "HIGH_VOLATILITY_BULL"
    if high_volatility and bias == "BEAR":
        return "HIGH_VOLATILITY_BEAR"
    if high_volatility:
        return "HIGH_VOLATILITY"

    strong_trend = adx_value >= settings.regime_adx_trend_threshold
    transition_trend = adx_value >= max(settings.regime_adx_transition_threshold, 20.0)

    if bias == "BULL" and strong_trend:
        mature = close > sma_200 and sma_50_slope >= 0 and adx_value >= (settings.regime_adx_trend_threshold + 5.0)
        return "TRENDING_BULL_MATURE" if mature else "TRENDING_BULL_EARLY"
    if bias == "BEAR" and strong_trend:
        mature = close < sma_200 and sma_50_slope <= 0 and adx_value >= (settings.regime_adx_trend_threshold + 5.0)
        return "TRENDING_BEAR_MATURE" if mature else "TRENDING_BEAR_EARLY"
    if transition_trend:
        return "TRANSITION"
    return "RANGING"
