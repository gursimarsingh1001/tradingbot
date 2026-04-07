from backend.engine.event_risk_engine import extract_financial_catalyst


def test_extract_financial_catalyst_detects_profit_and_revenue_surge():
    catalyst = extract_financial_catalyst(
        "Q4 results: net profit jumped 112 percent while revenue rose 28 percent and margin expands sharply."
    )

    assert catalyst.is_positive
    assert catalyst.results_context
    assert catalyst.profit_growth_pct == 112.0
    assert catalyst.revenue_growth_pct == 28.0
    assert "Fresh results catalyst" in catalyst.flags


def test_extract_financial_catalyst_stays_neutral_without_results_context():
    catalyst = extract_financial_catalyst("The company won a new order and shares were active in trade today.")

    assert not catalyst.is_positive
    assert catalyst.score == 0.0
