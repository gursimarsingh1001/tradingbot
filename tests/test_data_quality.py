from datetime import datetime, timedelta

import pandas as pd

from backend.data.data_quality import (
    sanitize_fundamental_snapshot,
    sanitize_news_timestamp,
    validate_ohlcv_frame,
    validate_quote_snapshot,
)


def test_validate_ohlcv_frame_drops_invalid_rows():
    frame = pd.DataFrame(
        {
            "Open": [100, 105],
            "High": [110, 101],
            "Low": [95, 102],
            "Close": [108, 103],
            "Volume": [1000, 1200],
        },
        index=pd.to_datetime(["2026-04-01", "2026-04-02"]),
    )

    validated = validate_ohlcv_frame(frame)

    assert len(validated) == 1
    assert float(validated.iloc[0]["Close"]) == 108


def test_validate_quote_snapshot_rejects_absurd_move():
    assert not validate_quote_snapshot(ltp=150.0, close=100.0, cached_ltp=100.0)
    assert validate_quote_snapshot(ltp=101.5, close=100.0, cached_ltp=100.5)


def test_sanitize_fundamental_snapshot_rejects_out_of_range_values():
    payload = {
        "symbol": "TEST",
        "asOfDate": "2026-04-01",
        "peRatio": 12.0,
        "pbRatio": 2.0,
        "roe": 25.0,
        "debtToEquity": 9999.0,
        "currentRatio": 1.5,
    }

    cleaned = sanitize_fundamental_snapshot(payload)

    assert cleaned is not None
    assert cleaned["debtToEquity"] is None
    assert cleaned["peRatio"] == 12.0


def test_sanitize_news_timestamp_rejects_future_timestamp():
    future = datetime.now().replace(microsecond=0) + timedelta(days=2)
    assert sanitize_news_timestamp(future.isoformat()) is None
