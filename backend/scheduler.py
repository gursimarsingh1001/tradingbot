from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
from threading import Thread

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, EVENT_SCHEDULER_SHUTDOWN, EVENT_SCHEDULER_STARTED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, func, select

from backend.config import get_settings
from backend.data.fundamentals_fetcher import StockAnalysisFundamentalsFetcher
from backend.data.historical_fetcher import HistoricalFetcher, PREFERRED_BATCH_SYMBOLS, SymbolConfig
from backend.data.indicator_calculator import IndicatorCalculator
from backend.data.news_fetcher import NewsFetcher
from backend.db.postgres import (
    Notification,
    PaperTrade,
    StockStrategyMap,
    TomorrowWatchlist,
    add_notification,
    get_config_value,
    session_scope,
    upsert_config_value,
)
from backend.engine.learning_engine import LearningEngine
from backend.engine.live_intraday_service import get_live_intraday_service
from backend.engine.market_calendar import get_market_calendar
from backend.engine.market_data_service import get_market_data_service
from backend.engine.alert_dispatcher import AlertDispatcher
from backend.engine.backup_service import BackupService
from backend.engine.daily_report_service import DailyReportService
from backend.engine.fundamental_engine import FundamentalEngine
from backend.engine.global_risk_scanner import GlobalRiskScanner
from backend.engine.investment_gate_runner import InvestmentGateRunner
from backend.engine.official_investment_recommendation_engine import OfficialInvestmentRecommendationEngine
from backend.engine.investment_scorer import InvestmentScorer
from backend.engine.official_investment_data_service import OfficialInvestmentDataService
from backend.engine.official_snapshot_builder import OfficialSnapshotBuilder
from backend.engine.paper_trader_v2 import PaperTrader
from backend.engine.regime_detector import detect_regime
from backend.engine.signal_engine import SignalEngine
from backend.engine.shadow_comparison import ShadowComparisonService
from backend.logging_utils import get_logger
from backend.strategies.base_strategy import StrategyContext


settings = get_settings()
logger = get_logger(__name__)


