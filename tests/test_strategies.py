import pandas as pd

from backend.strategies.base_strategy import StrategyContext
from backend.strategies.ema_crossover import EMACrossoverStrategy
from backend.strategies.news_driven import NewsDrivenMomentumStrategy


def test_news_driven_momentum_uses_reachable_sentiment_threshold():
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.5, 103.0],
            "Low": [99.5, 100.5],
            "Close": [101.0, 102.4],
            "RSI_14": [52.0, 58.0],
            "EMA_20": [100.8, 101.7],
            "ATR_14": [1.0, 1.1],
        },
        index=pd.date_range("2026-01-01", periods=2, freq="D"),
    )

    signal = NewsDrivenMomentumStrategy().generate_signal(
        frame,
        context=StrategyContext(news_score=0.6),
    )

    assert signal["signal"] == "BUY"


def test_ema_crossover_sell_requires_bearish_trend_filter():
    frame = pd.DataFrame(
        {
            "Open": [100.0, 104.0],
            "High": [105.0, 105.5],
            "Low": [99.5, 101.0],
            "Close": [104.0, 103.0],
            "EMA_9": [101.0, 100.0],
            "EMA_20": [100.0, 101.0],
            "SMA_50": [98.0, 102.0],
            "ATR_14": [1.1, 1.2],
        },
        index=pd.date_range("2026-01-01", periods=2, freq="D"),
    )

    strategy = EMACrossoverStrategy()

    hold_signal = strategy.generate_signal(frame)
    assert hold_signal["signal"] == "HOLD"

    bearish_frame = frame.copy()
    bearish_frame.loc[bearish_frame.index[-1], "Close"] = 100.5
    bearish_signal = strategy.generate_signal(bearish_frame)

    assert bearish_signal["signal"] == "SELL"
