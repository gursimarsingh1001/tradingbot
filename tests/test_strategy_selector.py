import math

import pandas as pd

from backend.backtest.strategy_selector import StrategySelector


def test_composite_score_is_bounded_and_trade_count_sensitive():
    selector = StrategySelector()
    sample_returns = pd.Series([0.03, -0.01, 0.04, -0.01, 0.02], dtype=float)

    low_trade_score, _, _, _ = selector._composite_score(
        total_return=float((1 + sample_returns).prod() - 1),
        sharpe=1.2,
        max_drawdown=-0.02,
        returns=sample_returns,
        trade_count=5,
    )
    high_trade_score, _, _, _ = selector._composite_score(
        total_return=float((1 + sample_returns).prod() - 1),
        sharpe=1.2,
        max_drawdown=-0.02,
        returns=sample_returns,
        trade_count=25,
    )

    assert 0.0 <= low_trade_score <= 1.0
    assert 0.0 <= high_trade_score <= 1.0
    assert math.isfinite(low_trade_score)
    assert math.isfinite(high_trade_score)
    assert high_trade_score > low_trade_score
