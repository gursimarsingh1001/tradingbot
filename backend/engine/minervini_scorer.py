from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


@dataclass(slots=True)
class MinerviniScoreResult:
    symbol: str
    as_of_date: date
    passed_checks: int
    vote_yes: bool
    rs_percentile: float | None
    data_complete: bool
    missing_fields: list[str] = field(default_factory=list)
    checks_json: dict[str, Any] = field(default_factory=dict)


class MinerviniScorer:
    LOOKBACK_SESSIONS = 252
    RS_THRESHOLD = 70.0

    @staticmethod
    def _coerce_frame(frame: pd.DataFrame | None, as_of_date: date) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        filtered = frame.copy()
        if isinstance(filtered.index, pd.DatetimeIndex):
            filtered = filtered[filtered.index.date <= as_of_date]
        return filtered

    @staticmethod
    def _series_or_compute(frame: pd.DataFrame, column: str, window: int) -> pd.Series:
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce")
        return pd.to_numeric(frame["Close"], errors="coerce").rolling(window).mean()

    @classmethod
    def score(
        cls,
        symbol: str,
        frame: pd.DataFrame | None,
        rs_percentile: float | None,
        as_of_date: date,
    ) -> MinerviniScoreResult:
        filtered = cls._coerce_frame(frame, as_of_date)
        missing_fields: list[str] = []
        checks_json: dict[str, Any] = {}

        if filtered.empty:
            missing_fields.append("daily_ohlcv_frame")
        if len(filtered) < cls.LOOKBACK_SESSIONS:
            missing_fields.append("lookback_252_sessions")
        if rs_percentile is None:
            missing_fields.append("rs_percentile")

        if missing_fields:
            return MinerviniScoreResult(
                symbol=symbol,
                as_of_date=as_of_date,
                passed_checks=0,
                vote_yes=False,
                rs_percentile=rs_percentile,
                data_complete=False,
                missing_fields=list(dict.fromkeys(missing_fields)),
                checks_json={
                    "lookback_sessions": len(filtered),
                    "rs_percentile": rs_percentile,
                },
            )

        close_series = pd.to_numeric(filtered["Close"], errors="coerce")
        high_series = pd.to_numeric(filtered["High"], errors="coerce")
        low_series = pd.to_numeric(filtered["Low"], errors="coerce")
        sma50 = cls._series_or_compute(filtered, "SMA_50", 50)
        sma150 = cls._series_or_compute(filtered, "SMA_150", 150)
        sma200 = cls._series_or_compute(filtered, "SMA_200", 200)

        current_close = float(close_series.iloc[-1])
        current_sma50 = sma50.iloc[-1]
        current_sma150 = sma150.iloc[-1]
        current_sma200 = sma200.iloc[-1]
        prior_sma200 = sma200.iloc[-21] if len(sma200) >= 21 else float("nan")
        trailing_high = float(high_series.tail(cls.LOOKBACK_SESSIONS).max())
        trailing_low = float(low_series.tail(cls.LOOKBACK_SESSIONS).min())

        checks: list[tuple[str, bool]] = [
            ("close_above_sma50", bool(pd.notna(current_sma50) and current_close > float(current_sma50))),
            ("close_above_sma150", bool(pd.notna(current_sma150) and current_close > float(current_sma150))),
            ("close_above_sma200", bool(pd.notna(current_sma200) and current_close > float(current_sma200))),
            ("sma150_above_sma200", bool(pd.notna(current_sma150) and pd.notna(current_sma200) and float(current_sma150) > float(current_sma200))),
            ("sma200_rising_vs_20_sessions_ago", bool(pd.notna(current_sma200) and pd.notna(prior_sma200) and float(current_sma200) > float(prior_sma200))),
            ("close_30pct_above_52w_low", bool(trailing_low > 0 and current_close >= (1.30 * trailing_low))),
            ("close_within_25pct_of_52w_high", bool(trailing_high > 0 and current_close >= (0.75 * trailing_high))),
            ("rs_percentile_gte_70", bool(rs_percentile >= cls.RS_THRESHOLD)),
        ]

        passed_checks = sum(1 for _, passed in checks if passed)
        checks_json = {
            "checks": {name: passed for name, passed in checks},
            "inputs": {
                "close": current_close,
                "sma50": float(current_sma50) if pd.notna(current_sma50) else None,
                "sma150": float(current_sma150) if pd.notna(current_sma150) else None,
                "sma200": float(current_sma200) if pd.notna(current_sma200) else None,
                "sma200_20_sessions_ago": float(prior_sma200) if pd.notna(prior_sma200) else None,
                "52w_high": trailing_high,
                "52w_low": trailing_low,
                "rs_percentile": rs_percentile,
            },
        }
        return MinerviniScoreResult(
            symbol=symbol,
            as_of_date=as_of_date,
            passed_checks=passed_checks,
            vote_yes=passed_checks == len(checks),
            rs_percentile=rs_percentile,
            data_complete=True,
            missing_fields=[],
            checks_json=checks_json,
        )


__all__ = ["MinerviniScorer", "MinerviniScoreResult"]
