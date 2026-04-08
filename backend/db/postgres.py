from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time, UniqueConstraint, create_engine, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from backend.config import get_settings


settings = get_settings()
engine = create_engine(settings.postgres_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockStrategyMap(Base):
    __tablename__ = "stock_strategy_map"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    best_strategy: Mapped[str | None] = mapped_column(String(50))
    sharpe_ratio: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    total_return: Mapped[float | None] = mapped_column(Float)
    composite_score: Mapped[float | None] = mapped_column(Float)
    regime_performed_best: Mapped[str | None] = mapped_column(String(30))
    avg_holding_days: Mapped[int | None] = mapped_column(Integer)
    sentiment_direction_best: Mapped[str | None] = mapped_column(String(20))
    best_quarter: Mapped[str | None] = mapped_column(String(10))
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperTrade(Base, TimestampMixin):
    __tablename__ = "paper_trades"

    trade_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    stock_symbol: Mapped[str | None] = mapped_column(String(20), index=True)
    strategy_name: Mapped[str | None] = mapped_column(String(50), index=True)
    signal_type: Mapped[str | None] = mapped_column(String(20))
    entry_date: Mapped[datetime | None] = mapped_column(Date)
    entry_time: Mapped[datetime | None] = mapped_column(Time)
    exit_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_zone_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_zone_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    target_1: Mapped[float | None] = mapped_column(Float)
    target_2: Mapped[float | None] = mapped_column(Float)
    target_3: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer)
    pnl_rupees: Mapped[float | None] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float | None] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    regime_at_entry: Mapped[str | None] = mapped_column(String(30))
    news_score_at_entry: Mapped[float | None] = mapped_column(Float)
    pattern_name: Mapped[str | None] = mapped_column(String(50))
    exit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    was_profitable: Mapped[bool | None] = mapped_column(Boolean, default=False)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    targets_hit: Mapped[dict[str, bool] | None] = mapped_column(JSONB, default=dict)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, default=dict)

    mistakes: Mapped[list["MistakeLog"]] = relationship(back_populates="trade")


class ScoringWeight(Base, TimestampMixin):
    __tablename__ = "scoring_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_weight: Mapped[float] = mapped_column(Float)
    ma_weight: Mapped[float] = mapped_column(Float)
    volume_weight: Mapped[float] = mapped_column(Float)
    news_weight: Mapped[float] = mapped_column(Float)
    regime_weight: Mapped[float] = mapped_column(Float)
    fundamental_weight: Mapped[float] = mapped_column(Float)
    model_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    type: Mapped[str | None] = mapped_column(String(30))
    title: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(10))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    related_stock: Mapped[str | None] = mapped_column(String(20))


class TomorrowWatchlist(Base, TimestampMixin):
    __tablename__ = "tomorrow_watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str | None] = mapped_column(String(20), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    watch_price: Mapped[float | None] = mapped_column(Float)
    signal_type: Mapped[str | None] = mapped_column(String(20))
    strategy: Mapped[str | None] = mapped_column(String(50))
    created_date: Mapped[datetime | None] = mapped_column(Date, index=True)


class MistakeLog(Base, TimestampMixin):
    __tablename__ = "mistakes_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("paper_trades.trade_id"))
    conditions_at_loss: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    adjustment_made: Mapped[str | None] = mapped_column(Text)

    trade: Mapped[PaperTrade | None] = relationship(back_populates="mistakes")


class BacktestTrade(Base, TimestampMixin):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy_name: Mapped[str] = mapped_column(String(50), index=True)
    entry_date: Mapped[datetime | None] = mapped_column(Date)
    exit_date: Mapped[datetime | None] = mapped_column(Date)
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float)
    shares: Mapped[int | None] = mapped_column(Integer)
    pnl_rupees: Mapped[float | None] = mapped_column(Float)
    pnl_pct: Mapped[float | None] = mapped_column(Float)
    news_score_at_entry: Mapped[float | None] = mapped_column(Float)
    regime_at_entry: Mapped[str | None] = mapped_column(String(30))
    pattern_at_entry: Mapped[str | None] = mapped_column(String(50))
    quarter_at_entry: Mapped[str | None] = mapped_column(String(10))
    holding_days: Mapped[int | None] = mapped_column(Integer)
    exit_reason: Mapped[str | None] = mapped_column(String(30))


class NewsArticle(Base, TimestampMixin):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    company_name: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str | None] = mapped_column(String(50))
    headline: Mapped[str | None] = mapped_column(String(500))
    body_snippet: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sentiment_label: Mapped[str | None] = mapped_column(String(20))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)


class StockFundamentalSnapshot(Base, TimestampMixin):
    __tablename__ = "stock_fundamental_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    company_name: Mapped[str | None] = mapped_column(String(150))
    sector: Mapped[str | None] = mapped_column(String(80), index=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    earnings_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    revenue_growth_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_growth_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)
    roce: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoter_holding: Mapped[float | None] = mapped_column(Float, nullable=True)
    pledged_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class OfficialQuoteSnapshot(Base, TimestampMixin):
    __tablename__ = "official_quote_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "as_of_date", name="uq_official_quote_snapshots_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    company_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    source_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    used_bse_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    industry_pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    week_52_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    week_52_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, default=dict)


