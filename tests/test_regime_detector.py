import pandas as pd

from backend.engine.regime_detector import detect_regime, regime_is_ranging, regime_is_trending, regime_trend_direction


def test_detect_regime_identifies_early_bull_trend():
    index = pd.date_range("2026-01-01", periods=30, freq="D")
    frame = pd.DataFrame(
        {
            "Close": [100 + i for i in range(30)],
            "EMA_20": [98 + (i * 0.8) for i in range(30)],
            "SMA_50": [96 + (i * 0.6) for i in range(30)],
            "SMA_200": [94 + (i * 0.2) for i in range(30)],
            "ADX": [18.0] * 29 + [23.0],
            "ATR_14": [2.0] * 30,
            "ATR_20_AVG": [2.0] * 30,
        },
        index=index,
    )

    regime = detect_regime(frame)

    assert regime == "TRENDING_BULL_EARLY"
    assert regime_is_trending(regime)
    assert regime_trend_direction(regime) == "BULL"


def test_detect_regime_identifies_mature_bear_trend():
    index = pd.date_range("2026-01-01", periods=30, freq="D")
    frame = pd.DataFrame(
        {
            "Close": [200 - i for i in range(30)],
            "EMA_20": [201 - (i * 0.8) for i in range(30)],
            "SMA_50": [203 - (i * 0.9) for i in range(30)],
            "SMA_200": [205 - (i * 0.3) for i in range(30)],
            "ADX": [24.0] * 29 + [34.0],
            "ATR_14": [2.2] * 30,
            "ATR_20_AVG": [2.0] * 30,
        },
        index=index,
    )

    regime = detect_regime(frame)

    assert regime == "TRENDING_BEAR_MATURE"
    assert regime_is_trending(regime)
    assert regime_trend_direction(regime) == "BEAR"


def test_detect_regime_falls_back_to_ranging_without_trend_alignment():
    index = pd.date_range("2026-01-01", periods=30, freq="D")
    frame = pd.DataFrame(
        {
            "Close": [100.0 + ((-1) ** i) for i in range(30)],
            "EMA_20": [100.0] * 30,
            "SMA_50": [100.0] * 30,
            "SMA_200": [100.0] * 30,
            "ADX": [15.0] * 30,
            "ATR_14": [1.5] * 30,
            "ATR_20_AVG": [1.5] * 30,
        },
        index=index,
    )

    regime = detect_regime(frame)

    assert regime_is_ranging(regime)
