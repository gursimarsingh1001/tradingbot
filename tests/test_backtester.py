from backend.backtest.backtester import WalkForwardBacktester


def test_transaction_costs_are_signal_type_aware():
    intraday_cost = WalkForwardBacktester._transaction_costs(
        100.0,
        102.0,
        100,
        direction="BUY",
        signal_type="INTRADAY",
    )
    delivery_cost = WalkForwardBacktester._transaction_costs(
        100.0,
        102.0,
        100,
        direction="BUY",
        signal_type="INVESTMENT",
    )

    assert delivery_cost > intraday_cost


def test_transaction_costs_match_realistic_indian_intraday_range():
    intraday_cost = WalkForwardBacktester._transaction_costs(
        500.0,
        505.0,
        100,
        direction="BUY",
        signal_type="INTRADAY",
    )

    assert 40.0 <= intraday_cost <= 100.0
