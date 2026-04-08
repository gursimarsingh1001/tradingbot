from __future__ import annotations

from datetime import date

import pandas as pd

from backend.engine.investment_gate_types import GateResult


class EntryTrigger:
    LOOKBACK_SESSIONS = 20
    VOLUME_MULTIPLIER = 1.5

    @staticmethod
    def _coerce_frame(frame: pd.DataFrame | None, as_of_date: date) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        filtered = frame.copy()
        if isinstance(filtered.index, pd.DatetimeIndex):
            filtered = filtered[filtered.index.date <= as_of_date]
        return filtered

    @classmethod
    def evaluate(
        cls,
        symbol: str,
        frame: pd.DataFrame | None,
        as_of_date: date,
    ) -> GateResult:
        filtered = cls._coerce_frame(frame, as_of_date)
        if filtered.empty:
            return GateResult(
                False,
                "daily_ohlcv_frame_missing",
                "Daily OHLCV history is missing, so the breakout trigger cannot fire.",
                {"symbol": symbol},
            )
        if len(filtered) < cls.LOOKBACK_SESSIONS + 1:
            return GateResult(
                False,
                "entry_trigger_history_too_short",
                "Not enough daily history exists to evaluate the breakout trigger.",
                {"symbol": symbol, "rows": len(filtered), "required_rows": cls.LOOKBACK_SESSIONS + 1},
            )
        for column in ("Close", "High", "Volume"):
            if column not in filtered.columns:
                return GateResult(
                    False,
                    "entry_trigger_incomplete",
                    "Daily OHLCV history is incomplete, so the breakout trigger cannot fire.",
                    {"symbol": symbol, "missing_column": column},
                )

        latest = filtered.iloc[-1]
        prior = filtered.iloc[-(cls.LOOKBACK_SESSIONS + 1) : -1]
        close = pd.to_numeric(pd.Series([latest.get("Close")]), errors="coerce").iloc[0]
        high_20 = pd.to_numeric(prior["High"], errors="coerce").max()
        avg_volume_20 = pd.to_numeric(prior["Volume"], errors="coerce").mean()
        latest_volume = pd.to_numeric(pd.Series([latest.get("Volume")]), errors="coerce").iloc[0]

        if pd.isna(close) or pd.isna(high_20) or pd.isna(avg_volume_20) or pd.isna(latest_volume) or float(avg_volume_20) <= 0:
            return GateResult(
                False,
                "entry_trigger_incomplete",
                "Breakout trigger inputs are incomplete, so the trade is blocked.",
                {
                    "symbol": symbol,
                    "close": None if pd.isna(close) else float(close),
                    "high_20": None if pd.isna(high_20) else float(high_20),
                    "avg_volume_20": None if pd.isna(avg_volume_20) else float(avg_volume_20),
                    "latest_volume": None if pd.isna(latest_volume) else float(latest_volume),
                },
            )

        breakout_pass = float(close) > float(high_20)
        volume_ratio = float(latest_volume) / float(avg_volume_20)
        volume_pass = volume_ratio > cls.VOLUME_MULTIPLIER

        if not breakout_pass or not volume_pass:
            reasons: list[str] = []
            if not breakout_pass:
                reasons.append("close_not_above_prior_20_day_high")
            if not volume_pass:
                reasons.append("volume_not_above_1.5x_average")
            return GateResult(
                False,
                "entry_trigger_not_confirmed",
                "The Minervini breakout confirmation did not fire.",
                {
                    "symbol": symbol,
                    "reasons": reasons,
                    "close": float(close),
                    "prior_20_day_high": float(high_20),
                    "latest_volume": float(latest_volume),
                    "avg_volume_20": float(avg_volume_20),
                    "volume_ratio": volume_ratio,
                    "required_volume_ratio": cls.VOLUME_MULTIPLIER,
                },
            )

        return GateResult(
            True,
            "entry_trigger_pass",
            "The Minervini breakout confirmation fired.",
            {
                "symbol": symbol,
                "close": float(close),
                "prior_20_day_high": float(high_20),
                "latest_volume": float(latest_volume),
                "avg_volume_20": float(avg_volume_20),
                "volume_ratio": volume_ratio,
                "required_volume_ratio": cls.VOLUME_MULTIPLIER,
            },
        )


__all__ = ["EntryTrigger"]
