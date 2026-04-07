from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from backend.config import get_settings


settings = get_settings()


def normalize_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=settings.tzinfo)
    return dt


def sanitize_news_timestamp(value: Any) -> datetime | None:
    dt = normalize_timestamp(value)
    if dt is None:
        return None
    if dt > datetime.now(tz=settings.tzinfo) + timedelta(hours=settings.news_future_tolerance_hours):
        return None
    return dt


def validate_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame

    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in frame.columns for column in required):
        return pd.DataFrame(columns=required)

    cleaned = frame.copy()
    if not isinstance(cleaned.index, pd.DatetimeIndex):
        cleaned.index = pd.to_datetime(cleaned.index, errors="coerce")

    if cleaned.index.tz is None:
        cleaned.index = cleaned.index.tz_localize(settings.tzinfo)

    cleaned = cleaned[~cleaned.index.isna()]
    cleaned = cleaned[~cleaned.index.duplicated(keep="last")]
    cleaned = cleaned.sort_index()

    cleaned[required] = cleaned[required].apply(pd.to_numeric, errors="coerce")
    cleaned = cleaned.dropna(subset=required)

    valid = (
        (cleaned["Open"] > 0)
        & (cleaned["High"] > 0)
        & (cleaned["Low"] > 0)
        & (cleaned["Close"] > 0)
        & (cleaned["Volume"] >= 0)
        & (cleaned["High"] >= cleaned[["Open", "Close", "Low"]].max(axis=1))
        & (cleaned["Low"] <= cleaned[["Open", "Close", "High"]].min(axis=1))
    )
    cleaned = cleaned.loc[valid]

    return cleaned


def validate_quote_snapshot(*, ltp: float, close: float, cached_ltp: float | None = None) -> bool:
    if ltp <= 0:
        return False
    if close < 0:
        return False
    if close > 0:
        change_pct = abs((ltp - close) / close)
        if change_pct > settings.market_quote_max_change_pct:
            return False
    if cached_ltp and cached_ltp > 0:
        jump_pct = abs((ltp - cached_ltp) / cached_ltp)
        if jump_pct > settings.market_quote_max_jump_vs_cache_pct:
            return False
    return True


def sanitize_fundamental_snapshot(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None

    cleaned = dict(payload)
    as_of_date = cleaned.get("asOfDate")
    if as_of_date:
        dt = normalize_timestamp(f"{as_of_date}T00:00:00")
        if dt is None:
            cleaned["asOfDate"] = None
        elif dt.date() > (datetime.now(tz=settings.tzinfo) + timedelta(days=settings.fundamentals_future_tolerance_days)).date():
            return None

    bounded_ranges = {
        "revenueGrowthPct": (-500.0, 500.0),
        "profitGrowthPct": (-500.0, 500.0),
        "roe": (-200.0, 200.0),
        "roce": (-200.0, 200.0),
        "debtToEquity": (0.0, 100.0),
        "currentRatio": (0.0, 100.0),
        "operatingMargin": (-200.0, 200.0),
        "netMargin": (-200.0, 200.0),
        "promoterHolding": (0.0, 100.0),
        "pledgedPct": (0.0, 100.0),
        "peRatio": (-5000.0, 5000.0),
        "pbRatio": (0.0, 1000.0),
        "dividendYield": (0.0, 100.0),
    }

    for key, (lower, upper) in bounded_ranges.items():
        value = cleaned.get(key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            cleaned[key] = None
            continue
        if numeric < lower or numeric > upper:
            cleaned[key] = None
        else:
            cleaned[key] = numeric

    core_metric_keys = [
        "revenueGrowthPct",
        "profitGrowthPct",
        "roe",
        "roce",
        "debtToEquity",
        "currentRatio",
        "operatingMargin",
        "netMargin",
        "peRatio",
        "pbRatio",
    ]
    if not any(cleaned.get(key) is not None for key in core_metric_keys):
        return None

    return cleaned