class TradingSchedulerService:
    INTRADAY_UNIVERSE_LIMIT = settings.intraday_universe_limit
    MIN_CONFIDENCE_SCORE = settings.signal_min_confidence
    DEFAULT_CONFIDENCE_SCORE = settings.default_recommendation_confidence
    ENTRY_ZONE_BUFFER_PCT = settings.watchlist_entry_zone_buffer_pct
    NEWS_PRIORITY_SYNC_LIMIT = 60
    NEWS_PRIORITY_SYNC_LOOKBACK_HOURS = 24
    INVESTMENT_UNIVERSE_LIMIT = 900
    INVESTMENT_MIN_PRICE = 40.0
    INVESTMENT_MIN_AVG_VOLUME = 100_000.0
    INVESTMENT_MIN_AVG_TURNOVER = 10_000_000.0
    FUNDAMENTALS_PRIORITY_LIMIT = 180
    FUNDAMENTALS_DAILY_BATCH_SIZE = 10_000
    FUNDAMENTALS_WORKERS = 8
    INVESTMENT_STRATEGY_CANDIDATES = (
        "Golden Cross",
        "EMA Crossover",
        "Breakout with Volume",
        "Supertrend",
        "MACD Momentum",
        "Support and Resistance",
        "News-Driven Momentum",
        "Combined Regime-Aware",
        "RSI Mean Reversion",
    )
    INVESTMENT_STRATEGY_BONUS = {
        "Golden Cross": 8.0,
        "Breakout with Volume": 7.0,
        "EMA Crossover": 6.0,
        "Supertrend": 5.0,
        "MACD Momentum": 4.5,
        "Support and Resistance": 3.0,
        "News-Driven Momentum": 3.0,
        "Combined Regime-Aware": 3.0,
        "RSI Mean Reversion": 1.0,
    }

    def __init__(self) -> None:
        self.market_scheduler = BackgroundScheduler(timezone=settings.timezone, job_defaults={"coalesce": True, "max_instances": 1})
        self.after_market_scheduler = BackgroundScheduler(
            timezone=settings.timezone,
            job_defaults={"coalesce": True, "max_instances": 1},
        )
        self.learning_engine = LearningEngine()
        self.fundamental_engine = FundamentalEngine()
        self.fundamental_engine.sync_from_config()
        self.fundamentals_fetcher = StockAnalysisFundamentalsFetcher()
        self.historical_fetcher = HistoricalFetcher()
        self.official_investment_data_service = OfficialInvestmentDataService(
            historical_fetcher=self.historical_fetcher,
        )
        self.official_snapshot_builder = OfficialSnapshotBuilder(
            data_service=self.official_investment_data_service,
        )
        self.shadow_comparison_service = ShadowComparisonService(
            data_service=self.official_investment_data_service,
        )
        self.investment_scorer = InvestmentScorer(
            historical_fetcher=self.historical_fetcher,
        )
        self.investment_gate_runner = InvestmentGateRunner(
            historical_fetcher=self.historical_fetcher,
        )
        self.global_risk_scanner = GlobalRiskScanner(
            historical_fetcher=self.historical_fetcher,
        )
        self.news_fetcher = NewsFetcher()
        self.signal_engine = SignalEngine()
        self.paper_trader = PaperTrader()
        self.official_investment_recommendation_engine = OfficialInvestmentRecommendationEngine(
            historical_fetcher=self.historical_fetcher,
            paper_trader=self.paper_trader,
            gate_runner=self.investment_gate_runner,
            risk_scanner=self.global_risk_scanner,
        )
        self.market_calendar = get_market_calendar()
        self.market_data_service = get_market_data_service()
        self.live_intraday_service = get_live_intraday_service()
        self.alert_dispatcher = AlertDispatcher()
        self.backup_service = BackupService()
        self.daily_report_service = DailyReportService()
        self._last_investment_scan_error_count = 0
        self._last_investment_scan_error_examples: list[str] = []
        self._last_official_investment_cutover_summary: dict[str, object] = {}
        self._full_news_sync_thread: Thread | None = None
        self._after_market_catchup_thread: Thread | None = None
        self._scheduler_health: dict[str, dict[str, str | None]] = {
            "market": {"status": "initialized", "last_event_at": None, "last_error": None},
            "after_market": {"status": "initialized", "last_event_at": None, "last_error": None},
        }
        self._configured = False
        self._attach_scheduler_listeners()

    @staticmethod
    def _parse_clock(value: str) -> dt_time:
        return dt_time.fromisoformat(value)

    def _within_intraday_window(self, moment: datetime) -> bool:
        current = moment.timetz().replace(tzinfo=None)
        return self._parse_clock(settings.market_open_time) <= current <= self._parse_clock(settings.market_close_time)

    def _within_after_market_window(self, moment: datetime) -> bool:
        current = moment.timetz().replace(tzinfo=None)
        return self._parse_clock(settings.after_market_start) <= current <= self._parse_clock(settings.after_market_end)

    def _within_intraday_entry_window(self, moment: datetime) -> bool:
        current = moment.timetz().replace(tzinfo=None)
        return self._parse_clock(settings.market_open_time) <= current < self._parse_clock(settings.intraday_entry_cutoff_time)

    @staticmethod
    def _capture_error(error_list: list[str], *, symbol: str, exc: Exception) -> None:
        message = f"{symbol}: {type(exc).__name__}: {exc}"
        logger.warning("[scheduler] %s", message)
        if len(error_list) < 5:
            error_list.append(message)

    def _attach_scheduler_listeners(self) -> None:
        self.market_scheduler.add_listener(
            lambda event: self._handle_scheduler_event("market", event),
            EVENT_SCHEDULER_STARTED | EVENT_SCHEDULER_SHUTDOWN | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
        )
        self.after_market_scheduler.add_listener(
            lambda event: self._handle_scheduler_event("after_market", event),
            EVENT_SCHEDULER_STARTED | EVENT_SCHEDULER_SHUTDOWN | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
        )

    def _handle_scheduler_event(self, scheduler_name: str, event) -> None:
        state = self._scheduler_health.setdefault(
            scheduler_name,
            {"status": "initialized", "last_event_at": None, "last_error": None},
        )
        state["last_event_at"] = datetime.now(tz=settings.tzinfo).isoformat()
        if event.code == EVENT_SCHEDULER_STARTED:
            state["status"] = "running"
            state["last_error"] = None
            logger.info("Scheduler %s started", scheduler_name)
        elif event.code == EVENT_SCHEDULER_SHUTDOWN:
            state["status"] = "stopped"
            logger.warning("Scheduler %s stopped", scheduler_name)
        elif event.code == EVENT_JOB_MISSED:
            state["status"] = "degraded"
            state["last_error"] = getattr(event, "job_id", "unknown-job")
            logger.warning("Scheduler %s missed job %s", scheduler_name, getattr(event, "job_id", "unknown-job"))
        elif event.code == EVENT_JOB_ERROR:
            state["status"] = "degraded"
            state["last_error"] = str(getattr(event, "exception", "unknown error"))
            logger.error(
                "Scheduler %s job %s failed: %s",
                scheduler_name,
                getattr(event, "job_id", "unknown-job"),
                getattr(event, "exception", "unknown error"),
            )

    def _holiday_reason(self, trading_day: date) -> str | None:
        return self.market_calendar.closure_reason(trading_day)

    @staticmethod
    def _today_local() -> date:
        return datetime.now(tz=settings.tzinfo).date()

    def _notify_holiday_skip(self, *, title: str, body: str) -> None:
        with session_scope() as session:
            existing = session.scalar(
                select(Notification).where(
                    Notification.type == "MARKET_HOLIDAY",
                    Notification.title == title,
                    func.date(Notification.created_at) == self._today_local(),
                )
            )
            if existing is not None:
                return
            add_notification(
                session,
                notification_type="MARKET_HOLIDAY",
                title=title,
                body=body,
                color="orange",
            )

    def _tracked_symbols_for_price_updates(self) -> list[str]:
        with session_scope() as session:
            trades = session.scalars(select(PaperTrade).where(PaperTrade.exit_date.is_(None))).all()
        symbols = sorted({trade.stock_symbol for trade in trades if trade.stock_symbol})
        return symbols

    def _news_priority_symbols(self, *, limit: int = 40) -> list[SymbolConfig]:
        symbol_map = self.historical_fetcher.load_symbol_map()
        prioritized: list[str] = []
        with session_scope() as session:
            watchlist_symbols = [
                symbol
                for symbol in session.scalars(
                    select(TomorrowWatchlist.symbol)
                    .where(TomorrowWatchlist.created_date >= self._today_local())
                    .order_by(TomorrowWatchlist.created_at.desc())
                ).all()
                if symbol
            ]
        prioritized.extend(watchlist_symbols)
        prioritized.extend(self._intraday_watchlist_symbols())
        prioritized.extend(self._tracked_symbols_for_price_updates())
        prioritized.extend(
            self.news_fetcher.recent_intraday_catalyst_symbols(
                as_of=datetime.now(tz=settings.tzinfo),
                limit=max(4, min(settings.news_intraday_catalyst_limit, limit // 2)),
            )
        )
        prioritized.extend([config.symbol for config in self.historical_fetcher.select_symbols(limit=limit)])

        selected: list[SymbolConfig] = []
        seen: set[str] = set()
        for symbol in prioritized:
            normalized = symbol.upper()
            if normalized in seen:
                continue
            config = symbol_map.get(normalized)
            if config is None:
                continue
            selected.append(config)
            seen.add(normalized)
            if len(selected) >= limit:
                break
        return selected

    def sync_news_for_symbols(
        self,
        symbol_configs: list[SymbolConfig],
        *,
        lookback_hours: int = 72,
        max_symbols: int | None = None,
    ) -> dict[str, int | list[str]]:
        selected = symbol_configs[:max_symbols] if max_symbols is not None else symbol_configs
        now = datetime.now(tz=settings.tzinfo)
        from_date = now - timedelta(hours=lookback_hours)
        inserted = 0
        processed = 0
        errors: list[str] = []
        for config in selected:
            try:
                inserted += self.news_fetcher.fetch_and_store_symbol_news(
                    symbol=config.symbol,
                    company_name=config.company_name,
                    from_date=from_date,
                    to_date=now,
                )
                processed += 1
            except Exception as exc:
                self._capture_error(errors, symbol=config.symbol, exc=exc)
        return {"processed": processed, "inserted": inserted, "errors": errors[:5]}

    def _load_news_sync_state(self) -> dict:
        with session_scope() as session:
            return get_config_value(
                session,
                "news_sync_state",
                {
                    "lastRunAt": None,
                    "lastProcessed": 0,
                    "lastInserted": 0,
                    "lastLookbackHours": 0,
                    "prioritySymbols": [],
                    "errors": [],
                },
            )

    def _store_news_sync_state(self, payload: dict) -> None:
        with session_scope() as session:
            upsert_config_value(session, "news_sync_state", payload)

    def refresh_priority_news(
        self,
        *,
        limit: int | None = None,
        lookback_hours: int | None = None,
    ) -> dict[str, object]:
        limit = limit or self.NEWS_PRIORITY_SYNC_LIMIT
        lookback_hours = lookback_hours or self.NEWS_PRIORITY_SYNC_LOOKBACK_HOURS
        symbol_configs = self._news_priority_symbols(limit=limit)
        result = self.sync_news_for_symbols(symbol_configs, lookback_hours=lookback_hours, max_symbols=len(symbol_configs))
        self._store_news_sync_state(
            {
                "lastRunAt": datetime.now(tz=settings.tzinfo).isoformat(),
                "lastProcessed": int(result["processed"]),
                "lastInserted": int(result["inserted"]),
                "lastLookbackHours": int(lookback_hours),
                "prioritySymbols": [config.symbol for config in symbol_configs[:25]],
                "errors": list(result["errors"])[:5],
            }
        )
        return {
            **result,
            "priority_symbols": [config.symbol for config in symbol_configs],
        }

    def sync_news_for_universe(
        self,
        *,
        limit: int | None = None,
        lookback_hours: int = 72,
        batch_size: int = 25,
    ) -> dict[str, int | list[str]]:
        universe = self.historical_fetcher.select_symbols(limit=limit)
        processed = 0
        inserted = 0
        errors: list[str] = []
        for offset in range(0, len(universe), batch_size):
            batch = universe[offset : offset + batch_size]
            result = self.sync_news_for_symbols(batch, lookback_hours=lookback_hours, max_symbols=len(batch))
            processed += int(result["processed"])
            inserted += int(result["inserted"])
            errors.extend(result["errors"])
        return {
            "processed": processed,
            "inserted": inserted,
            "errors": errors[:10],
        }

    def start_full_universe_news_sync(
        self,
        *,
        limit: int | None = None,
        lookback_hours: int = 72,
        batch_size: int = 25,
    ) -> bool:
        if self._full_news_sync_thread is not None and self._full_news_sync_thread.is_alive():
            return False

        def runner() -> None:
            with session_scope() as session:
                add_notification(
                    session,
                    notification_type="NEWS_SYNC",
                    title="Full news sync started",
                    body=(
                        f"Syncing news across "
                        f"{limit if limit is not None else len(self.historical_fetcher.load_symbols())} stocks "
                        f"for the last {lookback_hours} hours."
                    ),
                    color="blue",
                )
            try:
                result = self.sync_news_for_universe(
                    limit=limit,
                    lookback_hours=lookback_hours,
                    batch_size=batch_size,
                )
                error_suffix = ""
                if result["errors"]:
                    error_suffix = f" Examples: {'; '.join(result['errors'][:3])}."
                with session_scope() as session:
                    add_notification(
                        session,
                        notification_type="NEWS_SYNC",
                        title="Full news sync completed",
                        body=(
                            f"Processed {result['processed']} stocks and stored {result['inserted']} articles."
                            f"{error_suffix}"
                        ),
                        color="blue",
                    )
            except Exception as exc:
                with session_scope() as session:
                    add_notification(
                        session,
                        notification_type="NEWS_SYNC",
                        title="Full news sync failed",
                        body=f"{type(exc).__name__}: {exc}",
                        color="red",
                    )

        self._full_news_sync_thread = Thread(target=runner, daemon=True, name="full-universe-news-sync")
        self._full_news_sync_thread.start()
        return True

    def _fundamental_priority_symbols(self, *, limit: int | None = None) -> list[SymbolConfig]:
        limit = limit or self.FUNDAMENTALS_PRIORITY_LIMIT
        symbol_map = self.historical_fetcher.load_symbol_map()
        prioritized: list[str] = []
        prioritized.extend(PREFERRED_BATCH_SYMBOLS)
        prioritized.extend(self._tracked_symbols_for_price_updates())
        prioritized.extend(self._intraday_watchlist_symbols())
        with session_scope() as session:
            watchlist_symbols = [
                symbol
                for symbol in session.scalars(
                    select(TomorrowWatchlist.symbol)
                    .where(TomorrowWatchlist.created_date >= self._today_local() - timedelta(days=3))
                    .order_by(TomorrowWatchlist.created_at.desc())
                    .limit(limit)
                ).all()
                if symbol
            ]
            strategy_symbols = [
                symbol
                for symbol in session.scalars(
                    select(StockStrategyMap.symbol)
                    .where(StockStrategyMap.best_strategy.is_not(None))
                    .order_by(StockStrategyMap.composite_score.desc().nullslast())
                    .limit(limit * 2)
                ).all()
                if symbol
            ]
        prioritized.extend(watchlist_symbols)
        prioritized.extend(strategy_symbols)
        prioritized.extend([config.symbol for config in self.historical_fetcher.select_symbols(limit=limit * 2)])

        selected: list[SymbolConfig] = []
        seen: set[str] = set()
        for symbol in prioritized:
            normalized = str(symbol or "").upper()
            if not normalized or normalized in seen:
                continue
            config = symbol_map.get(normalized)
            if config is None or not self.historical_fetcher.is_backtest_candidate(config):
                continue
            selected.append(config)
            seen.add(normalized)
            if len(selected) >= limit:
                break
        return selected

    def _load_fundamentals_sync_state(self) -> dict:
        with session_scope() as session:
            return get_config_value(
                session,
                "fundamentals_sync_state",
                {
                    "lastRunAt": None,
                    "lastOffset": 0,
                    "nextOffset": 0,
                    "lastRequested": 0,
                    "lastLoaded": 0,
                    "lastFailed": 0,
                    "totalUniverse": 0,
                    "prioritySymbols": [],
                    "rollingSymbols": [],
                    "failedExamples": {},
                },
            )

    def _store_fundamentals_sync_state(self, payload: dict) -> None:
        with session_scope() as session:
            upsert_config_value(session, "fundamentals_sync_state", payload)

    def _rolling_fundamental_symbol_batch(self, *, batch_size: int | None = None) -> tuple[list[SymbolConfig], dict[str, int]]:
        batch_size = batch_size or self.FUNDAMENTALS_DAILY_BATCH_SIZE
        universe = self.historical_fetcher.select_symbols(limit=None)
        total = len(universe)
        if total == 0:
            return [], {"last_offset": 0, "next_offset": 0, "total": 0}
        state = self._load_fundamentals_sync_state()
        last_offset = int(state.get("nextOffset") or 0)
        if last_offset >= total:
            last_offset = 0
        batch = universe[last_offset : last_offset + batch_size]
        if not batch:
            last_offset = 0
            batch = universe[:batch_size]
        next_offset = last_offset + len(batch)
        if next_offset >= total:
            next_offset = 0
        return batch, {"last_offset": last_offset, "next_offset": next_offset, "total": total}

    def refresh_daily_fundamentals(
        self,
        *,
        priority_limit: int | None = None,
        rolling_batch_size: int | None = None,
        workers: int | None = None,
    ) -> dict[str, object]:
        priority_limit = priority_limit or self.FUNDAMENTALS_PRIORITY_LIMIT
        rolling_batch_size = rolling_batch_size or self.FUNDAMENTALS_DAILY_BATCH_SIZE
        workers = workers or self.FUNDAMENTALS_WORKERS

        priority_configs = self._fundamental_priority_symbols(limit=priority_limit)
        rolling_configs, rolling_state = self._rolling_fundamental_symbol_batch(batch_size=rolling_batch_size)

        merged_configs: list[SymbolConfig] = []
        seen: set[str] = set()
        for config in [*priority_configs, *rolling_configs]:
            normalized = config.symbol.upper()
            if normalized in seen:
                continue
            seen.add(normalized)
            merged_configs.append(config)

        result = self.fundamentals_fetcher.load_and_write_for_symbol_configs(merged_configs, workers=workers)
        synced_to_db = self.fundamental_engine.sync_from_config()
        self.signal_engine.intelligence_engine.fundamental_engine.invalidate_cache()

        state_payload = {
            "lastRunAt": datetime.now(tz=settings.tzinfo).isoformat(),
            "lastOffset": rolling_state["last_offset"],
            "nextOffset": rolling_state["next_offset"],
            "lastRequested": int(result["total_requested"]),
            "lastLoaded": int(result["loaded"]),
            "lastFailed": int(result["failed"]),
            "syncedToDb": synced_to_db,
            "totalUniverse": rolling_state["total"],
            "prioritySymbols": [config.symbol for config in priority_configs[:25]],
            "rollingSymbols": [config.symbol for config in rolling_configs[:25]],
            "failedExamples": result["failed_examples"],
        }
        self._store_fundamentals_sync_state(state_payload)

        with session_scope() as session:
            add_notification(
                session,
                notification_type="FUNDAMENTALS_SYNC",
                title="Daily fundamentals refresh completed",
                body=(
                    f"Requested {result['total_requested']} stocks, refreshed {result['loaded']} snapshots, "
                    f"failed {result['failed']}, synced {synced_to_db} rows into the fundamentals store. "
                    f"Next rolling offset: {rolling_state['next_offset']} of {rolling_state['total']}."
                ),
                color="blue",
            )

        return {
            **result,
            "synced_to_db": synced_to_db,
            "next_offset": rolling_state["next_offset"],
            "rolling_total": rolling_state["total"],
            "priority_count": len(priority_configs),
            "rolling_count": len(rolling_configs),
        }

    def _intraday_watchlist_symbols(self) -> list[str]:
        with session_scope() as session:
            trades = session.scalars(
                select(PaperTrade).where(
                    PaperTrade.signal_type == "INTRADAY",
                    PaperTrade.exit_date.is_(None),
                )
            ).all()
        symbols: set[str] = set()
        for trade in trades:
            if not trade.stock_symbol:
                continue
            metadata = trade.metadata_json or {}
            if metadata.get("plan_only") and trade.entry_date and trade.entry_date > self._today_local():
                continue
            symbols.add(trade.stock_symbol)
        return sorted(symbols)

    def _intraday_candidate_symbols(self, *, limit: int | None = None) -> list[str]:
        limit = limit or self.INTRADAY_UNIVERSE_LIMIT
        prioritized: list[str] = []
        prioritized.extend(self._tracked_symbols_for_price_updates())
        prioritized.extend(self._intraday_watchlist_symbols())
        prioritized.extend(
            self.news_fetcher.recent_intraday_catalyst_symbols(
                as_of=datetime.now(tz=settings.tzinfo),
                limit=max(4, min(settings.news_intraday_catalyst_limit, limit // 3)),
            )
        )
        prioritized.extend([config.symbol for config in self.historical_fetcher.select_symbols(limit=limit)])

        selected: list[str] = []
        seen: set[str] = set()
        for symbol in prioritized:
            normalized = str(symbol or "").upper()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            selected.append(normalized)
            if len(selected) >= limit:
                break
        return selected

    def catch_up_live_session_if_needed(self) -> None:
        if reason := self._holiday_reason(self._today_local()):
            return
        now = datetime.now(tz=settings.tzinfo)
        if not self._within_intraday_window(now):
            return

        runtime_symbols = self._intraday_candidate_symbols(limit=self.INTRADAY_UNIVERSE_LIMIT)
        self.live_intraday_service.ensure_runtime(runtime_symbols, force_seed=True)
        if runtime_symbols:
            self.market_data_service.refresh_market_cache(
                force=True,
                watchlist_limit=min(len(runtime_symbols), self.market_data_service.LIVE_WATCHLIST_LIMIT),
            )
        price_symbols = sorted(set(runtime_symbols + self._tracked_symbols_for_price_updates()))
        latest_prices = self.live_intraday_service.get_latest_prices(price_symbols)
        missing_prices = [symbol for symbol in price_symbols if symbol not in latest_prices]
        if missing_prices:
            latest_prices.update(self.market_data_service.fetch_quotes_for_symbols(missing_prices))
        if latest_prices:
            self.paper_trader.activate_planned_trades(latest_prices, now=now)
            self.paper_trader.update_trades(latest_prices)

    def _has_upcoming_watchlist_batch(self, planned_for: date) -> bool:
        with session_scope() as session:
            trades = session.scalars(
                select(PaperTrade).where(
                    PaperTrade.entry_date == planned_for,
                    PaperTrade.exit_date.is_(None),
                )
            ).all()
        for trade in trades:
            metadata = trade.metadata_json or {}
            if metadata.get("plan_only") and metadata.get("opened_from") == "after_market_watchlist":
                return True
        return False

    def catch_up_after_market_if_needed(self) -> None:
        if reason := self._holiday_reason(self._today_local()):
            return
        now = datetime.now(tz=settings.tzinfo)
        if now.timetz().replace(tzinfo=None) < self._parse_clock(settings.after_market_start):
            return
        next_session = self.market_calendar.next_trading_day(self._today_local())
        if self._has_upcoming_watchlist_batch(next_session):
            return
        if self._after_market_catchup_thread is not None and self._after_market_catchup_thread.is_alive():
            return

        def runner() -> None:
            with session_scope() as session:
                add_notification(
                    session,
                    notification_type="WATCHLIST_READY",
                    title="After-market watchlist catch-up started",
                    body=f"Startup happened after the scheduled run, so rebuilding the watchlist now for {next_session.isoformat()}.",
                    color="blue",
                )
            try:
                self.after_market_analysis(force=True)
            except Exception as exc:
                with session_scope() as session:
                    add_notification(
                        session,
                        notification_type="WATCHLIST_READY",
                        title="After-market watchlist catch-up failed",
                        body=f"{type(exc).__name__}: {exc}",
                        color="red",
                    )

        self._after_market_catchup_thread = Thread(
            target=runner,
            daemon=True,
            name="after-market-watchlist-catchup",
        )
        self._after_market_catchup_thread.start()

    @staticmethod
    def _volume_ratio_from_frame(frame) -> float:
        latest = frame.iloc[-1]
        baseline = float(latest.get("Volume_SMA_20", latest["Volume"]) or latest["Volume"] or 1.0)
        return float(latest["Volume"] / max(baseline, 1.0))

    @staticmethod
    def _bullish_candle(frame) -> bool:
        latest = frame.iloc[-1]
        return any(float(latest.get(column, 0) or 0) > 0 for column in ["HAMMER", "ENGULFING", "MORNING_STAR", "THREE_WHITE"])

    @staticmethod
    def _bearish_candle(frame) -> bool:
        latest = frame.iloc[-1]
        return any(float(latest.get(column, 0) or 0) < 0 for column in ["EVENING_STAR", "SHOOTING_ST", "DARK_CLOUD", "THREE_BLACK"])

    @staticmethod
    def _strategy_backtest_summary(strategy_row: StockStrategyMap | None, strategy_name: str) -> str | None:
        if strategy_row is None:
            return None
        metrics: list[str] = []
        if strategy_row.best_strategy:
            metrics.append(f"best mapped strategy is {strategy_row.best_strategy}")
        if strategy_row.win_rate is not None:
            metrics.append(f"{strategy_row.win_rate:.0%} backtest win rate")
        if strategy_row.composite_score is not None:
            metrics.append(f"composite score {strategy_row.composite_score:.2f}")
        if strategy_row.regime_performed_best:
            metrics.append(f"worked best in {strategy_row.regime_performed_best.lower()}")
        if not metrics:
            return None
        return f"Backtest context: {', '.join(metrics[:3])}."

    @staticmethod
    def _news_perspective_text(intelligence, news_score: float) -> str:
        combined = intelligence.combined_news_score
        if combined >= 0.8:
            label = "bullish"
        elif combined <= -0.8:
            label = "bearish"
        else:
            label = "mixed"
        parts = [f"News view is {label} with combined score {combined:.2f} (raw sentiment {news_score:.2f})"]
        if intelligence.event.event_flags:
            parts.append(f"key event flags: {', '.join(intelligence.event.event_flags[:2])}")
        if intelligence.event.catalyst_summary:
            parts.append(intelligence.event.catalyst_summary)
        if intelligence.fundamental.days_to_earnings is not None:
            if intelligence.fundamental.days_to_earnings <= 1:
                parts.append("earnings are due within the next session")
            else:
                parts.append(f"{intelligence.fundamental.days_to_earnings} days remain to earnings")
        return ". ".join(parts) + "."

    @staticmethod
    def _candidate_explanation_sections(
        *,
        reason: str,
        basis_points: list[str],
        news_perspective: str,
        intelligence,
        adjustment_reasons: list[str],
        regime: str,
        backtest_summary: str | None,
    ) -> dict[str, list[str]]:
        sector_line = (
            f"Sector context: {intelligence.sector_strength.sector} is "
            f"{intelligence.sector_strength.label.lower()} with score {intelligence.sector_strength.score:.2f}."
        )
        fundamental_line = (
            f"Fundamental quality score is {intelligence.scoring_fundamental_score:.2f} "
            f"with confidence {intelligence.fundamental.confidence:.2f}."
        )
        valuation_line = (
            f"Valuation looks {intelligence.valuation_label.lower()} with score {intelligence.valuation_score:.2f}; "
            f"selection score is {intelligence.selection_score:.2f} ({intelligence.selection_label.lower().replace('_', ' ')})."
        )
        outlook_line = f"Business outlook score is {intelligence.business_outlook_score:.2f}. {intelligence.fundamental.selection_summary}"
        data_source_line = (
            "Structured balance-sheet data is available for this stock."
            if intelligence.fundamental.has_snapshot
            else "Structured balance-sheet data is not stored, so financial-news cues are carrying more weight."
        )
        risk_points = [f"Regime filter: {regime}.", *adjustment_reasons[:3]]
        if intelligence.fundamental.days_to_earnings is not None:
            risk_points.append(f"Days to earnings: {intelligence.fundamental.days_to_earnings}.")
        if backtest_summary:
            risk_points.append(backtest_summary)
        return {
            "technical": [reason, *basis_points[:3]],
            "news": [news_perspective, *intelligence.event.notes[:2], *intelligence.event.event_flags[:2]],
            "sector": [sector_line, *intelligence.sector_strength.notes[:2]],
            "fundamentals": [fundamental_line, valuation_line, outlook_line, data_source_line, *intelligence.fundamental.flags[:2]],
            "risk": risk_points[:5],
        }

    def _build_strategy_signal_candidate(
        self,
        *,
        symbol_config: SymbolConfig,
        frame,
        now: datetime,
        strategy_row: StockStrategyMap | None,
    ) -> dict[str, object] | None:
        symbol = symbol_config.symbol
        news_score = self.news_fetcher.get_sentiment_for_date(symbol, now)
        strategy_name = self.signal_engine._select_strategy_name(symbol)
        strategy = self.signal_engine.strategy_registry[strategy_name]
        regime = detect_regime(frame)
        if regime == "RANGING" and strategy_name in {"EMA Crossover", "Golden Cross", "MACD Momentum", "Breakout with Volume", "Supertrend"}:
            strategy_name = "RSI Mean Reversion"
            strategy = self.signal_engine.strategy_registry[strategy_name]
        intelligence = self.signal_engine.intelligence_engine.build(
            symbol=symbol,
            company_name=symbol_config.company_name,
            as_of=now,
            signal_type=strategy.signal_type,
            base_news_score=news_score,
        )
        signal = strategy.generate_signal(
            frame,
            date=frame.index[-1],
            context=StrategyContext(
                news_score=intelligence.combined_news_score,
                regime=regime,
                signal_type=strategy.signal_type,
            ),
        )
        signal, news_override_reasons = self.signal_engine._maybe_use_news_catalyst_signal(
            signal=signal,
            df=frame,
            regime=regime,
            combined_news_score=intelligence.combined_news_score,
            signal_type=strategy.signal_type,
            timeframe="DAILY",
            event_flags=intelligence.event.event_flags,
        )
        strategy_name = str(signal.get("strategy_name") or strategy_name)
        strategy = self.signal_engine.strategy_registry.get(strategy_name, strategy)
        direction = str(signal["signal"]).upper()
        if direction == "HOLD":
            return None
        if strategy.signal_type == "INVESTMENT" and direction != "BUY":
            return None

        features = self.signal_engine._build_signal_features(
            frame,
            signal,
            intelligence.combined_news_score,
            regime,
            intelligence.scoring_fundamental_score,
        )
        confidence = self.signal_engine.scoring_engine.score(features)
        adjusted_confidence, adjustment_reasons = self.signal_engine._apply_intelligence_confidence(
            confidence,
            intelligence,
            direction=direction,
            signal_type=strategy.signal_type,
        )
        if adjusted_confidence is None:
            return None
        adjusted_confidence, bear_penalty_reasons = self.signal_engine._apply_bearish_buy_penalty(
            adjusted_confidence,
            direction=direction,
            signal_type=strategy.signal_type,
            regime=regime,
            combined_news_score=intelligence.combined_news_score,
            event_flags=intelligence.event.event_flags,
        )
        adjustment_reasons = list(news_override_reasons) + list(adjustment_reasons or []) + bear_penalty_reasons
        if adjusted_confidence < self.MIN_CONFIDENCE_SCORE:
            return None
        signal.update(
            {
                "stock_symbol": symbol,
                "confidence_score": adjusted_confidence,
                "news_score_at_entry": intelligence.combined_news_score,
                "regime_at_entry": regime,
                "entry_zone_low": round(signal["entry_price"] * (1 - self.ENTRY_ZONE_BUFFER_PCT), 2),
                "entry_zone_high": round(signal["entry_price"] * (1 + self.ENTRY_ZONE_BUFFER_PCT), 2),
                "feature_breakdown": features,
                "sector": intelligence.sector,
                "sector_score": intelligence.sector_strength.score,
                "days_to_earnings": intelligence.fundamental.days_to_earnings,
                "event_score": intelligence.event.event_score,
                "event_flags": intelligence.event.event_flags,
                "fundamental_quality_score": intelligence.scoring_fundamental_score,
                "fundamental_has_snapshot": intelligence.fundamental.has_snapshot,
                "fundamental_confidence": intelligence.fundamental.confidence,
                "fundamental_raw_metrics": intelligence.fundamental.raw_metrics,
                "fundamental_growth_score": intelligence.fundamental.growth_score,
                "fundamental_balance_score": intelligence.fundamental.balance_sheet_score,
                "fundamental_business_quality_score": intelligence.fundamental.business_quality_score,
                "fundamental_ownership_score": intelligence.fundamental.ownership_score,
                "valuation_score": intelligence.valuation_score,
                "valuation_label": intelligence.valuation_label,
                "business_outlook_score": intelligence.business_outlook_score,
                "selection_score": intelligence.selection_score,
                "selection_label": intelligence.selection_label,
                "sector_peer_count": intelligence.fundamental.sector_peer_count,
                "financial_data_source": "STRUCTURED_SNAPSHOT" if intelligence.fundamental.has_snapshot else "NEWS_FALLBACK",
                "intelligence_notes": intelligence.notes,
            }
        )
        self.signal_engine._attach_explanation(
            symbol,
            frame,
            signal,
            regime=regime,
            news_score=intelligence.combined_news_score,
            features=features,
            intelligence=intelligence,
            adjustment_reasons=adjustment_reasons,
        )
        backtest_summary = self._strategy_backtest_summary(strategy_row, strategy_name)
        news_perspective = self._news_perspective_text(intelligence, news_score)
        basis_points = list(signal.get("basis_points", []))
        if backtest_summary:
            basis_points.append(backtest_summary)
        basis_points.append(news_perspective)
        strategy_reason = str(signal.get("recommendation_reason") or f"{strategy_name} produced a ready setup on the daily chart.")
        if backtest_summary:
            strategy_reason = f"{strategy_reason} {backtest_summary}"
        signal_type = str(signal.get("signal_type") or strategy.signal_type)
        explanation_sections = signal.get("explanation_sections") or self._candidate_explanation_sections(
            reason=strategy_reason,
            basis_points=basis_points,
            news_perspective=news_perspective,
            intelligence=intelligence,
            adjustment_reasons=adjustment_reasons,
            regime=regime,
            backtest_summary=backtest_summary,
        )
        trigger_price = float(signal.get("entry_zone_low") or signal["entry_price"]) if direction == "SELL" else float(signal.get("entry_zone_high") or signal["entry_price"])
        return {
            "symbol": symbol,
            "company_name": symbol_config.company_name,
            "reason": strategy_reason,
            "price": float(frame.iloc[-1]["Close"]),
            "trigger_price": trigger_price,
            "score": (
                float(adjusted_confidence)
                + (4.0 if strategy_row and strategy_row.best_strategy == strategy_name else 0.0)
                + (float(intelligence.selection_score) * 6.0)
                + (float(intelligence.business_outlook_score) * 4.0)
            ),
            "trigger_style": "ENTRY_ZONE",
            "strategy_name": strategy_name,
            "signal_type": signal_type,
            "direction": direction,
            "confidence_score": float(adjusted_confidence),
            "news_perspective": news_perspective,
            "news_score": float(intelligence.combined_news_score),
            "event_flags": intelligence.event.event_flags[:4],
            "event_positive_results_catalyst": intelligence.event.positive_results_catalyst,
            "event_financial_catalyst_score": float(intelligence.event.financial_catalyst_score),
            "event_catalyst_summary": intelligence.event.catalyst_summary,
            "basis_points": basis_points[:10],
            "explanation_sections": explanation_sections,
            "sector": intelligence.sector,
            "sector_score": float(intelligence.sector_strength.score),
            "fundamental_quality_score": float(intelligence.scoring_fundamental_score),
            "fundamental_has_snapshot": intelligence.fundamental.has_snapshot,
            "fundamental_confidence": float(intelligence.fundamental.confidence),
            "fundamental_raw_metrics": intelligence.fundamental.raw_metrics,
            "valuation_score": float(intelligence.valuation_score),
            "valuation_label": intelligence.valuation_label,
            "business_outlook_score": float(intelligence.business_outlook_score),
            "selection_score": float(intelligence.selection_score),
            "selection_label": intelligence.selection_label,
            "financial_data_source": "STRUCTURED_SNAPSHOT" if intelligence.fundamental.has_snapshot else "NEWS_FALLBACK",
        }

    def _build_strategy_proximity_candidate(
        self,
        *,
        symbol_config: SymbolConfig,
        frame,
        now: datetime,
        strategy_row: StockStrategyMap | None,
    ) -> dict[str, object] | None:
        symbol = symbol_config.symbol
        strategy_name = self.signal_engine._select_strategy_name(symbol)
        strategy = self.signal_engine.strategy_registry[strategy_name]
        regime = detect_regime(frame)
        if regime == "RANGING" and strategy_name in {"EMA Crossover", "Golden Cross", "MACD Momentum", "Breakout with Volume", "Supertrend"}:
            strategy_name = "RSI Mean Reversion"
            strategy = self.signal_engine.strategy_registry[strategy_name]
        latest = frame.iloc[-1]
        previous = frame.iloc[-2]
        close = float(latest["Close"])
        volume_ratio = self._volume_ratio_from_frame(frame)
        news_score = self.news_fetcher.get_sentiment_for_date(symbol, now)
        intelligence = self.signal_engine.intelligence_engine.build(
            symbol=symbol,
            company_name=symbol_config.company_name,
            as_of=now,
            signal_type=strategy.signal_type,
            base_news_score=news_score,
        )
        reason: str | None = None
        trigger_price: float | None = None
        score = 56.0
        direction = "BUY"
        basis_points: list[str] = []
        trigger_style = "ENTRY_ZONE"

        if strategy_name == "EMA Crossover":
            if close > float(latest["SMA_50"]):
                gap_pct = ((float(latest["EMA_20"]) - float(latest["EMA_9"])) / max(close, 0.01)) * 100
                if 0.0 <= gap_pct <= 0.9:
                    direction = "BUY"
                    trigger_price = max(close, float(latest["EMA_20"]) * 1.002)
                    reason = f"EMA9 is only {gap_pct:.2f}% below EMA20 while price remains above SMA50, so the mapped crossover setup is close to triggering."
                    basis_points = [
                        f"EMA9 {latest['EMA_9']:.2f} vs EMA20 {latest['EMA_20']:.2f}.",
                        f"Close {close:.2f} is above SMA50 {latest['SMA_50']:.2f}.",
                    ]
                    score += max(0.0, 10.0 - (gap_pct * 8.0))
            elif strategy.signal_type != "INVESTMENT":
                gap_pct = ((float(latest["EMA_9"]) - float(latest["EMA_20"])) / max(close, 0.01)) * 100
                if 0.0 <= gap_pct <= 0.9:
                    direction = "SELL"
                    trigger_price = min(close, float(latest["EMA_20"]) * 0.998)
                    reason = f"EMA9 is only {gap_pct:.2f}% above EMA20 while price remains below SMA50, so the mapped bearish crossover setup is close to triggering."
                    basis_points = [
                        f"EMA9 {latest['EMA_9']:.2f} vs EMA20 {latest['EMA_20']:.2f}.",
                        f"Close {close:.2f} is below SMA50 {latest['SMA_50']:.2f}.",
                    ]
                    score += max(0.0, 10.0 - (gap_pct * 8.0))
        elif strategy_name == "RSI Mean Reversion":
            current_rsi = float(latest["RSI_14"])
            previous_rsi = float(previous["RSI_14"])
            if 28.0 <= current_rsi <= 40.0 and current_rsi >= (previous_rsi - 1.5):
                direction = "BUY"
                trigger_price = close * 1.01
                reason = f"RSI is sitting at {current_rsi:.2f} after an oversold stretch, so the mapped mean-reversion setup is close to a bounce trigger."
                basis_points = [
                    f"RSI moved from {previous_rsi:.2f} to {current_rsi:.2f}.",
                    f"Recent swing-low support is {frame['Low'].tail(5).min():.2f}.",
                ]
                score += max(0.0, 12.0 - abs(35.0 - current_rsi))
            elif strategy.signal_type != "INVESTMENT" and 60.0 <= current_rsi <= 72.0 and current_rsi <= (previous_rsi + 1.0):
                direction = "SELL"
                trigger_price = close * 0.99
                reason = f"RSI is elevated at {current_rsi:.2f} after an overbought stretch, so the mapped mean-reversion setup is close to a short reversal trigger."
                basis_points = [
                    f"RSI moved from {previous_rsi:.2f} to {current_rsi:.2f}.",
                    f"Recent swing-high resistance is {frame['High'].tail(5).max():.2f}.",
                ]
                score += max(0.0, 12.0 - abs(65.0 - current_rsi))
        elif strategy_name == "MACD Momentum":
            macd_gap = float(latest["MACD_Signal"] - latest["MACD"])
            macd_threshold = max(0.15, float(latest["ATR_14"]) * 0.12)
            if close > float(latest["SMA_50"]) and 0.0 <= macd_gap <= macd_threshold:
                direction = "BUY"
                trigger_price = close * 1.005
                reason = f"MACD is close to a bullish crossover while price remains above SMA50, so the mapped momentum setup is on watch."
                basis_points = [
                    f"MACD {latest['MACD']:.2f} vs signal {latest['MACD_Signal']:.2f}.",
                    f"Histogram is {latest['MACD_Hist']:.2f}.",
                ]
                score += 10.0 - ((macd_gap / max(macd_threshold, 0.01)) * 6.0)
            elif strategy.signal_type != "INVESTMENT":
                bearish_gap = float(latest["MACD"] - latest["MACD_Signal"])
                if close < float(latest["SMA_50"]) and 0.0 <= bearish_gap <= macd_threshold:
                    direction = "SELL"
                    trigger_price = close * 0.995
                    reason = "MACD is close to a bearish crossover while price remains below SMA50, so the mapped momentum setup is on watch for downside follow-through."
                    basis_points = [
                        f"MACD {latest['MACD']:.2f} vs signal {latest['MACD_Signal']:.2f}.",
                        f"Histogram is {latest['MACD_Hist']:.2f}.",
                    ]
                    score += 10.0 - ((bearish_gap / max(macd_threshold, 0.01)) * 6.0)
        elif strategy_name == "Bollinger Band Squeeze":
            width = float(latest["BB_Width"])
            width_avg = float(latest["BB_Width_Avg_20"])
            band_gap_pct = ((float(latest["BB_Upper"]) - close) / max(close, 0.01)) * 100
            if width <= width_avg * 1.05 and 0.0 <= band_gap_pct <= 1.5 and volume_ratio >= 1.05:
                direction = "BUY"
                trigger_price = float(latest["BB_Upper"])
                reason = f"Bollinger width remains compressed and price is only {band_gap_pct:.2f}% below the upper band, keeping the mapped squeeze breakout setup in play."
                basis_points = [
                    f"BB width {width:.2f} vs 20-day average {width_avg:.2f}.",
                    f"Volume is {volume_ratio:.2f}x the 20-day average.",
                ]
                trigger_style = "BREAKOUT"
                score += 9.0
            elif strategy.signal_type != "INVESTMENT":
                lower_gap_pct = ((close - float(latest["BB_Lower"])) / max(close, 0.01)) * 100
                if width <= width_avg * 1.05 and 0.0 <= lower_gap_pct <= 1.5 and volume_ratio >= 1.05:
                    direction = "SELL"
                    trigger_price = float(latest["BB_Lower"])
                    reason = f"Bollinger width remains compressed and price is only {lower_gap_pct:.2f}% above the lower band, keeping the mapped squeeze breakdown setup in play."
                    basis_points = [
                        f"BB width {width:.2f} vs 20-day average {width_avg:.2f}.",
                        f"Volume is {volume_ratio:.2f}x the 20-day average.",
                    ]
                    trigger_style = "BREAKDOWN"
                    score += 9.0
        elif strategy_name == "Breakout with Volume":
            breakout_level = float(latest.get("High_20") or frame["High"].tail(20).max())
            breakdown_level = float(latest.get("Low_20") or frame["Low"].tail(20).min())
            breakout_gap_pct = ((breakout_level - close) / max(close, 0.01)) * 100
            if 0.0 <= breakout_gap_pct <= 2.0 and volume_ratio >= 1.10:
                direction = "BUY"
                trigger_price = breakout_level
                reason = f"Price is within {breakout_gap_pct:.2f}% of the 20-day breakout level with improving volume, which fits the mapped breakout strategy."
                basis_points = [
                    f"Breakout level is {breakout_level:.2f}.",
                    f"Volume is {volume_ratio:.2f}x average.",
                ]
                trigger_style = "BREAKOUT"
                score += 10.0
            elif strategy.signal_type != "INVESTMENT":
                breakdown_gap_pct = ((close - breakdown_level) / max(close, 0.01)) * 100
                if 0.0 <= breakdown_gap_pct <= 2.0 and volume_ratio >= 1.10:
                    direction = "SELL"
                    trigger_price = breakdown_level
                    reason = f"Price is within {breakdown_gap_pct:.2f}% of the 20-day breakdown level with improving volume, which fits the mapped bearish breakout strategy."
                    basis_points = [
                        f"Breakdown level is {breakdown_level:.2f}.",
                        f"Volume is {volume_ratio:.2f}x average.",
                    ]
                    trigger_style = "BREAKDOWN"
                    score += 10.0
        elif strategy_name == "Supertrend":
            line_gap_pct = abs(close - float(latest["Supertrend"])) / max(close, 0.01) * 100
            if line_gap_pct <= 1.2:
                if close >= float(latest["Supertrend"]):
                    direction = "BUY"
                    trigger_price = max(close, float(latest["Supertrend"]) * 1.002)
                    reason = f"Price is only {line_gap_pct:.2f}% away from the supertrend line, so the mapped supertrend flip setup is very close."
                    basis_points = [
                        f"Close {close:.2f} vs supertrend {latest['Supertrend']:.2f}.",
                        f"Previous close was {previous['Close']:.2f}.",
                    ]
                    score += 9.0
                elif strategy.signal_type != "INVESTMENT":
                    direction = "SELL"
                    trigger_price = min(close, float(latest["Supertrend"]) * 0.998)
                    reason = f"Price is only {line_gap_pct:.2f}% away from the supertrend line on the downside, so the mapped bearish flip setup is very close."
                    basis_points = [
                        f"Close {close:.2f} vs supertrend {latest['Supertrend']:.2f}.",
                        f"Previous close was {previous['Close']:.2f}.",
                    ]
                    score += 9.0
        elif strategy_name == "Support and Resistance":
            support = float(latest["Low_63"])
            resistance = float(latest["High_63"])
            support_gap_pct = abs(close - support) / max(close, 0.01) * 100
            resistance_gap_pct = abs(resistance - close) / max(close, 0.01) * 100
            bullish_pattern = self._bullish_candle(frame)
            bearish_pattern = self._bearish_candle(frame)
            if support_gap_pct <= 2.2 or (support_gap_pct <= 3.0 and bullish_pattern):
                direction = "BUY"
                trigger_price = close * 1.01
                reason = f"Price is near the key 3-month support zone and the mapped support-resistance strategy is waiting for a bounce confirmation."
                basis_points = [
                    f"Support is {support:.2f}; resistance is {resistance:.2f}.",
                    f"Bullish candle confirmation is {'present' if bullish_pattern else 'not yet present'}.",
                ]
                score += 11.0 if bullish_pattern else 7.0
            elif strategy.signal_type != "INVESTMENT" and (resistance_gap_pct <= 2.2 or (resistance_gap_pct <= 3.0 and bearish_pattern)):
                direction = "SELL"
                trigger_price = close * 0.99
                reason = "Price is near the key 3-month resistance zone and the mapped support-resistance strategy is waiting for a rejection confirmation."
                basis_points = [
                    f"Resistance is {resistance:.2f}; support is {support:.2f}.",
                    f"Bearish candle confirmation is {'present' if bearish_pattern else 'not yet present'}.",
                ]
                score += 11.0 if bearish_pattern else 7.0
        elif strategy_name == "News-Driven Momentum":
            current_rsi = float(latest["RSI_14"])
            sentiment_threshold = settings.news_momentum_sentiment_threshold
            if (
                intelligence.combined_news_score >= sentiment_threshold
                and 40.0 <= current_rsi <= 65.0
                and close > float(latest["EMA_20"])
            ):
                direction = "BUY"
                trigger_price = close * 1.004
                reason = "News flow is supportive and price structure is already constructive, so the mapped news-momentum setup is on watch for continuation."
                basis_points = [
                    f"Combined news score is {intelligence.combined_news_score:.2f}.",
                    f"RSI is {current_rsi:.2f} and close is above EMA20 {latest['EMA_20']:.2f}.",
                ]
                score += 10.0
            elif (
                strategy.signal_type != "INVESTMENT"
                and intelligence.combined_news_score <= -sentiment_threshold
                and 35.0 <= current_rsi <= 60.0
                and close < float(latest["EMA_20"])
            ):
                direction = "SELL"
                trigger_price = close * 0.996
                reason = "News flow is negative and price structure is already weak, so the mapped news-momentum setup is on watch for downside continuation."
                basis_points = [
                    f"Combined news score is {intelligence.combined_news_score:.2f}.",
                    f"RSI is {current_rsi:.2f} and close is below EMA20 {latest['EMA_20']:.2f}.",
                ]
                score += 10.0
        elif strategy_name == "Golden Cross":
            sma_gap_pct = ((float(latest["SMA_50"]) - float(latest["SMA_200"])) / max(close, 0.01)) * 100
            if close > float(latest["SMA_200"]) and -1.0 <= sma_gap_pct <= 1.25:
                direction = "BUY"
                trigger_price = max(close, float(latest["SMA_50"]) * 1.002)
                reason = f"The medium-term averages are converging and the mapped golden-cross investment setup is close to confirmation."
                basis_points = [
                    f"SMA50 {latest['SMA_50']:.2f} vs SMA200 {latest['SMA_200']:.2f}.",
                    f"Close {close:.2f} remains above the long-term trend line.",
                ]
                score += 8.0
        elif strategy_name == "Combined Regime-Aware":
            if regime.startswith("TRENDING"):
                gap_pct = ((float(latest["EMA_20"]) - float(latest["EMA_9"])) / max(close, 0.01)) * 100
                if close > float(latest["SMA_50"]) and 0.0 <= gap_pct <= 1.0:
                    direction = "BUY"
                    trigger_price = max(close, float(latest["EMA_20"]) * 1.002)
                    reason = f"Trending regime remains intact and the mapped regime-aware setup is close to an EMA-based trigger."
                    basis_points = [
                        f"Detected regime {regime}.",
                        f"EMA9 {latest['EMA_9']:.2f} vs EMA20 {latest['EMA_20']:.2f}.",
                    ]
                    score += 9.0
                elif strategy.signal_type != "INVESTMENT":
                    bearish_gap = ((float(latest["EMA_9"]) - float(latest["EMA_20"])) / max(close, 0.01)) * 100
                    if close < float(latest["SMA_50"]) and 0.0 <= bearish_gap <= 1.0:
                        direction = "SELL"
                        trigger_price = min(close, float(latest["EMA_20"]) * 0.998)
                        reason = "Trending bearish regime remains intact and the mapped regime-aware setup is close to an EMA-based short trigger."
                        basis_points = [
                            f"Detected regime {regime}.",
                            f"EMA9 {latest['EMA_9']:.2f} vs EMA20 {latest['EMA_20']:.2f}.",
                        ]
                        score += 9.0
            elif regime == "RANGING":
                current_rsi = float(latest["RSI_14"])
                if 30.0 <= current_rsi <= 40.0:
                    direction = "BUY"
                    trigger_price = close * 1.01
                    reason = f"Ranging regime is active and RSI is nearing a mean-reversion reversal, which matches the mapped regime-aware setup."
                    basis_points = [
                        f"Detected regime {regime}.",
                        f"RSI14 is {current_rsi:.2f}.",
                    ]
                    score += 8.0
                elif (
                    strategy.signal_type != "INVESTMENT"
                    and settings.intraday_overbought_rsi_floor <= current_rsi <= settings.intraday_overbought_rsi_ceiling
                ):
                    direction = "SELL"
                    trigger_price = close * 0.99
                    reason = "Ranging regime is active and RSI is nearing an overbought reversal, which matches the mapped regime-aware short setup."
                    basis_points = [
                        f"Detected regime {regime}.",
                        f"RSI14 is {current_rsi:.2f}.",
                    ]
                    score += 8.0

        positive_results_catalyst = self.signal_engine._has_positive_results_catalyst(
            intelligence.combined_news_score,
            intelligence.event.event_flags,
        )
        if positive_results_catalyst:
            current_rsi = float(latest["RSI_14"])
            if close >= float(latest["EMA_20"]) * 0.997 and 40.0 <= current_rsi <= 74.0 and volume_ratio >= 0.80:
                direction = "BUY"
                trigger_price = max(close * 1.002, float(latest["EMA_20"]) * 1.001)
                strategy_name = "News-Driven Momentum"
                strategy = self.signal_engine.strategy_registry.get(strategy_name, strategy)
                reason = (
                    intelligence.event.catalyst_summary
                    or "Fresh results/news flow is strongly positive, so the stock is on watch for an intraday momentum continuation."
                )
                basis_points = [
                    f"Combined news score is {intelligence.combined_news_score:.2f}.",
                    f"RSI is {current_rsi:.2f}, close is above EMA20 {latest['EMA_20']:.2f}, and volume is {volume_ratio:.2f}x average.",
                    f"Event flags: {', '.join(intelligence.event.event_flags[:2]) or 'positive catalyst detected'}.",
                ]
                trigger_style = "BREAKOUT"
                score = max(score, 74.0) + (float(intelligence.event.financial_catalyst_score) * 10.0)

        if reason is None or trigger_price is None:
            return None
        if strategy.signal_type == "INVESTMENT" and direction != "BUY":
            return None

        if strategy_row and strategy_row.best_strategy == strategy_name and strategy_row.win_rate is not None:
            score += float(strategy_row.win_rate) * 6.0

        adjusted_confidence, adjustment_reasons = self.signal_engine._apply_intelligence_confidence(
            score,
            intelligence,
            direction=direction,
            signal_type=strategy.signal_type,
        )
        if adjusted_confidence is None:
            return None
        adjusted_confidence, bear_penalty_reasons = self.signal_engine._apply_bearish_buy_penalty(
            adjusted_confidence,
            direction=direction,
            signal_type=strategy.signal_type,
            regime=regime,
            combined_news_score=intelligence.combined_news_score,
            event_flags=intelligence.event.event_flags,
        )
        adjustment_reasons = list(adjustment_reasons or []) + bear_penalty_reasons
        if adjusted_confidence < self.MIN_CONFIDENCE_SCORE:
            return None
        backtest_summary = self._strategy_backtest_summary(strategy_row, strategy_name)
        news_perspective = self._news_perspective_text(intelligence, news_score)
        if backtest_summary:
            basis_points.append(backtest_summary)
        basis_points.append(news_perspective)
        basis_points.extend(adjustment_reasons[:2])
        full_reason = f"{reason} {backtest_summary}" if backtest_summary else reason
        explanation_sections = self._candidate_explanation_sections(
            reason=full_reason,
            basis_points=basis_points,
            news_perspective=news_perspective,
            intelligence=intelligence,
            adjustment_reasons=adjustment_reasons,
            regime=regime,
            backtest_summary=backtest_summary,
        )
        return {
            "symbol": symbol,
            "company_name": symbol_config.company_name,
            "reason": full_reason,
            "price": close,
            "trigger_price": round(trigger_price, 2),
            "score": round(float(adjusted_confidence), 2),
            "trigger_style": trigger_style,
            "strategy_name": strategy_name,
            "signal_type": strategy.signal_type,
            "direction": direction,
            "confidence_score": round(float(adjusted_confidence), 2),
            "news_perspective": news_perspective,
            "news_score": float(intelligence.combined_news_score),
            "event_flags": intelligence.event.event_flags[:4],
            "event_positive_results_catalyst": intelligence.event.positive_results_catalyst,
            "event_financial_catalyst_score": float(intelligence.event.financial_catalyst_score),
            "event_catalyst_summary": intelligence.event.catalyst_summary,
            "basis_points": basis_points[:10],
            "explanation_sections": explanation_sections,
            "sector": intelligence.sector,
            "sector_score": float(intelligence.sector_strength.score),
            "fundamental_quality_score": float(intelligence.scoring_fundamental_score),
            "fundamental_has_snapshot": intelligence.fundamental.has_snapshot,
            "fundamental_confidence": float(intelligence.fundamental.confidence),
            "valuation_score": float(intelligence.valuation_score),
            "valuation_label": intelligence.valuation_label,
            "business_outlook_score": float(intelligence.business_outlook_score),
            "selection_score": float(intelligence.selection_score),
            "selection_label": intelligence.selection_label,
            "financial_data_source": "STRUCTURED_SNAPSHOT" if intelligence.fundamental.has_snapshot else "NEWS_FALLBACK",
        }

    def _open_intraday_symbols(self) -> set[str]:
        with session_scope() as session:
            trades = session.scalars(
                select(PaperTrade).where(
                    PaperTrade.signal_type == "INTRADAY",
                    PaperTrade.exit_date.is_(None),
                )
            ).all()
        symbols: set[str] = set()
        for trade in trades:
            if not trade.stock_symbol:
                continue
            metadata = trade.metadata_json or {}
            if metadata.get("plan_only"):
                continue
            symbols.add(trade.stock_symbol)
        return symbols

    def configure(self) -> None:
        if self._configured:
            return
        self._configure_market_jobs()
        self._configure_after_market_jobs()
        self._configured = True

    def _configure_market_jobs(self) -> None:
        self.market_scheduler.add_job(
            self.run_global_risk_scan_pre_market,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=30),
            id="global-risk-pre-market",
            replace_existing=True,
        )
        self.market_scheduler.add_job(
            self.market_open_preparation,
            CronTrigger(day_of_week="mon-fri", hour=9, minute="0"),
            id="market-open-prep",
            replace_existing=True,
        )
        self.market_scheduler.add_job(
            self.intraday_scan,
            CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5"),
            id="intraday-scan",
            replace_existing=True,
        )
        self.market_scheduler.add_job(
            self.refresh_priority_news,
            CronTrigger(day_of_week="mon-fri", hour="9-15", minute="2,12,22,32,42,52"),
            id="priority-news-sync",
            replace_existing=True,
        )
        self.market_scheduler.add_job(
            self.dispatch_phone_alerts,
            CronTrigger(minute="*"),
            id="dispatch-phone-alerts",
            replace_existing=True,
        )

    def _configure_after_market_jobs(self) -> None:
        self.after_market_scheduler.add_job(
            self.after_market_analysis,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=35),
            id="after-market-analysis",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.run_global_risk_scan_after_market,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=15),
            id="global-risk-after-market",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.refresh_official_daily_quote_shadow,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
            id="official-daily-quote-shadow",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.refresh_official_market_context_shadow,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=30),
            id="official-market-context-shadow",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.refresh_priority_news,
            CronTrigger(day_of_week="mon-fri", hour="16-18", minute="5,35"),
            id="after-market-news-sync",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.refresh_daily_fundamentals,
            CronTrigger(day_of_week="mon-fri", hour=17, minute=15),
            id="daily-fundamentals-refresh",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.generate_daily_report,
            CronTrigger(day_of_week="mon-fri", hour=18, minute=5),
            id="daily-report",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.create_daily_backup,
            CronTrigger(day_of_week="mon-fri", hour=18, minute=20),
            id="daily-backup",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.refresh_official_weekly_fundamentals_shadow,
            CronTrigger(day_of_week="sun", hour=18, minute=0),
            id="official-weekly-fundamentals-shadow",
            replace_existing=True,
        )
        self.after_market_scheduler.add_job(
            self.weekly_retrain,
            CronTrigger(day_of_week="sun", hour=23, minute=0),
            id="weekly-retrain",
            replace_existing=True,
        )

    def market_open_preparation(self) -> None:
        if reason := self._holiday_reason(self._today_local()):
            self._notify_holiday_skip(
                title="Market closed today",
                body=f"Skipped market preparation because {reason}.",
            )
            return
        symbols = self._intraday_candidate_symbols(limit=self.INTRADAY_UNIVERSE_LIMIT)
        news_sync = self.refresh_priority_news(limit=self.NEWS_PRIORITY_SYNC_LIMIT, lookback_hours=24)
        self.live_intraday_service.ensure_runtime(symbols, force_seed=True)
        if symbols:
            self.market_data_service.refresh_market_cache(force=True, watchlist_limit=min(len(symbols), self.market_data_service.LIVE_WATCHLIST_LIMIT))
        with session_scope() as session:
            add_notification(
                session,
                notification_type="MARKET_PREP",
                title="Market open preparation started",
                body=(
                    "Loading the broader intraday universe, latest news sentiment, and pre-open candidates. "
                    f"News sync processed {news_sync['processed']} symbols and stored {news_sync['inserted']} articles."
                ),
                color="blue",
            )
        logger.info(
            "Market prep completed: intraday_universe=%s, news_processed=%s, news_inserted=%s",
            len(symbols),
            news_sync["processed"],
            news_sync["inserted"],
        )

        self.start_full_universe_news_sync(limit=None, lookback_hours=72, batch_size=25)

    def intraday_scan(self) -> None:
        if reason := self._holiday_reason(self._today_local()):
            self._notify_holiday_skip(
                title="Intraday scan skipped",
                body=f"Skipped intraday scan because {reason}.",
            )
            return
        now = datetime.now(tz=settings.tzinfo)
        if not self._within_intraday_window(now):
            self.live_intraday_service.set_active_signals([])
            return
        candidate_symbols = self._intraday_candidate_symbols(limit=self.INTRADAY_UNIVERSE_LIMIT)
        self.live_intraday_service.ensure_runtime(candidate_symbols)
        price_symbols = sorted(set(candidate_symbols + self._tracked_symbols_for_price_updates()))
        latest_prices = self.live_intraday_service.get_latest_prices(price_symbols)
        missing_prices = [symbol for symbol in price_symbols if symbol not in latest_prices]
        if missing_prices:
            latest_prices.update(self.market_data_service.fetch_quotes_for_symbols(missing_prices))
        activated = self.paper_trader.activate_planned_trades(latest_prices)
        self.paper_trader.update_trades(latest_prices)
        generated_signals: list[dict] = []
        entry_window_open = self._within_intraday_entry_window(now)
        symbol_map = self.historical_fetcher.load_symbol_map()
        open_intraday_symbols = self._open_intraday_symbols()
        scan_errors: list[str] = []
        if entry_window_open:
            for symbol in candidate_symbols:
                if symbol in open_intraday_symbols:
                    continue
                symbol_config = symbol_map.get(symbol.upper())
                if symbol_config is None:
                    continue
                intraday_frame = self.live_intraday_service.get_intraday_frame(symbol)
                if intraday_frame.empty or len(intraday_frame) < 50:
                    continue
                intraday_frame = IndicatorCalculator.enrich(intraday_frame)
                try:
                    daily_frame = self.historical_fetcher.fetch_symbol_frame(symbol_config)
                    news_score = self.news_fetcher.get_sentiment_for_date(symbol, now)
                    signal = self.signal_engine.evaluate_intraday_symbol(
                        symbol,
                        daily_frame,
                        intraday_frame,
                        news_score=news_score,
                        fundamental_score=0.5,
                        open_trade=True,
                        opened_from="intraday_scan",
                        company_name=symbol_config.company_name,
                    )
                except Exception as exc:
                    self._capture_error(scan_errors, symbol=symbol, exc=exc)
                    continue
                if signal is not None:
                    generated_signals.append(signal)

        self.live_intraday_service.set_active_signals(generated_signals)
        error_suffix = ""
        if scan_errors:
            error_suffix = f" Examples: {'; '.join(scan_errors[:2])}."
        cutoff_suffix = "New intraday entries are now locked after 3:00 PM; only open-trade management continues. " if not entry_window_open else ""
        with session_scope() as session:
            add_notification(
                session,
                notification_type="SCAN",
                title="Intraday scan executed",
                body=(
                    "Five-minute scan completed for the broader intraday universe. "
                    f"Activated {activated['activated']} planned trades, expired {activated['expired']}, "
                    f"generated {len(generated_signals)} fresh live signals, "
                    f"{cutoff_suffix}"
                    f"and skipped {len(scan_errors)} symbols due to processing issues."
                    f"{error_suffix}"
                ),
                color="blue",
            )
        logger.info(
            "Intraday scan completed: activated=%s expired=%s live_signals=%s skipped=%s entry_window_open=%s",
            activated["activated"],
            activated["expired"],
            len(generated_signals),
            len(scan_errors),
            entry_window_open,
        )

    def after_market_analysis(self, force: bool = False) -> None:
        fundamentals_result: dict[str, object] | None = None
        try:
            fundamentals_result = self.refresh_daily_fundamentals()
        except Exception as exc:
            logger.warning(
                "fundamentals refresh failed before after-market analysis: %s: %s",
                type(exc).__name__,
                exc,
            )
        if reason := self._holiday_reason(self._today_local()):
            recommendations = [] if settings.official_investment_cutover_enabled else self.generate_after_market_investment_recommendations()
            self._notify_holiday_skip(
                title="After-market watchlist skipped",
                body=f"Skipped watchlist rebuild because {reason}.",
            )
            with session_scope() as session:
                add_notification(
                    session,
                    notification_type="INVESTMENT_SCAN",
                    title="Holiday investment scan completed",
                    body=(
                        (
                            f"Market is closed for {reason}, and investment cutover is enabled, so no new investment plans were generated."
                            if settings.official_investment_cutover_enabled
                            else f"Market is closed for {reason}, but {len(recommendations)} investment recommendations were evaluated using the latest available daily data."
                        )
                        + (
                            f" Fundamentals refresh loaded {int(fundamentals_result['loaded'])} snapshots."
                            if fundamentals_result is not None
                            else ""
                        )
                    ),
                    color="blue",
                )
            return
        if not force and not self._within_after_market_window(datetime.now(tz=settings.tzinfo)):
            return

        with session_scope() as session:
            today_local = self._today_local()
            session.execute(delete(TomorrowWatchlist).where(TomorrowWatchlist.created_date == today_local))
            self.paper_trader.clear_planned_watchlist_trades(from_date=today_local, signal_type="INTRADAY")

            next_session = self.market_calendar.next_trading_day(today_local)

        now = datetime.now(tz=settings.tzinfo)
        symbols = self.historical_fetcher.select_symbols(limit=None)
        frame_map: dict[str, object] = {}
        watchlist_errors: list[str] = []
        with session_scope() as session:
            strategy_rows = session.scalars(
                select(StockStrategyMap).where(StockStrategyMap.symbol.in_([config.symbol for config in symbols]))
            ).all()
        strategy_map = {row.symbol: row for row in strategy_rows if row.symbol}
        for symbol_config in symbols:
            try:
                frame = self.historical_fetcher.fetch_symbol_frame(symbol_config)
            except Exception as exc:
                self._capture_error(watchlist_errors, symbol=symbol_config.symbol, exc=exc)
                continue
            if frame.empty or len(frame) < 63:
                continue
            frame_map[symbol_config.symbol] = frame

        self.signal_engine.intelligence_engine.sector_strength_engine.refresh_from_frames(symbols, frame_map, generated_at=now)

        ranked_candidates: list[dict[str, object]] = []
        for symbol_config in symbols:
            frame = frame_map.get(symbol_config.symbol)
            if frame is None:
                continue
            strategy_row = strategy_map.get(symbol_config.symbol)
            try:
                ready_candidate = self._build_strategy_signal_candidate(
                    symbol_config=symbol_config,
                    frame=frame,
                    now=now,
                    strategy_row=strategy_row,
                )
                if ready_candidate is not None:
                    ranked_candidates.append(ready_candidate)
                    continue
                proximity_candidate = self._build_strategy_proximity_candidate(
                    symbol_config=symbol_config,
                    frame=frame,
                    now=now,
                    strategy_row=strategy_row,
                )
                if proximity_candidate is not None:
                    ranked_candidates.append(proximity_candidate)
            except Exception as exc:
                self._capture_error(watchlist_errors, symbol=symbol_config.symbol, exc=exc)

        ranked = sorted(ranked_candidates, key=lambda item: float(item["score"]), reverse=True)[:20]
        with session_scope() as session:
            for item in ranked:
                symbol = str(item["symbol"])
                reason = str(item["reason"])
                price = float(item["price"])
                trigger_price = float(item["trigger_price"])
                trigger_style = str(item["trigger_style"])
                per_stock = strategy_map.get(symbol) or session.get(StockStrategyMap, symbol)
                self.paper_trader.plan_watchlist_trade(
                    stock_symbol=symbol,
                    watch_price=price,
                    reason=reason,
                    strategy_name=str(item.get("strategy_name") or (per_stock.best_strategy if per_stock else None) or "Tomorrow Watchlist"),
                    signal_type=str(item.get("signal_type") or "INTRADAY"),
                    direction=str(item.get("direction") or "BUY"),
                    planned_for=next_session,
                    trigger_price=trigger_price,
                    trigger_style=trigger_style,
                        confidence_score=float(item.get("confidence_score") or self.DEFAULT_CONFIDENCE_SCORE),
                    news_score=float(item.get("news_score") or 0.0),
                    news_perspective=str(item.get("news_perspective") or ""),
                    event_flags=list(item.get("event_flags") or []),
                    basis_points=list(item.get("basis_points") or []),
                    explanation_sections=dict(item.get("explanation_sections") or {}),
                    sector=item.get("sector"),
                    sector_score=float(item.get("sector_score")) if item.get("sector_score") is not None else None,
                    fundamental_quality_score=(
                        float(item.get("fundamental_quality_score"))
                        if item.get("fundamental_quality_score") is not None
                        else None
                    ),
                    fundamental_has_snapshot=bool(item.get("fundamental_has_snapshot")) if item.get("fundamental_has_snapshot") is not None else None,
                    fundamental_confidence=(
                        float(item.get("fundamental_confidence"))
                        if item.get("fundamental_confidence") is not None
                        else None
                    ),
                    financial_data_source=str(item.get("financial_data_source") or "") or None,
                )
                session.add(
                    TomorrowWatchlist(
                        symbol=symbol,
                        reason=reason,
                        watch_price=trigger_price,
                        signal_type=str(item.get("signal_type") or "INTRADAY"),
                        strategy=str(item.get("strategy_name") or (per_stock.best_strategy if per_stock else None)),
                    created_date=today_local,
                    )
                )
            add_notification(
                session,
                notification_type="WATCHLIST_READY",
                title="Tomorrow's watchlist ready",
                body=f"{len(ranked)} stocks were added to the next tradable session watchlist for {next_session.isoformat()}.",
                color="blue",
            )

        ranked_symbols = {str(item["symbol"]) for item in ranked}
        watchlist_configs = [config for config in symbols if config.symbol in ranked_symbols]
        self.sync_news_for_symbols(watchlist_configs, lookback_hours=72, max_symbols=20)
        if settings.official_investment_cutover_enabled:
            recommendations: list[dict] = []
            self._last_investment_scan_error_count = 0
            self._last_investment_scan_error_examples = []
        else:
            recommendations = self.generate_after_market_investment_recommendations()
        error_suffix = ""
        total_watchlist_errors = len(watchlist_errors) + self._last_investment_scan_error_count
        if total_watchlist_errors:
            merged_examples = watchlist_errors[:2] + self._last_investment_scan_error_examples[:2]
            error_suffix = (
                f" Skipped {total_watchlist_errors} symbols due to processing issues. "
                f"Examples: {'; '.join(merged_examples[:3])}."
            )
        with session_scope() as session:
            add_notification(
                session,
                notification_type="INVESTMENT_SCAN",
                title="After-market investment scan completed" if not settings.official_investment_cutover_enabled else "After-market investment cutover deferred",
                body=(
                    (
                        f"{len(recommendations)} investment recommendations were generated from daily charts."
                        if not settings.official_investment_cutover_enabled
                        else "Official investment cutover is enabled, so new investment plans will be generated after official quote, market context, Phase 2, and Phase 3 jobs complete."
                    )
                    + (
                        f" Fundamentals refresh loaded {int(fundamentals_result['loaded'])} snapshots before ranking."
                        if fundamentals_result is not None
                        else ""
                    )
                    + error_suffix
                ),
                color="blue",
            )
        logger.info(
            "After-market analysis completed: watchlist=%s investment_recommendations=%s skipped=%s cutover_enabled=%s",
            len(ranked),
            len(recommendations) if not settings.official_investment_cutover_enabled else "deferred",
            total_watchlist_errors,
            settings.official_investment_cutover_enabled,
        )

    def refresh_official_daily_quote_shadow(self) -> dict[str, object]:
        if not settings.official_investment_shadow_enabled:
            return {"enabled": False}
        today_local = self._today_local()
        if reason := self._holiday_reason(today_local):
            logger.info("Skipping official daily quote shadow sync because %s", reason)
            return {"enabled": True, "skipped": True, "reason": reason}
        result = self.official_investment_data_service.refresh_quote_snapshots()
        rebuild = self.official_snapshot_builder.rebuild_daily_snapshot(
            as_of_date=date.fromisoformat(str(result["as_of_date"]))
        )
        summary = self.shadow_comparison_service.compare(
            as_of_date=date.fromisoformat(str(result["as_of_date"])),
            missing_bse_mapping_symbols=list(result.get("missing_bse_mappings") or []),
            recovered_by_bse_count=int(result.get("recovered_by_bse") or 0),
        )
        with session_scope() as session:
            add_notification(
                session,
                notification_type="OFFICIAL_DATA_SHADOW",
                title="Official quote shadow sync completed",
                body=(
                    f"Stored {int(result['stored'])} official quote snapshots and rebuilt {int(rebuild['stored'])} official investment snapshots. "
                    f"BSE recovered {int(result['recovered_by_bse'])} symbols; "
                    f"legacy comparison covered {int(summary.get('coverageCompared') or 0)} symbols."
                ),
                color="blue",
            )
        return {
            "quote": result,
            "rebuild": rebuild,
            "summary": summary,
        }

    @staticmethod
    def _risk_notification_color(risk_level: str) -> str:
        return {"GREEN": "green", "YELLOW": "orange", "RED": "red"}.get(str(risk_level or "").upper(), "blue")

    def _planned_cutover_risk_levels_for_day(self, planned_for: date) -> list[str]:
        with session_scope() as session:
            trades = session.scalars(
                select(PaperTrade).where(
                    PaperTrade.signal_type == "INVESTMENT",
                    PaperTrade.entry_date == planned_for,
                    PaperTrade.exit_date.is_(None),
                )
            ).all()
        levels = {
            str((trade.metadata_json or {}).get("global_risk_level") or "").upper()
            for trade in trades
            if (trade.metadata_json or {}).get("plan_only")
            and (trade.metadata_json or {}).get("source_kind") == "official_investment_cutover"
        }
        return sorted(level for level in levels if level)

    def run_global_risk_scan_after_market(self) -> dict[str, object]:
        if not settings.global_risk_scanner_enabled:
            return {"enabled": False}
        today_local = self._today_local()
        if reason := self._holiday_reason(today_local):
            logger.info("Skipping after-market global risk scan because %s", reason)
            return {"enabled": True, "skipped": True, "reason": reason}
        if not self.global_risk_scanner.after_market_inputs_ready(today_local):
            logger.info("Skipping provisional after-market global risk scan because same-day inputs are not ready yet.")
            return {
                "enabled": True,
                "skipped": True,
                "reason": "same_day_inputs_not_ready",
            }
        result = self.global_risk_scanner.scan(today_local, scan_type="AFTER_MARKET")
        active = [signal.name for signal in result.signals if signal.severity in {"CAUTION", "BLOCK"}]
        with session_scope() as session:
            add_notification(
                session,
                notification_type="GLOBAL_RISK",
                title="After-market global risk scan completed",
                body=(
                    f"Risk level: {result.risk_level}; multiplier: {result.position_size_multiplier:.2f}; "
                    f"active signals: {', '.join(active) or 'none'}. {result.summary_message}"
                ),
                color=self._risk_notification_color(result.risk_level),
            )
        return {
            "enabled": True,
            "risk_level": result.risk_level,
            "position_size_multiplier": result.position_size_multiplier,
            "active_signals": active,
            "summary_message": result.summary_message,
        }

    def run_global_risk_scan_pre_market(self) -> dict[str, object]:
        if not settings.global_risk_scanner_enabled:
            return {"enabled": False}
        today_local = self._today_local()
        if reason := self._holiday_reason(today_local):
            logger.info("Skipping pre-market global risk scan because %s", reason)
            return {"enabled": True, "skipped": True, "reason": reason}
        prior_levels = self._planned_cutover_risk_levels_for_day(today_local)
        result = self.global_risk_scanner.scan(today_local, scan_type="PRE_MARKET")
        active = [signal.name for signal in result.signals if signal.severity in {"CAUTION", "BLOCK"}]
        cancelled = 0
        if result.risk_level == "RED":
            cancelled = self.official_investment_recommendation_engine.cancel_planned_recommendations_for_day(
                planned_for=today_local
            )
        with session_scope() as session:
            add_notification(
                session,
                notification_type="GLOBAL_RISK",
                title="Pre-market global risk scan completed",
                body=(
                    f"Risk level: {result.risk_level}; multiplier: {result.position_size_multiplier:.2f}; "
                    f"active signals: {', '.join(active) or 'none'}. "
                    f"Prior after-market plan risk levels: {', '.join(prior_levels) or 'none'}. "
                    + (
                        f"Cancelled {cancelled} planned official investment trade(s) for today due to RED overnight risk."
                        if result.risk_level == "RED"
                        else "No planned investment trades were cancelled."
                    )
                ),
                color=self._risk_notification_color(result.risk_level),
            )
        return {
            "enabled": True,
            "risk_level": result.risk_level,
            "position_size_multiplier": result.position_size_multiplier,
            "active_signals": active,
            "summary_message": result.summary_message,
            "cancelled_plans": cancelled,
            "prior_plan_risk_levels": prior_levels,
        }

    def refresh_official_investment_scores_shadow(self, *, as_of_date: date | None = None) -> dict[str, object]:
        if not settings.official_investment_shadow_enabled:
            return {"enabled": False}
        result = self.investment_scorer.score_universe(as_of_date=as_of_date)
        resolved_as_of_date = date.fromisoformat(str(result["as_of_date"])) if result.get("as_of_date") else None
        phase3 = self.refresh_official_investment_gates_shadow(as_of_date=resolved_as_of_date)
        with session_scope() as session:
            add_notification(
                session,
                notification_type="OFFICIAL_DATA_SHADOW",
                title="Official investment scores refreshed",
                body=(
                    f"Processed {int(result.get('processed') or 0)} symbols for {result.get('as_of_date') or 'latest data'}. "
                    f"Strong buy: {int(result.get('strong_buy') or 0)}, "
                    f"watchlist: {int(result.get('watchlist') or 0)}, "
                    f"no action: {int(result.get('no_action') or 0)}."
                ),
                color="blue",
            )
        return {**result, "phase3": phase3}

    def refresh_official_investment_gates_shadow(self, *, as_of_date: date | None = None) -> dict[str, object]:
        if not settings.official_investment_shadow_enabled:
            return {"enabled": False}
        result = self.investment_gate_runner.run_universe(as_of_date=as_of_date)
        logger.info(
            "Official investment gate shadow batch for %s: strong_buy=%s buy=%s skip=%s blocked_market=%s blocked_sector=%s blocked_earnings=%s blocked_promoter=%s blocked_entry=%s",
            result.get("as_of_date"),
            int(result.get("eligible_strong_buy") or 0),
            int(result.get("buy") or 0),
            int(result.get("skip") or 0),
            int(result.get("blocked_by_market_health") or 0),
            int(result.get("blocked_by_sector_strength") or 0),
            int(result.get("blocked_by_earnings_proximity") or 0),
            int(result.get("blocked_by_promoter") or 0),
            int(result.get("blocked_by_entry_trigger") or 0),
        )
        return result

    def refresh_official_market_context_shadow(self) -> dict[str, object]:
        if not settings.official_investment_shadow_enabled:
            return {"enabled": False}
        today_local = self._today_local()
        if reason := self._holiday_reason(today_local):
            logger.info("Skipping official market-context shadow sync because %s", reason)
            return {"enabled": True, "skipped": True, "reason": reason}
        actions = self.official_investment_data_service.refresh_corporate_actions()
        market_context = self.official_investment_data_service.refresh_market_context()
        rebuild = self.official_snapshot_builder.rebuild_daily_snapshot(
            as_of_date=date.fromisoformat(str(market_context["as_of_date"]))
        )
        scores = self.refresh_official_investment_scores_shadow(
            as_of_date=date.fromisoformat(str(market_context["as_of_date"]))
        )
        cutover_recommendations: list[dict[str, object]] = []
        cutover_summary: dict[str, object] = {}
        if settings.official_investment_cutover_enabled:
            cutover_recommendations = self.generate_after_market_investment_recommendations(
                as_of_date=date.fromisoformat(str(market_context["as_of_date"]))
            )
            cutover_summary = dict(self._last_official_investment_cutover_summary or {})
        summary = self.shadow_comparison_service.compare(
            as_of_date=date.fromisoformat(str(market_context["as_of_date"])),
        )
        with session_scope() as session:
            add_notification(
                session,
                notification_type="OFFICIAL_DATA_SHADOW",
                title="Official market context shadow sync completed",
                body=(
                    f"Stored {int(actions['stored'])} corporate-action rows and refreshed market context with "
                    f"{int(market_context['sector_context_count'])} sector targets. "
                    f"Official coverage is {int(summary.get('officialCoverage') or 0)} symbols."
                ),
                color="blue",
            )
            if settings.official_investment_cutover_enabled:
                risk_suffix = (
                    f" Global risk {cutover_summary.get('global_risk_level') or 'UNKNOWN'} "
                    f"with size multiplier {float(cutover_summary.get('position_size_multiplier') or 0.0):.2f}."
                )
                active_signals = list(cutover_summary.get("active_global_signals") or [])
                if active_signals:
                    risk_suffix += f" Active signals: {', '.join(active_signals)}."
                if cutover_summary.get("risk_summary_message"):
                    risk_suffix += f" {cutover_summary.get('risk_summary_message')}"
                add_notification(
                    session,
                    notification_type="INVESTMENT_SCAN",
                    title="Official investment cutover completed",
                    body=(
                        f"Strong buy candidates: {int(cutover_summary.get('strong_buy_candidates') or 0)}; "
                        f"Phase 3 approved buys: {int(cutover_summary.get('phase3_buy_candidates') or 0)}; "
                        f"planned investment trades created: {int(cutover_summary.get('created') or 0)}. "
                        f"Blocked by market/sector/earnings/promoter/entry: "
                        f"{int(cutover_summary.get('blocked_by_market_health') or 0)}/"
                        f"{int(cutover_summary.get('blocked_by_sector_strength') or 0)}/"
                        f"{int(cutover_summary.get('blocked_by_earnings_proximity') or 0)}/"
                        f"{int(cutover_summary.get('blocked_by_promoter') or 0)}/"
                        f"{int(cutover_summary.get('blocked_by_entry_trigger') or 0)}."
                        + (
                            " Zero approved buys remained after cutover filtering."
                            if int(cutover_summary.get("created") or 0) == 0
                            else ""
                        )
                        + risk_suffix
                    ),
                    color=self._risk_notification_color(str(cutover_summary.get("global_risk_level") or "BLUE")),
                )
        return {
            "actions": actions,
            "market_context": market_context,
            "rebuild": rebuild,
            "scores": scores,
            "summary": summary,
            "cutover_recommendations": cutover_recommendations,
            "cutover_summary": cutover_summary,
        }

    def refresh_official_weekly_fundamentals_shadow(self) -> dict[str, object]:
        if not settings.official_investment_shadow_enabled:
            return {"enabled": False}
        result = self.official_investment_data_service.refresh_weekly_fundamentals(
            symbol_configs=self.historical_fetcher.select_symbols(limit=None),
        )
        rebuild = self.official_snapshot_builder.rebuild_daily_snapshot()
        score_as_of_date = date.fromisoformat(str(rebuild["as_of_date"])) if rebuild.get("as_of_date") else None
        scores = self.refresh_official_investment_scores_shadow(as_of_date=score_as_of_date)
        cutover_recommendations: list[dict[str, object]] = []
        cutover_summary: dict[str, object] = {}
        if settings.official_investment_cutover_enabled and score_as_of_date is not None:
            cutover_recommendations = self.generate_after_market_investment_recommendations(as_of_date=score_as_of_date)
            cutover_summary = dict(self._last_official_investment_cutover_summary or {})
        summary = self.shadow_comparison_service.compare(
            missing_bse_mapping_symbols=list(result.get("missing_bse_mappings") or []),
            recovered_by_bse_count=int(result.get("recovered_by_bse") or 0),
        )
        with session_scope() as session:
            add_notification(
                session,
                notification_type="OFFICIAL_DATA_SHADOW",
                title="Official weekly fundamentals shadow sync completed",
                body=(
                    f"Processed {int(result['processed'])} symbols, stored {int(result['stored_periods'])} financial periods and "
                    f"{int(result['stored_shareholding'])} shareholding snapshots. "
                    f"Next weekly offset is {int(result['next_offset'])}. "
                    f"Shadow comparison now covers {int(summary.get('coverageCompared') or 0)} symbols."
                ),
                color="blue",
            )
            if settings.official_investment_cutover_enabled:
                risk_suffix = (
                    f" Global risk {cutover_summary.get('global_risk_level') or 'UNKNOWN'} "
                    f"with size multiplier {float(cutover_summary.get('position_size_multiplier') or 0.0):.2f}."
                )
                active_signals = list(cutover_summary.get("active_global_signals") or [])
                if active_signals:
                    risk_suffix += f" Active signals: {', '.join(active_signals)}."
                add_notification(
                    session,
                    notification_type="INVESTMENT_SCAN",
                    title="Official investment cutover refreshed after weekly fundamentals",
                    body=(
                        f"Strong buy candidates: {int(cutover_summary.get('strong_buy_candidates') or 0)}; "
                        f"Phase 3 approved buys: {int(cutover_summary.get('phase3_buy_candidates') or 0)}; "
                        f"planned investment trades created: {int(cutover_summary.get('created') or 0)}."
                        + risk_suffix
                    ),
                    color=self._risk_notification_color(str(cutover_summary.get("global_risk_level") or "BLUE")),
                )
        return {
            "weekly": result,
            "rebuild": rebuild,
            "scores": scores,
            "summary": summary,
            "cutover_recommendations": cutover_recommendations,
            "cutover_summary": cutover_summary,
        }

    def generate_after_market_investment_recommendations(
        self,
        universe_limit: int | None = None,
        top_n: int = 10,
        *,
        as_of_date: date | None = None,
    ) -> list[dict]:
        if settings.official_investment_cutover_enabled:
            summary = self.official_investment_recommendation_engine.rebuild_planned_recommendations(
                as_of_date=as_of_date,
                top_n=top_n,
            )
            self._last_official_investment_cutover_summary = self.official_investment_recommendation_engine.asdict(summary)
            self._last_investment_scan_error_count = len(summary.failed_examples)
            self._last_investment_scan_error_examples = list(summary.failed_examples.values())[:3]
            return list(summary.recommendations)

        now = datetime.now(tz=settings.tzinfo)
        symbols = self.historical_fetcher.select_symbols(limit=None)
        universe_limit = universe_limit or self.INVESTMENT_UNIVERSE_LIMIT
        symbol_names = [config.symbol for config in symbols]
        candidates: list[dict] = []
        reserve_candidates: list[dict] = []
        scan_errors: list[str] = []
        with session_scope() as session:
            investment_rows = session.scalars(
                select(PaperTrade).where(
                    PaperTrade.signal_type == "INVESTMENT",
                    PaperTrade.exit_date.is_(None),
                )
            ).all()
            existing_symbols: set[str] = set()
            for row in investment_rows:
                metadata = row.metadata_json or {}
                if metadata.get("plan_only"):
                    session.delete(row)
                    continue
                if row.stock_symbol:
                    existing_symbols.add(row.stock_symbol)
            strategy_rows = (
                session.scalars(
                    select(StockStrategyMap).where(
                        StockStrategyMap.symbol.in_(symbol_names) if symbol_names else False,
                    )
                ).all()
                if symbol_names
                else []
            )
            strategy_map = {row.symbol: row for row in strategy_rows if row.symbol}
        preferred_priority = {symbol.upper(): index for index, symbol in enumerate(PREFERRED_BATCH_SYMBOLS)}
        symbols.sort(
            key=lambda config: (
                0 if config.symbol.upper() in preferred_priority else 1,
                preferred_priority.get(config.symbol.upper(), 9999),
                -float((strategy_map.get(config.symbol).composite_score or 0.0) if strategy_map.get(config.symbol) else -999.0),
                -float((strategy_map.get(config.symbol).win_rate or 0.0) if strategy_map.get(config.symbol) else 0.0),
                config.symbol,
            )
        )
        if universe_limit is not None and universe_limit > 0:
            symbols = symbols[:universe_limit]

        def investment_technical_bonus(frame) -> float:
            latest = frame.iloc[-1]
            score = 0.0
            close = float(latest.get("Close", 0.0) or 0.0)
            sma_200 = float(latest.get("SMA_200", close) or close)
            sma_50 = float(latest.get("SMA_50", close) or close)
            ema_20 = float(latest.get("EMA_20", close) or close)
            rsi = float(latest.get("RSI_14", 50.0) or 50.0)
            adx = float(latest.get("ADX", 0.0) or 0.0)
            if close > sma_200:
                score += 5.0
            if sma_50 > sma_200:
                score += 4.0
            if close > ema_20:
                score += 2.0
            if 48.0 <= rsi <= 68.0:
                score += 2.0
            if adx >= 20.0:
                score += 2.0
            if self._volume_ratio_from_frame(frame) >= 1.2:
                score += 1.5
            return min(score, 12.0)

        def is_excluded_investment_symbol(symbol: str) -> bool:
            upper = symbol.upper()
            return upper.endswith(("-RE", "-SM", "-BZ", "-BE", "-BL"))

        def investment_liquidity_profile(frame) -> dict[str, float]:
            latest = frame.iloc[-1]
            tail = frame.tail(20)
            close = float(latest.get("Close", 0.0) or 0.0)
            avg_volume = float(tail["Volume"].fillna(0.0).mean()) if "Volume" in tail else 0.0
            avg_close = float(tail["Close"].fillna(0.0).mean()) if "Close" in tail else close
            avg_turnover = avg_close * avg_volume
            return {
                "close": close,
                "avg_volume": avg_volume,
                "avg_turnover": avg_turnover,
            }

        def investment_candidate_rank(signal: dict, frame, strategy_row: StockStrategyMap | None) -> float:
            raw_metrics = dict(signal.get("fundamental_raw_metrics") or {})
            rank = float(signal.get("confidence_score") or 0.0)
            rank += investment_technical_bonus(frame)
            rank += self.INVESTMENT_STRATEGY_BONUS.get(str(signal.get("strategy_name") or ""), 0.0)
            rank += max(0.0, (float(signal.get("fundamental_quality_score") or 0.0) - 0.50) * 22.0)
            rank += max(0.0, (float(signal.get("business_outlook_score") or 0.0) - 0.50) * 20.0)
            rank += max(0.0, (float(signal.get("selection_score") or 0.0) - 0.50) * 20.0)
            rank += max(0.0, (float(signal.get("valuation_score") or 0.0) - 0.50) * 16.0)
            rank += max(0.0, (float(signal.get("sector_score") or 0.0) - 0.45) * 16.0)
            rank += max(0.0, float(signal.get("news_score_at_entry") or 0.0)) * 2.5
            if signal.get("fundamental_has_snapshot"):
                rank += 4.0
            if str(signal.get("valuation_label") or "") == "CHEAP":
                rank += 4.0
            elif str(signal.get("valuation_label") or "") == "EXPENSIVE":
                rank -= 5.0
            if str(signal.get("selection_label") or "") == "HIGH_CONVICTION":
                rank += 5.0
            elif str(signal.get("selection_label") or "") == "AVOID":
                rank -= 8.0
            if strategy_row is not None:
                rank += max(0.0, float(strategy_row.composite_score or 0.0)) * 6.0
                rank += max(0.0, float(strategy_row.sharpe_ratio or 0.0)) * 1.5
                rank += max(0.0, (float(strategy_row.win_rate or 0.0) - 0.45)) * 14.0
                if strategy_row.best_strategy == signal.get("strategy_name"):
                    rank += 5.0
                if float(strategy_row.composite_score or 0.0) < 0:
                    rank -= 6.0
            else:
                rank -= 4.0
            pe_ratio = raw_metrics.get("pe_ratio")
            pb_ratio = raw_metrics.get("pb_ratio")
            debt_to_equity = raw_metrics.get("debt_to_equity")
            roe = raw_metrics.get("roe")
            if isinstance(pe_ratio, (int, float)) and 0 < float(pe_ratio) <= 28:
                rank += 3.0
            if isinstance(pb_ratio, (int, float)) and 0 < float(pb_ratio) <= 4.5:
                rank += 2.0
            if isinstance(debt_to_equity, (int, float)) and float(debt_to_equity) <= 0.8:
                rank += 2.0
            if isinstance(roe, (int, float)) and float(roe) >= 15.0:
                rank += 3.0
            days_to_earnings = signal.get("days_to_earnings")
            if isinstance(days_to_earnings, int) and 0 <= days_to_earnings <= 5:
                rank -= 6.0
            return rank

        for symbol_config in symbols:
            if symbol_config.symbol in existing_symbols:
                continue
            if is_excluded_investment_symbol(symbol_config.symbol):
                continue
            try:
                frame = self.historical_fetcher.fetch_symbol_frame(symbol_config)
                if frame.empty or len(frame) < 200:
                    continue
                latest = frame.iloc[-1]
                liquidity = investment_liquidity_profile(frame)
                strategy_row = strategy_map.get(symbol_config.symbol)
                close = liquidity["close"]
                avg_volume = liquidity["avg_volume"]
                avg_turnover = liquidity["avg_turnover"]
                sma_50 = float(latest.get("SMA_50", close) or close)
                sma_200 = float(latest.get("SMA_200", close) or close)
                ema_20 = float(latest.get("EMA_20", close) or close)
                regime = detect_regime(frame)
                if close < self.INVESTMENT_MIN_PRICE:
                    continue
                if avg_volume < self.INVESTMENT_MIN_AVG_VOLUME:
                    continue
                turnover_floor = self.INVESTMENT_MIN_AVG_TURNOVER if close >= 100 else self.INVESTMENT_MIN_AVG_TURNOVER * 1.25
                if avg_turnover < turnover_floor:
                    continue
                if strategy_row is not None and float(strategy_row.composite_score or 0.0) < -0.35:
                    continue
                news_score = self.news_fetcher.get_sentiment_for_date(symbol_config.symbol, now)
                best_signal: dict | None = None
                best_rank = float("-inf")
                for strategy_name in self.INVESTMENT_STRATEGY_CANDIDATES:
                    signal = self.signal_engine.evaluate_symbol(
                        symbol_config.symbol,
                        frame,
                        news_score=news_score,
                        fundamental_score=0.5,
                        signal_type_override="INVESTMENT",
                        strategy_name_override=strategy_name,
                        open_trade=False,
                        long_only=True,
                        opened_from="after_market_investment_scan",
                        company_name=symbol_config.company_name,
                    )
                    if signal is None:
                        continue
                    confidence = float(signal.get("confidence_score") or 0.0)
                    selection_score = float(signal.get("selection_score") or 0.0)
                    business_outlook = float(signal.get("business_outlook_score") or 0.0)
                    valuation_label = str(signal.get("valuation_label") or "")
                    meets_primary = confidence >= 56.0 or (
                        confidence >= 52.0
                        and selection_score >= 0.62
                        and business_outlook >= 0.58
                        and valuation_label != "EXPENSIVE"
                    )
                    meets_reserve = (
                        confidence >= 50.0
                        and selection_score >= 0.58
                        and business_outlook >= 0.54
                        and valuation_label != "EXPENSIVE"
                    )
                    if not meets_primary:
                        if meets_reserve:
                            reserve_signal = dict(signal)
                            reserve_signal["investment_rank"] = round(investment_candidate_rank(signal, frame, strategy_row) - 4.0, 2)
                            reserve_signal["company_name"] = symbol_config.company_name
                            reserve_candidates.append(reserve_signal)
                        continue
                    sector_score = float(signal.get("sector_score") or 0.0)
                    fundamental_quality = float(signal.get("fundamental_quality_score") or 0.0)
                    has_snapshot = bool(signal.get("fundamental_has_snapshot"))
                    if close < sma_50 and close < ema_20 and sector_score < 0.14 and fundamental_quality < 0.46:
                        continue
                    if (
                        strategy_name == "RSI Mean Reversion"
                        and not has_snapshot
                        and sector_score < 0.12
                        and avg_turnover < 25_000_000.0
                    ):
                        continue
                    if (
                        fundamental_quality < 0.26
                        and sector_score < 0.18
                        and not has_snapshot
                    ):
                        continue
                    if valuation_label == "EXPENSIVE" and business_outlook < 0.58 and fundamental_quality < 0.62:
                        continue
                    rank = investment_candidate_rank(signal, frame, strategy_row)
                    if regime == "TRENDING_BEAR":
                        rank -= 8.0
                    if close < sma_200:
                        rank -= 7.0
                    if close < sma_50:
                        rank -= 3.0
                    if sector_score < 0.18:
                        rank -= 3.0
                    if not has_snapshot:
                        rank -= 1.5
                    signal.setdefault("basis_points", [])
                    signal["basis_points"] = list(signal["basis_points"]) + [
                        f"20-day average turnover is {avg_turnover:,.0f} rupees.",
                        f"20-day average volume is {avg_volume:,.0f} shares.",
                    ]
                    if strategy_row is not None:
                        signal["basis_points"].append(
                            f"Backtest composite score is {float(strategy_row.composite_score or 0.0):.2f} with win rate {(float(strategy_row.win_rate or 0.0) * 100):.0f}%."
                        )
                    if rank > best_rank:
                        best_rank = rank
                        best_signal = signal
                if best_signal is None:
                    continue
                best_signal["company_name"] = symbol_config.company_name
                best_signal["investment_rank"] = round(best_rank, 2)
                candidates.append(best_signal)
            except Exception as exc:
                self._capture_error(scan_errors, symbol=symbol_config.symbol, exc=exc)
                continue

        candidates.sort(
            key=lambda item: (
                float(item.get("investment_rank") or 0.0),
                float(item.get("confidence_score") or 0.0),
                float(item.get("fundamental_quality_score") or 0.0),
            ),
            reverse=True,
        )
        reserve_candidates.sort(
            key=lambda item: (
                float(item.get("investment_rank") or 0.0),
                float(item.get("confidence_score") or 0.0),
                float(item.get("fundamental_quality_score") or 0.0),
            ),
            reverse=True,
        )
        chosen: list[dict] = []
        next_session = self.market_calendar.next_trading_day(self._today_local())
        selection_pool = candidates[:top_n]
        if not selection_pool:
            selection_pool = reserve_candidates[: min(top_n, 5)]
        for signal in selection_pool:
            trade_id = self.signal_engine.paper_trader.plan_signal_trade(signal, planned_for=next_session, activation_window_days=5)
            signal["paper_trade_id"] = trade_id
            signal["paper_trade_status"] = "PLANNED"
            chosen.append(signal)
        self._last_investment_scan_error_count = len(scan_errors)
        self._last_investment_scan_error_examples = scan_errors[:3]
        return chosen

    def weekly_retrain(self) -> None:
        result = self.learning_engine.weekly_retrain()
        with session_scope() as session:
            add_notification(
                session,
                notification_type="LEARNING",
                title="Weekly retraining completed",
                body=f"Model updated: {result['updated']}. Accuracy: {result['accuracy']:.2%}.",
                color="orange",
            )

    def dispatch_phone_alerts(self) -> dict[str, object]:
        return self.alert_dispatcher.dispatch_pending_notifications()

    def generate_daily_report(self) -> dict[str, object]:
        result = self.daily_report_service.generate_daily_report()
        with session_scope() as session:
            add_notification(
                session,
                notification_type="DAILY_REPORT",
                title="Daily report generated",
                body=f"Saved daily report to {result['path']}.",
                color="blue",
            )
        return result

    def create_daily_backup(self) -> dict[str, object]:
        result = self.backup_service.create_daily_backup()
        if result.created:
            with session_scope() as session:
                add_notification(
                    session,
                    notification_type="BACKUP",
                    title="Daily backup created",
                    body=(
                        f"Saved local backup to {result.backup_dir}. "
                        f"Retention: {result.retained_days} days."
                    ),
                    color="blue",
                )
        return {
            "created": result.created,
            "backup_dir": result.backup_dir,
            "tables": result.tables,
            "retained_days": result.retained_days,
            "skipped": result.skipped,
        }

    def stop_after_market_scheduler(self) -> None:
        return

    def start(self) -> None:
        self.configure()
        if not self.market_scheduler.running:
            self.market_scheduler.start()
        if not self.after_market_scheduler.running:
            self.after_market_scheduler.start()

    def shutdown(self) -> None:
        if self.market_scheduler.running:
            self.market_scheduler.shutdown(wait=False)
        if self.after_market_scheduler.running:
            self.after_market_scheduler.shutdown(wait=False)
