from __future__ import annotations

import pandas as pd
import pandas_ta as ta

from backend.logging_utils import get_logger

try:
    import talib
except Exception:  # pragma: no cover
    talib = None


logger = get_logger(__name__)


class IndicatorCalculator:
    _warned_talib_missing = False

    @staticmethod
    def _nan_series(df: pd.DataFrame) -> pd.Series:
        return pd.Series(float("nan"), index=df.index, dtype="float64")

    @classmethod
    def _series_or_nan(cls, value: pd.Series | None, df: pd.DataFrame) -> pd.Series:
        if isinstance(value, pd.Series):
            return pd.to_numeric(value, errors="coerce").reindex(df.index)
        return cls._nan_series(df)

    @classmethod
    def _frame_column_or_nan(
        cls,
        frame: pd.DataFrame | pd.Series | None,
        column: str,
        df: pd.DataFrame,
        *,
        startswith: tuple[str, ...] = (),
    ) -> pd.Series:
        if isinstance(frame, pd.Series):
            return cls._series_or_nan(frame, df)
        if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
            return cls._nan_series(df)
        if column in frame.columns:
            return cls._series_or_nan(frame[column], df)
        for candidate in frame.columns:
            if candidate == column or any(candidate.startswith(prefix) for prefix in startswith):
                return cls._series_or_nan(frame[candidate], df)
        return cls._nan_series(df)

    @classmethod
    def _vwap_or_nan(cls, df: pd.DataFrame) -> pd.Series:
        high = df["High"].copy()
        low = df["Low"].copy()
        close = df["Close"].copy()
        volume = df["Volume"].copy()
        original_index = df.index
        if isinstance(original_index, pd.DatetimeIndex) and original_index.tz is not None:
            naive_index = original_index.tz_localize(None)
            high.index = naive_index
            low.index = naive_index
            close.index = naive_index
            volume.index = naive_index
        vwap = ta.vwap(high, low, close, volume)
        if isinstance(vwap, pd.Series):
            vwap.index = original_index
        return cls._series_or_nan(vwap, df)

    @staticmethod
    def _body(df: pd.DataFrame) -> pd.Series:
        return (df["Close"] - df["Open"]).abs()

    @staticmethod
    def _range(df: pd.DataFrame) -> pd.Series:
        return (df["High"] - df["Low"]).replace(0, pd.NA)

    @staticmethod
    def _upper_shadow(df: pd.DataFrame) -> pd.Series:
        candle_high = df[["Open", "Close"]].max(axis=1)
        return (df["High"] - candle_high).clip(lower=0.0)

    @staticmethod
    def _lower_shadow(df: pd.DataFrame) -> pd.Series:
        candle_low = df[["Open", "Close"]].min(axis=1)
        return (candle_low - df["Low"]).clip(lower=0.0)

    @classmethod
    def _candlestick_fallback(cls, df: pd.DataFrame) -> dict[str, pd.Series]:
        body = cls._body(df)
        total_range = cls._range(df).fillna(0.0)
        upper_shadow = cls._upper_shadow(df)
        lower_shadow = cls._lower_shadow(df)

        bullish = df["Close"] > df["Open"]
        bearish = df["Close"] < df["Open"]
        small_body = body <= (total_range * 0.12)
        midpoint = (df["Open"] + df["Close"]) / 2
        bullish_prev = bullish.shift(1, fill_value=False)
        bearish_prev = bearish.shift(1, fill_value=False)
        bullish_prev2 = bullish.shift(2, fill_value=False)
        bearish_prev2 = bearish.shift(2, fill_value=False)
        small_body_prev = small_body.shift(1, fill_value=False)

        prev_open = df["Open"].shift(1)
        prev_close = df["Close"].shift(1)
        prev_high = df["High"].shift(1)
        prev_low = df["Low"].shift(1)
        prev_body = body.shift(1)
        prev_midpoint = midpoint.shift(1)

        hammer = (
            (lower_shadow >= body * 2.0)
            & (upper_shadow <= (body + (total_range * 0.08)))
            & ((df["Close"] - df["Low"]) >= total_range * 0.55)
        )
        inverted_hammer = (
            (upper_shadow >= body * 2.0)
            & (lower_shadow <= (body + (total_range * 0.08)))
            & ((df["High"] - df["Close"]) <= total_range * 0.45)
        )
        shooting_star = (
            (upper_shadow >= body * 2.0)
            & (lower_shadow <= (body + (total_range * 0.08)))
            & ((df["High"] - df["Open"].where(bearish, df["Close"])) >= total_range * 0.55)
        )
        doji = body <= (total_range * 0.08)

        bullish_engulfing = (
            bearish_prev
            & bullish
            & (df["Open"] <= prev_close)
            & (df["Close"] >= prev_open)
            & (body >= prev_body.fillna(0.0))
        )
        bearish_engulfing = (
            bullish_prev
            & bearish
            & (df["Open"] >= prev_close)
            & (df["Close"] <= prev_open)
            & (body >= prev_body.fillna(0.0))
        )

        morning_star = (
            bearish_prev2
            & small_body_prev
            & bullish
            & (df["Close"] >= ((df["Open"].shift(2) + df["Close"].shift(2)) / 2))
            & (prev_close <= df["Close"].shift(2))
        )
        evening_star = (
            bullish_prev2
            & small_body_prev
            & bearish
            & (df["Close"] <= ((df["Open"].shift(2) + df["Close"].shift(2)) / 2))
            & (prev_close >= df["Close"].shift(2))
        )

        piercing = (
            bearish_prev
            & bullish
            & (df["Open"] <= prev_low.fillna(df["Open"]))
            & (df["Close"] > prev_midpoint.fillna(df["Close"]))
            & (df["Close"] < prev_open.fillna(df["Close"]))
        )
        dark_cloud = (
            bullish_prev
            & bearish
            & (df["Open"] >= prev_high.fillna(df["Open"]))
            & (df["Close"] < prev_midpoint.fillna(df["Close"]))
            & (df["Close"] > prev_open.fillna(df["Close"]))
        )

        three_white = (
            bullish
            & bullish_prev
            & bullish_prev2
            & (df["Close"] > prev_close.fillna(df["Close"]))
            & (prev_close > df["Close"].shift(2).fillna(prev_close))
        )
        three_black = (
            bearish
            & bearish_prev
            & bearish_prev2
            & (df["Close"] < prev_close.fillna(df["Close"]))
            & (prev_close < df["Close"].shift(2).fillna(prev_close))
        )

        return {
            "HAMMER": hammer.astype(int) * 100,
            "ENGULFING": bullish_engulfing.astype(int) * 100 - bearish_engulfing.astype(int) * 100,
            "MORNING_STAR": morning_star.astype(int) * 100,
            "EVENING_STAR": evening_star.astype(int) * -100,
            "DOJI": doji.astype(int) * 100,
            "SHOOTING_ST": shooting_star.astype(int) * -100,
            "HAMMER_INV": inverted_hammer.astype(int) * 100,
            "PIERCING": piercing.astype(int) * 100,
            "DARK_CLOUD": dark_cloud.astype(int) * -100,
            "THREE_WHITE": three_white.astype(int) * 100,
            "THREE_BLACK": three_black.astype(int) * -100,
        }

    @staticmethod
    def enrich(frame: pd.DataFrame) -> pd.DataFrame:
        df = frame.copy()
        if df.empty:
            return df

        for length in (8, 9, 10, 12, 20, 21, 26, 50):
            df[f"EMA_{length}"] = IndicatorCalculator._series_or_nan(ta.ema(df["Close"], length=length), df)
        for length in (20, 40, 50, 55, 100, 180, 200):
            df[f"SMA_{length}"] = IndicatorCalculator._series_or_nan(ta.sma(df["Close"], length=length), df)

        df["RSI_14"] = IndicatorCalculator._series_or_nan(ta.rsi(df["Close"], length=14), df)
        macd = ta.macd(df["Close"])
        df["MACD"] = IndicatorCalculator._frame_column_or_nan(macd, "MACD_12_26_9", df, startswith=("MACD_",))
        df["MACD_Signal"] = IndicatorCalculator._frame_column_or_nan(macd, "MACDs_12_26_9", df, startswith=("MACDs_",))
        df["MACD_Hist"] = IndicatorCalculator._frame_column_or_nan(macd, "MACDh_12_26_9", df, startswith=("MACDh_",))

        df["ATR_14"] = IndicatorCalculator._series_or_nan(ta.atr(df["High"], df["Low"], df["Close"], length=14), df)
        bb = ta.bbands(df["Close"], length=20)
        df["BB_Upper"] = IndicatorCalculator._frame_column_or_nan(bb, "BBU_20_2.0", df, startswith=("BBU_",))
        df["BB_Lower"] = IndicatorCalculator._frame_column_or_nan(bb, "BBL_20_2.0", df, startswith=("BBL_",))
        df["BB_Width"] = IndicatorCalculator._frame_column_or_nan(bb, "BBB_20_2.0", df, startswith=("BBB_",))

        adx = ta.adx(df["High"], df["Low"], df["Close"], length=14)
        df["ADX"] = IndicatorCalculator._frame_column_or_nan(adx, "ADX_14", df, startswith=("ADX_",))
        df["DI_Plus"] = IndicatorCalculator._frame_column_or_nan(adx, "DMP_14", df, startswith=("DMP_",))
        df["DI_Minus"] = IndicatorCalculator._frame_column_or_nan(adx, "DMN_14", df, startswith=("DMN_",))
        supertrend = ta.supertrend(df["High"], df["Low"], df["Close"])
        df["Supertrend"] = IndicatorCalculator._frame_column_or_nan(supertrend, "SUPERT_7_3.0", df, startswith=("SUPERT_",))

        df["OBV"] = IndicatorCalculator._series_or_nan(ta.obv(df["Close"], df["Volume"]), df)
        df["VWAP"] = IndicatorCalculator._vwap_or_nan(df)
        df["Volume_SMA_20"] = IndicatorCalculator._series_or_nan(ta.sma(df["Volume"], length=20), df)
        df["ATR_20_AVG"] = df["ATR_14"].rolling(20).mean()
        df["BB_Width_Avg_20"] = df["BB_Width"].rolling(20).mean()
        df["High_20"] = df["High"].rolling(20).max()
        df["Low_20"] = df["Low"].rolling(20).min()
        df["High_63"] = df["High"].rolling(63).max()
        df["Low_63"] = df["Low"].rolling(63).min()

        if talib is not None:
            opens = df["Open"].values
            highs = df["High"].values
            lows = df["Low"].values
            closes = df["Close"].values
            df["HAMMER"] = talib.CDLHAMMER(opens, highs, lows, closes)
            df["ENGULFING"] = talib.CDLENGULFING(opens, highs, lows, closes)
            df["MORNING_STAR"] = talib.CDLMORNINGSTAR(opens, highs, lows, closes)
            df["EVENING_STAR"] = talib.CDLEVENINGSTAR(opens, highs, lows, closes)
            df["DOJI"] = talib.CDLDOJI(opens, highs, lows, closes)
            df["SHOOTING_ST"] = talib.CDLSHOOTINGSTAR(opens, highs, lows, closes)
            df["HAMMER_INV"] = talib.CDLINVERTEDHAMMER(opens, highs, lows, closes)
            df["PIERCING"] = talib.CDLPIERCING(opens, highs, lows, closes)
            df["DARK_CLOUD"] = talib.CDLDARKCLOUDCOVER(opens, highs, lows, closes)
            df["THREE_WHITE"] = talib.CDL3WHITESOLDIERS(opens, highs, lows, closes)
            df["THREE_BLACK"] = talib.CDL3BLACKCROWS(opens, highs, lows, closes)
        else:
            if not IndicatorCalculator._warned_talib_missing:
                logger.warning("TA-Lib is unavailable; using heuristic candlestick fallback patterns.")
                IndicatorCalculator._warned_talib_missing = True
            for col, values in IndicatorCalculator._candlestick_fallback(df).items():
                df[col] = values

        return df
