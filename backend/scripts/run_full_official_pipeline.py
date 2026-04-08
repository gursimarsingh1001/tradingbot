from __future__ import annotations

import json
import os
import traceback
from datetime import datetime

from backend.config import get_settings
from backend.db.postgres import session_scope, upsert_config_value
from backend.scheduler import TradingSchedulerService


def emit(event: str, **payload) -> None:
    message = {
        "ts": datetime.now().isoformat(),
        "event": event,
        **payload,
    }
    print(json.dumps(message, default=str), flush=True)


def main() -> int:
    settings = get_settings()
    scheduler = TradingSchedulerService()
    as_of_date = datetime.now(tz=settings.tzinfo).date()
    symbol_configs = scheduler.historical_fetcher.load_symbols()
    quote_batch_size = 50
    weekly_batch_size = 50
    force_screener_refresh = os.getenv("FORCE_SCREENER_REFRESH", "").strip().lower() in {"1", "true", "yes"}

    emit("start", as_of_date=as_of_date, universe_size=len(symbol_configs), force_screener_refresh=force_screener_refresh)

    quote_stored = 0
    quote_recovered_by_bse = 0
    quote_missing_bse_mappings: list[str] = []
    quote_failed_examples: dict[str, str] = {}
    for offset in range(0, len(symbol_configs), quote_batch_size):
        batch = symbol_configs[offset : offset + quote_batch_size]
        emit("quote_batch_start", batch_start=offset, batch_size=len(batch))
        batch_result = scheduler.official_investment_data_service.refresh_quote_snapshots(
            symbol_configs=batch,
            as_of_date=as_of_date,
        )
        quote_stored += int(batch_result.get("stored") or 0)
        quote_recovered_by_bse += int(batch_result.get("recovered_by_bse") or 0)
        quote_missing_bse_mappings.extend(list(batch_result.get("missing_bse_mappings") or []))
        quote_failed_examples.update(dict(batch_result.get("failed_examples") or {}))
        emit(
            "quote_batch_complete",
            batch_start=offset,
            batch_size=len(batch),
            stored=batch_result.get("stored"),
            recovered_by_bse=batch_result.get("recovered_by_bse"),
            failed=len(dict(batch_result.get("failed_examples") or {})),
        )

    quote = {
        "requested": len(symbol_configs),
        "stored": quote_stored,
        "recovered_by_bse": quote_recovered_by_bse,
        "missing_bse_mappings": quote_missing_bse_mappings,
        "failed_examples": dict(list(quote_failed_examples.items())[:10]),
        "as_of_date": as_of_date.isoformat(),
    }
    quote_rebuild = scheduler.official_snapshot_builder.rebuild_daily_snapshot(as_of_date=as_of_date)
    quote_summary = scheduler.shadow_comparison_service.compare(
        as_of_date=as_of_date,
        missing_bse_mapping_symbols=list(quote.get("missing_bse_mappings") or []),
        recovered_by_bse_count=int(quote.get("recovered_by_bse") or 0),
    )
    emit(
        "quote_complete",
        requested=quote.get("requested"),
        stored=quote.get("stored"),
        recovered_by_bse=quote.get("recovered_by_bse"),
        missing_bse_mappings=len(list(quote.get("missing_bse_mappings") or [])),
        rebuilt_snapshots=quote_rebuild.get("stored"),
        comparison_coverage=quote_summary.get("coverageCompared"),
    )

    with session_scope() as session:
        upsert_config_value(
            session,
            settings.official_shadow_weekly_state_key,
            {
                "lastRunAt": None,
                "lastOffset": 0,
                "nextOffset": 0,
                "lastRequested": 0,
                "lastProcessed": 0,
                "lastStoredPeriods": 0,
                "lastStoredShareholding": 0,
            },
        )

    if force_screener_refresh:
        for offset in range(0, len(symbol_configs), weekly_batch_size):
            batch = symbol_configs[offset : offset + weekly_batch_size]
            emit("screener_batch_start", batch_start=offset, batch_size=len(batch))
            screener_result = scheduler.official_investment_data_service.refresh_screener_cache(
                symbol_configs=batch,
                force_refresh=True,
            )
            emit(
                "screener_batch_complete",
                batch_start=offset,
                batch_size=len(batch),
                refreshed=screener_result.get("refreshed"),
                used_cached=screener_result.get("used_cached"),
                failed=len(dict(screener_result.get("failed_examples") or {})),
            )

    weekly_batches = 0
    while True:
        emit("weekly_batch_start", batch=weekly_batches + 1)
        weekly = scheduler.official_investment_data_service.refresh_weekly_fundamentals(
            symbol_configs=symbol_configs,
            batch_size=weekly_batch_size,
        )
        weekly_batches += 1
        next_offset = int(weekly.get("next_offset") or 0)
        emit(
            "weekly_batch_complete",
            batch=weekly_batches,
            requested=weekly.get("requested"),
            processed=weekly.get("processed"),
            stored_periods=weekly.get("stored_periods"),
            stored_shareholding=weekly.get("stored_shareholding"),
            recovered_by_bse=weekly.get("recovered_by_bse"),
            missing_bse_mappings=len(list(weekly.get("missing_bse_mappings") or [])),
            next_offset=next_offset,
            screener_refreshed=(weekly.get("screener_cache") or {}).get("refreshed"),
            board_meetings_stored=(weekly.get("earnings_calendar") or {}).get("stored"),
        )
        if next_offset == 0:
            break
        if weekly_batches >= 100:
            raise RuntimeError("Weekly full pipeline guard triggered after 100 batches.")

    actions = scheduler.official_investment_data_service.refresh_corporate_actions(
        symbol_configs=symbol_configs,
        as_of_date=as_of_date,
    )
    market_context = scheduler.official_investment_data_service.refresh_market_context(as_of_date=as_of_date)
    final_rebuild = scheduler.official_snapshot_builder.rebuild_daily_snapshot(as_of_date=as_of_date)
    scores = scheduler.refresh_official_investment_scores_shadow(as_of_date=as_of_date)
    cutover_summary: dict[str, object] = {}
    cutover_created = 0
    if settings.official_investment_cutover_enabled:
        scheduler.generate_after_market_investment_recommendations(as_of_date=as_of_date)
        cutover_summary = dict(scheduler._last_official_investment_cutover_summary or {})
        cutover_created = int(cutover_summary.get("created") or 0)
    final_summary = scheduler.shadow_comparison_service.compare(as_of_date=as_of_date)

    emit(
        "complete",
        as_of_date=as_of_date,
        corporate_actions_stored=actions.get("stored"),
        sector_context_count=market_context.get("sector_context_count"),
        rebuilt_snapshots=final_rebuild.get("stored"),
        strong_buy=scores.get("strong_buy"),
        watchlist=scores.get("watchlist"),
        no_action=scores.get("no_action"),
        phase3_buy=(scores.get("phase3") or {}).get("buy"),
        phase3_skip=(scores.get("phase3") or {}).get("skip"),
        cutover_created=cutover_created,
        global_risk_level=cutover_summary.get("global_risk_level"),
        position_size_multiplier=cutover_summary.get("position_size_multiplier"),
        official_coverage=final_summary.get("officialCoverage"),
        average_fill_rate=final_summary.get("averageFillRate"),
        symbols_below_80_fill=final_summary.get("symbolsBelow80FillRate"),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - operational script
        emit("error", error_type=type(exc).__name__, error=str(exc))
        traceback.print_exc()
        raise
