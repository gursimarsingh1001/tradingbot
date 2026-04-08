from __future__ import annotations

from datetime import date

from backend.engine.data_reconciler import DataReconciler


def test_reconcile_single_source_and_fill_rate():
    reconciler = DataReconciler(tolerance=0.10)

    result = reconciler.reconcile_fields(
        {
            "pe_ratio": [{"source": "OFFICIAL_QUOTE", "value": 18.5}],
            "dividend_yield": [{"source": "OFFICIAL_QUOTE", "value": None}],
        }
    )

    assert result.values["pe_ratio"] == 18.5
    assert result.fields["pe_ratio"].confidence == "SINGLE"
    assert result.values["dividend_yield"] is None
    assert result.fill_rate == 0.5


def test_reconcile_two_source_agreement_and_mismatch():
    reconciler = DataReconciler(tolerance=0.10)

    agreed = reconciler.reconcile_field(
        "roe",
        [
            {"source": "OFFICIAL_PERIOD", "value": 20.0},
            {"source": "SCREENER", "value": 19.0},
        ],
    )
    mismatched = reconciler.reconcile_field(
        "debt_to_equity",
        [
            {"source": "OFFICIAL_PERIOD", "value": 0.2},
            {"source": "SCREENER", "value": 0.8},
        ],
    )

    assert agreed.confidence == "HIGH"
    assert agreed.mismatch is False
    assert mismatched.confidence == "MEDIUM"
    assert mismatched.mismatch is True


def test_gap_fill_and_earnings_date_priority():
    reconciler = DataReconciler(tolerance=0.10)

    result = reconciler.reconcile_fields(
        {
            "pb_ratio": [
                {"source": "OFFICIAL_QUOTE", "value": None},
                {"source": "SCREENER", "value": 3.2},
            ],
            "earnings_date": [
                {"source": "BSE_BOARD_MEETING", "value": date.fromisoformat("2026-04-25")},
                {"source": "MONEYCONTROL", "value": date.fromisoformat("2026-04-26")},
                {"source": "OFFICIAL_PERIOD", "value": date.fromisoformat("2026-05-01")},
            ],
        }
    )

    assert result.values["pb_ratio"] == 3.2
    assert result.fields["pb_ratio"].selected_source == "SCREENER"
    assert result.values["earnings_date"] == date.fromisoformat("2026-04-25")
    assert result.fields["earnings_date"].selected_source == "BSE_BOARD_MEETING"
