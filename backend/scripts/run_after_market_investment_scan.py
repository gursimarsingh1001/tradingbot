from __future__ import annotations

from backend.scheduler import TradingSchedulerService


def main() -> None:
    service = TradingSchedulerService()
    recommendations = service.generate_after_market_investment_recommendations(
        universe_limit=service.INVESTMENT_UNIVERSE_LIMIT,
        top_n=10,
    )
    payload = {
        "count": len(recommendations),
        "symbols": [item["stock_symbol"] for item in recommendations],
    }
    print(payload)


if __name__ == "__main__":
    main()
