from __future__ import annotations

import argparse

from backend.scheduler import TradingSchedulerService


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill market news for the selected NSE universe.")
    parser.add_argument("--limit", type=int, default=0, help="Number of stocks to process; 0 means all loaded symbols")
    parser.add_argument("--lookback-hours", type=int, default=168, help="How many recent hours of news to fetch")
    parser.add_argument("--batch-size", type=int, default=25, help="Batch size for sequential sync")
    args = parser.parse_args()

    service = TradingSchedulerService()
    universe_limit = args.limit if args.limit > 0 else None
    universe = service.historical_fetcher.select_symbols(limit=universe_limit)
    processed = 0
    inserted = 0
    errors: list[str] = []
    total = len(universe)
    for offset in range(0, total, args.batch_size):
        batch = universe[offset : offset + args.batch_size]
        result = service.sync_news_for_symbols(
            batch,
            lookback_hours=args.lookback_hours,
            max_symbols=len(batch),
        )
        processed += int(result["processed"])
        inserted += int(result["inserted"])
        errors.extend(result["errors"])
        print(
            f"Batch {offset // args.batch_size + 1}: processed {processed}/{total}, "
            f"inserted {inserted}, batchErrors={len(result['errors'])}"
        )
    print(
        "Full news sync completed:",
        {
            "processed": processed,
            "inserted": inserted,
            "errors": errors[:10],
        },
    )


if __name__ == "__main__":
    main()
