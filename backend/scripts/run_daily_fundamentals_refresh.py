from __future__ import annotations

import json

from backend.scheduler import TradingSchedulerService


def main() -> None:
    service = TradingSchedulerService()
    result = service.refresh_daily_fundamentals()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
