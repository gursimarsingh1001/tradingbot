from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from threading import Lock, Thread

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from backend.config import get_settings, to_camel
from backend.data.historical_fetcher import HistoricalFetcher
from backend.data.news_fetcher import article_is_relevant_to_symbol, article_relevance_score, article_source_is_market_relevant
from backend.data.news_fetcher import NewsFetcher
from backend.db.influx import get_influx_store
from backend.db.postgres import NewsArticle, get_db
from backend.engine.event_risk_engine import classify_event_labels


router = APIRouter(prefix="/api/news", tags=["news"])
historical_fetcher = HistoricalFetcher()
news_fetcher = NewsFetcher()
settings = get_settings()
logger = logging.getLogger(__name__)
SYMBOL_REFRESH_MINUTES = 20
FEED_REFRESH_MINUTES = 30
FEED_REFRESH_LIMIT = 30
_refresh_lock = Lock()
_inflight_symbol_refreshes: set[str] = set()
_global_refresh_inflight = False


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class NewsItem(CamelModel):
    id: int
    symbol: str
    source: str | None
    headline: str | None
    body_snippet: str | None
    published_at: datetime | None
    sentiment_label: str | None
    sentiment_score: float | None
    sentiment_confidence: float | None
    url: str | None
    event_flags: list[str] | None = None


class CorrelationPoint(CamelModel):
    date: str
    sentiment_score: float
    price_change_pct: float


class NewsResponse(CamelModel):
    items: list[NewsItem]
    correlation_series: list[CorrelationPoint]