class OfficialFinancialPeriod(Base, TimestampMixin):
    __tablename__ = "official_financial_periods"
    __table_args__ = (UniqueConstraint("symbol", "period_type", "period_end", name="uq_official_financial_periods_symbol_type_end"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    period_type: Mapped[str] = mapped_column(String(20), index=True)
    fiscal_label: Mapped[str | None] = mapped_column(String(40), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(Date, index=True)
    earnings_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    source_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    ebit: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_basic: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_debt: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_assets: Mapped[float | None] = mapped_column(Float, nullable=True)
    shareholder_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    capital_employed: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_assets: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_liabilities: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    asset_turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    roa: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares_outstanding: Mapped[float | None] = mapped_column(Float, nullable=True)
    npa_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    capital_adequacy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)


class OfficialShareholdingSnapshot(Base, TimestampMixin):
    __tablename__ = "official_shareholding_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "as_of_date", name="uq_official_shareholding_snapshots_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    promoter_holding: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoter_pledge: Mapped[float | None] = mapped_column(Float, nullable=True)
    fii_holding: Mapped[float | None] = mapped_column(Float, nullable=True)
    dii_holding: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)


class OfficialCorporateAction(Base, TimestampMixin):
    __tablename__ = "official_corporate_actions"
    __table_args__ = (UniqueConstraint("symbol", "ex_date", "action_type", name="uq_official_corporate_actions_symbol_date_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    ex_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    action_type: Mapped[str] = mapped_column(String(30), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)


class OfficialMarketContextSnapshot(Base, TimestampMixin):
    __tablename__ = "official_market_context_snapshots"
    __table_args__ = (UniqueConstraint("as_of_date", name="uq_official_market_context_snapshots_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    nifty50_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    nifty50_sma200: Mapped[float | None] = mapped_column(Float, nullable=True)
    india_vix: Mapped[float | None] = mapped_column(Float, nullable=True)
    aaa_bond_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)


class OfficialInvestmentSnapshot(Base, TimestampMixin):
    __tablename__ = "official_investment_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "as_of_date", name="uq_official_investment_snapshots_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    company_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    earnings_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    industry_pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    week_52_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    week_52_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_growth_3y_cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_growth_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)
    roce: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoter_holding: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoter_pledge: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoter_holding_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    fii_holding: Mapped[float | None] = mapped_column(Float, nullable=True)
    dii_holding: Mapped[float | None] = mapped_column(Float, nullable=True)
    fii_holding_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dii_holding_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    npa_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    capital_adequacy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_coverage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    data_sources: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    raw_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)


class ScreenerCache(Base, TimestampMixin):
    __tablename__ = "screener_cache"
    __table_args__ = (UniqueConstraint("symbol", name="uq_screener_cache_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    company_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    screener_slug: Mapped[str | None] = mapped_column(String(180), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    data_json: Mapped[dict[str, Any] | None] = mapped_column("data", JSONB, default=dict)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)


class LynchScore(Base, TimestampMixin):
    __tablename__ = "lynch_scores"
    __table_args__ = (UniqueConstraint("symbol", "as_of_date", name="uq_lynch_scores_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    lynch_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_growth_3y_cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    vote_yes: Mapped[bool] = mapped_column(Boolean, default=False)
    data_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSONB, default=list)
    details_json: Mapped[dict[str, Any] | None] = mapped_column("details", JSONB, default=dict)


class PiotroskiScore(Base, TimestampMixin):
    __tablename__ = "piotroski_scores"
    __table_args__ = (UniqueConstraint("symbol", "as_of_date", name="uq_piotroski_scores_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    f_score: Mapped[int] = mapped_column(Integer, default=0)
    vote_yes: Mapped[bool] = mapped_column(Boolean, default=False)
    data_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSONB, default=list)
    signals_json: Mapped[dict[str, Any] | None] = mapped_column("signals", JSONB, default=dict)


class MinerviniScore(Base, TimestampMixin):
    __tablename__ = "minervini_scores"
    __table_args__ = (UniqueConstraint("symbol", "as_of_date", name="uq_minervini_scores_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    passed_checks: Mapped[int] = mapped_column(Integer, default=0)
    vote_yes: Mapped[bool] = mapped_column(Boolean, default=False)
    rs_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSONB, default=list)
    checks_json: Mapped[dict[str, Any] | None] = mapped_column("checks", JSONB, default=dict)


class GlobalRiskSnapshot(Base, TimestampMixin):
    __tablename__ = "global_risk_snapshots"
    __table_args__ = (UniqueConstraint("as_of_date", "scan_type", name="uq_global_risk_snapshot_date_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[datetime | None] = mapped_column(Date, index=True)
    scan_type: Mapped[str] = mapped_column(String(20), index=True)
    risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    position_size_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)
    vix_current: Mapped[float | None] = mapped_column(Float, nullable=True)
    vix_5day_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    vix_velocity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    vix_severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nifty_prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    nifty_today_open: Mapped[float | None] = mapped_column(Float, nullable=True)
    nifty_gap_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    nifty_gap_severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fii_net_today_crores: Mapped[float | None] = mapped_column(Float, nullable=True)
    fii_consecutive_sell_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fii_cumulative_5day_crores: Mapped[float | None] = mapped_column(Float, nullable=True)
    fii_severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sp500_prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    sp500_latest_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    sp500_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sp500_severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    crude_prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    crude_latest_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    crude_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    crude_severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    usdinr_prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    usdinr_latest_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    usdinr_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    usdinr_severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    active_signals: Mapped[list[str] | None] = mapped_column(JSONB, default=list)
    signal_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)


class BotConfig(Base, TimestampMixin):
    __tablename__ = "bot_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_postgres() -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE official_financial_periods ADD COLUMN IF NOT EXISTS total_assets DOUBLE PRECISION")
        )
        connection.execute(
            text("ALTER TABLE official_investment_snapshots ADD COLUMN IF NOT EXISTS pb_ratio DOUBLE PRECISION")
        )
        connection.execute(
            text("ALTER TABLE official_investment_snapshots ADD COLUMN IF NOT EXISTS data_sources JSONB DEFAULT '{}'::jsonb")
        )
    with session_scope() as session:
        ensure_default_scoring_weights(session)
        ensure_default_config(session)


def ensure_default_scoring_weights(session: Session) -> None:
    existing = session.scalar(select(ScoringWeight).order_by(ScoringWeight.created_at.desc()))
    if existing:
        return
    session.add(
        ScoringWeight(
            pattern_weight=0.28,
            ma_weight=0.18,
            volume_weight=0.16,
            news_weight=0.14,
            regime_weight=0.14,
            fundamental_weight=0.10,
            model_accuracy=0.0,
            effective_from=datetime.now(tz=settings.tzinfo),
        )
    )


def ensure_default_config(session: Session) -> None:
    defaults = {
        "kill_switch": {"active": False, "reason": None},
        "global_best_strategy": {"name": None, "composite_score": None},
        "peak_portfolio_value": {"value": settings.paper_portfolio_value},
        "backtest_progress": {"active": False, "progress": 0, "message": "Idle"},
        "current_model_accuracy": {"value": 0.0},
        "sector_strength_snapshot": {"generated_at": None, "sectors": {}, "symbols": {}},
        "fundamentals_sync_state": {
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
        "news_sync_state": {
            "lastRunAt": None,
            "lastProcessed": 0,
            "lastInserted": 0,
            "lastLookbackHours": 0,
            "prioritySymbols": [],
            "errors": [],
        },
        "alert_dispatch_state": {
            "lastSentAt": None,
            "lastNotificationId": None,
            "lastDeliveredCount": 0,
            "lastError": None,
        },
        "daily_report_state": {
            "lastGeneratedDate": None,
            "lastReportPath": None,
            "lastSummary": None,
        },
        "backup_state": {
            "lastRunAt": None,
            "lastBackupDate": None,
            "lastBackupDir": None,
            "retainedDays": settings.backup_retention_days,
        },
        settings.official_shadow_quote_state_key: {
            "lastRunAt": None,
            "lastRequested": 0,
            "lastStored": 0,
            "lastRecoveredByBse": 0,
            "missingBseMappings": 0,
            "failedExamples": {},
        },
        settings.official_shadow_weekly_state_key: {
            "lastRunAt": None,
            "lastOffset": 0,
            "nextOffset": 0,
            "lastRequested": 0,
            "lastProcessed": 0,
            "lastStoredPeriods": 0,
            "lastStoredShareholding": 0,
            "lastRecoveredByBse": 0,
            "missingBseMappings": 0,
            "failedExamples": {},
        },
        settings.official_shadow_summary_key: {
            "generatedAt": None,
            "asOfDate": None,
            "officialCoverage": 0,
            "legacyCoverage": 0,
            "missingBseMappings": 0,
            "recoveredByBse": 0,
            "missingFieldCounts": {},
            "materialDifferences": {},
            "sampleSymbols": [],
        },
    }
    for key, value in defaults.items():
        existing = session.get(BotConfig, key)
        if not existing:
            session.add(BotConfig(key=key, value=value))


def get_config_value(session: Session, key: str, default: Any = None) -> Any:
    record = session.get(BotConfig, key)
    if not record:
        return default
    return record.value


def upsert_config_value(session: Session, key: str, value: Any) -> None:
    record = session.get(BotConfig, key)
    if record:
        record.value = value
    else:
        session.add(BotConfig(key=key, value=value))


def add_notification(
    session: Session,
    *,
    notification_type: str,
    title: str,
    body: str,
    color: str,
    related_stock: str | None = None,
) -> Notification:
    notification = Notification(
        type=notification_type,
        title=title,
        body=body,
        color=color,
        related_stock=related_stock,
    )
    session.add(notification)
    session.flush()
    return notification
