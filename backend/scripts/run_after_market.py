from backend.scheduler import TradingSchedulerService


def main() -> None:
    TradingSchedulerService().after_market_analysis()
    print("done")


if __name__ == "__main__":
    main()
