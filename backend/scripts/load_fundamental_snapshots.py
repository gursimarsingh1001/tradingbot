from __future__ import annotations

import argparse
import json

from backend.data.fundamentals_fetcher import StockAnalysisFundamentalsFetcher
from backend.engine.fundamental_engine import FundamentalEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Load structured fundamental snapshots into config JSON and PostgreSQL.")
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--skip-db-sync", action="store_true")
    args = parser.parse_args()

    fetcher = StockAnalysisFundamentalsFetcher()
    result = fetcher.load_and_write(limit=args.limit, offset=args.offset, workers=args.workers)
    synced = 0
    if not args.skip_db_sync:
        synced = FundamentalEngine().sync_from_config()
    print(
        json.dumps(
            {
                **result,
                "synced_to_db": synced,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