def _refresh_symbol_news_worker(symbol: str, company_name: str, days: int) -> None:
    upper_symbol = symbol.upper()
    try:
        NewsFetcher().fetch_and_store_symbol_news(
            symbol=upper_symbol,
            company_name=company_name,
            from_date=datetime.now(timezone.utc) - timedelta(days=max(days, 7)),
            to_date=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.warning("Background symbol news refresh failed for %s: %s", upper_symbol, exc)
    finally:
        with _refresh_lock:
            _inflight_symbol_refreshes.discard(upper_symbol)


def _refresh_feed_worker(days: int) -> None:
    global _global_refresh_inflight
    try:
        fetcher = NewsFetcher()
        selector = HistoricalFetcher()
        for config in selector.select_symbols(limit=FEED_REFRESH_LIMIT):
            try:
                fetcher.fetch_and_store_symbol_news(
                    symbol=config.symbol,
                    company_name=config.company_name,
                    from_date=datetime.now(timezone.utc) - timedelta(days=max(days, 7)),
                    to_date=datetime.now(timezone.utc),
                )
            except Exception as exc:
                logger.warning("Background feed refresh failed for %s: %s", config.symbol, exc)
    finally:
        with _refresh_lock:
            _global_refresh_inflight = False


def _schedule_symbol_refresh(symbol: str, company_name: str, days: int) -> None:
    upper_symbol = symbol.upper()
    with _refresh_lock:
        if upper_symbol in _inflight_symbol_refreshes:
            return
        _inflight_symbol_refreshes.add(upper_symbol)
    Thread(
        target=_refresh_symbol_news_worker,
        args=(upper_symbol, company_name, days),
        daemon=True,
        name=f"news-refresh-{upper_symbol}",
    ).start()


def _schedule_feed_refresh(days: int) -> None:
    global _global_refresh_inflight
    with _refresh_lock:
        if _global_refresh_inflight:
            return
        _global_refresh_inflight = True
    Thread(
        target=_refresh_feed_worker,
        args=(days,),
        daemon=True,
        name="news-refresh-feed",
    ).start()


@router.get("/latest", response_model=NewsResponse)
def get_latest_news(
    symbol: str | None = None,
    sentiment: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=90),
    db: Session = Depends(get_db),
) -> NewsResponse:
    stmt = select(NewsArticle).order_by(NewsArticle.published_at.desc()).limit(200 if symbol else 1500)
    if not symbol:
        latest_global = db.scalar(select(NewsArticle).order_by(NewsArticle.published_at.desc()))
        latest_global_timestamp = latest_global.published_at if latest_global is not None else None
        refresh_cutoff = datetime.now(timezone.utc) - timedelta(minutes=FEED_REFRESH_MINUTES)
        if latest_global_timestamp is not None and latest_global_timestamp.tzinfo is None:
            latest_global_timestamp = latest_global_timestamp.replace(tzinfo=timezone.utc)
        if latest_global_timestamp is None or latest_global_timestamp < refresh_cutoff:
            _schedule_feed_refresh(days)
    if symbol:
        symbol_map = historical_fetcher.load_symbol_map()
        config = symbol_map.get(symbol.upper())
        if config is not None:
            latest_for_symbol = db.scalar(
                select(NewsArticle)
                .where(NewsArticle.symbol == symbol.upper())
                .order_by(NewsArticle.published_at.desc())
            )
            latest_timestamp = latest_for_symbol.published_at if latest_for_symbol is not None else None
            refresh_cutoff = datetime.now(timezone.utc) - timedelta(minutes=SYMBOL_REFRESH_MINUTES)
            if latest_timestamp is not None and latest_timestamp.tzinfo is None:
                latest_timestamp = latest_timestamp.replace(tzinfo=timezone.utc)
            if latest_timestamp is None or latest_timestamp < refresh_cutoff:
                _schedule_symbol_refresh(config.symbol, config.company_name, days)
        stmt = stmt.where(NewsArticle.symbol == symbol)
    if sentiment:
        stmt = stmt.where(NewsArticle.sentiment_label == sentiment.upper())
    scored_items = [
        (
            article_relevance_score(
                item.headline,
                item.body_snippet,
                symbol=item.symbol,
                company_name=item.company_name,
            ),
            item,
        )
        for item in db.scalars(stmt).all()
        if article_source_is_market_relevant(item.source, item.url)
        if article_is_relevant_to_symbol(
            item.headline,
            item.body_snippet,
            symbol=item.symbol,
            company_name=item.company_name,
        )
    ]
    scored_items.sort(
        key=lambda row: (
            row[0],
            (row[1].published_at.replace(tzinfo=timezone.utc).timestamp() if row[1].published_at and row[1].published_at.tzinfo is None else row[1].published_at.timestamp())
            if row[1].published_at
            else 0.0,
        ),
        reverse=True,
    )
    items = [item for _, item in scored_items]
    if not symbol:
        per_symbol_counts: dict[str, int] = {}
        diversified_items: list[NewsArticle] = []
        for item in items:
            count = per_symbol_counts.get(item.symbol, 0)
            if count >= 2:
                continue
            diversified_items.append(item)
            per_symbol_counts[item.symbol] = count + 1
            if len(diversified_items) >= 200:
                break
        items = diversified_items
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    raw_series_rows = db.scalars(
        select(NewsArticle)
        .where(
            and_(
                NewsArticle.symbol == (symbol or NewsArticle.symbol),
                NewsArticle.published_at >= cutoff,
            )
        )
        .order_by(NewsArticle.published_at.asc())
    ).all()
    series_rows = [
        row
        for row in raw_series_rows
        if article_source_is_market_relevant(row.source, row.url)
        if article_relevance_score(
            row.headline,
            row.body_snippet,
            symbol=row.symbol,
            company_name=row.company_name,
        ) >= settings.news_relevance_threshold
    ]
    daily_price_change: dict[str, float] = {}
    if symbol:
        influx = get_influx_store()
        price_frame = influx.query_symbol_history(symbol, start=cutoff, stop=datetime.now(timezone.utc))
        if not price_frame.empty and "Close" in price_frame.columns:
            price_frame = price_frame.copy()
            price_frame["date"] = price_frame.index.date.astype(str)
            price_frame["priceChangePct"] = price_frame["Close"].pct_change().fillna(0.0) * 100
            daily_price_change = {
                row["date"]: float(row["priceChangePct"])
                for _, row in price_frame[["date", "priceChangePct"]].iterrows()
            }
    correlation = [
        CorrelationPoint(
            date=row.published_at.date().isoformat() if row.published_at else "",
            sentiment_score=float(row.sentiment_score or 0.0),
            price_change_pct=float(daily_price_change.get(row.published_at.date().isoformat(), 0.0)) if row.published_at else 0.0,
        )
        for row in series_rows
    ]
    parsed_items = []
    for item in items:
        event_flags = classify_event_labels(f"{item.headline or ''} {item.body_snippet or ''}")
        parsed_items.append(
            NewsItem(
                id=item.id,
                symbol=item.symbol,
                source=item.source,
                headline=item.headline,
                body_snippet=item.body_snippet,
                published_at=item.published_at,
                sentiment_label=item.sentiment_label,
                sentiment_score=item.sentiment_score,
                sentiment_confidence=item.sentiment_confidence,
                url=item.url,
                event_flags=event_flags,
            )
        )
    return NewsResponse(items=parsed_items, correlation_series=correlation)
