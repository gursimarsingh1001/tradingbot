from backend.scheduler import TradingSchedulerService


def main() -> None:
    service = TradingSchedulerService()
    service._holiday_reason = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    service._within_after_market_window = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    service.after_market_analysis()
    print("watchlist rebuilt")


if __name__ == "__main__":
    main()
