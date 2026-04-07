from backend.engine.signal_engine import SignalEngine


def test_bearish_buy_penalty_softens_longs_instead_of_blocking():
    adjusted, reasons = SignalEngine._apply_bearish_buy_penalty(
        74.0,
        direction="BUY",
        signal_type="INTRADAY",
        regime="TRENDING_BEAR_EARLY",
        combined_news_score=0.1,
    )

    assert adjusted < 74.0
    assert adjusted > 50.0
    assert reasons


def test_bearish_buy_penalty_is_reduced_by_strong_positive_news():
    plain_adjusted, _ = SignalEngine._apply_bearish_buy_penalty(
        74.0,
        direction="BUY",
        signal_type="INTRADAY",
        regime="TRENDING_BEAR_EARLY",
        combined_news_score=0.1,
    )
    news_adjusted, reasons = SignalEngine._apply_bearish_buy_penalty(
        74.0,
        direction="BUY",
        signal_type="INTRADAY",
        regime="TRENDING_BEAR_EARLY",
        combined_news_score=1.2,
        event_flags=["Fresh results catalyst"],
    )

    assert news_adjusted > plain_adjusted
    assert any("positive" in reason.lower() or "catalyst" in reason.lower() for reason in reasons)
