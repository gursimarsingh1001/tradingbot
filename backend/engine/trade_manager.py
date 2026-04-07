from __future__ import annotations

from backend.engine.paper_trader_v2 import PaperTrader


class TradeManager:
    def __init__(self, paper_trader: PaperTrader | None = None) -> None:
        self.paper_trader = paper_trader or PaperTrader()

    def process_price_update(self, latest_prices: dict[str, float]) -> None:
        self.paper_trader.update_trades(latest_prices)
