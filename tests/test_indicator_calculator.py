import pandas as pd

from backend.data.indicator_calculator import IndicatorCalculator


def test_candlestick_fallback_detects_hammer_and_three_white():
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, 101.8, 102.5],
            "High": [101.5, 102.2, 103.1, 102.0, 103.4],
            "Low": [99.6, 100.4, 101.6, 99.8, 102.2],
            "Close": [101.0, 102.0, 103.0, 101.9, 103.2],
        }
    )

    patterns = IndicatorCalculator._candlestick_fallback(frame)

    assert int(patterns["HAMMER"].iloc[3]) > 0
    assert int(patterns["THREE_WHITE"].iloc[2]) > 0


def test_candlestick_fallback_detects_bearish_engulfing():
    frame = pd.DataFrame(
        {
            "Open": [100.0, 103.0],
            "High": [102.5, 103.5],
            "Low": [99.5, 98.5],
            "Close": [102.0, 99.0],
        }
    )

    patterns = IndicatorCalculator._candlestick_fallback(frame)

    assert int(patterns["ENGULFING"].iloc[1]) < 0
