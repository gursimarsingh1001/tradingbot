from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "trading-bot"
    app_env: str = "development"
    timezone: str = "Asia/Kolkata"
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8000

    postgres_url: str = Field(
        default="postgresql+psycopg://trading_user:trading_password@localhost:5432/trading_bot",
        alias="POSTGRES_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    influx_url: str = Field(default="http://localhost:8086", alias="INFLUX_URL")
    influx_token: str = Field(default="influx-token", alias="INFLUX_TOKEN")
    influx_org: str = Field(default="trading-bot", alias="INFLUX_ORG")
    influx_bucket: str = Field(default="market-data", alias="INFLUX_BUCKET")

    angel_one_api_key: str = Field(default="", alias="ANGEL_ONE_API_KEY")
    angel_one_client_id: str = Field(default="", alias="ANGEL_ONE_CLIENT_ID")
    angel_one_password: str = Field(default="", alias="ANGEL_ONE_PASSWORD")
    angel_one_totp_secret: str = Field(default="", alias="ANGEL_ONE_TOTP_SECRET")
    news_api_key: str = Field(default="", alias="NEWS_API_KEY")
    alerts_enabled: bool = Field(default=True, alias="ALERTS_ENABLED")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    cors_allowed_origins: str = Field(
        default="http://localhost:4173,http://127.0.0.1:4173,http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ALLOWED_ORIGINS",
    )

    symbols_config_path: Path = Field(
        default=BASE_DIR / "backend" / "config" / "nifty500_symbols.json",
        alias="SYMBOLS_CONFIG_PATH",
    )
    fundamentals_config_path: Path = Field(
        default=BASE_DIR / "backend" / "config" / "fundamental_snapshots.json",
        alias="FUNDAMENTALS_CONFIG_PATH",
    )
    bse_symbol_mapping_path: Path = Field(
        default=BASE_DIR / "backend" / "config" / "bse_symbol_mappings.json",
        alias="BSE_SYMBOL_MAPPING_PATH",
    )
    official_sector_index_config_path: Path = Field(
        default=BASE_DIR / "backend" / "config" / "official_sector_indices.json",
        alias="OFFICIAL_SECTOR_INDEX_CONFIG_PATH",
    )
    market_holidays_path_override: Path | None = Field(default=None, alias="MARKET_HOLIDAYS_PATH")
    market_holidays_dir: Path = Field(default=BASE_DIR / "backend" / "config", alias="MARKET_HOLIDAYS_DIR")
    backups_dir: Path = Field(default=BASE_DIR / "logs" / "backups", alias="BACKUPS_DIR")
    reports_dir: Path = Field(default=BASE_DIR / "logs" / "reports", alias="REPORTS_DIR")
    log_dir: Path = Field(default=BASE_DIR / "logs" / "app", alias="LOG_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_file_name: str = Field(default="trading-bot.log", alias="LOG_FILE_NAME")
    log_max_bytes: int = Field(default=5_000_000, alias="LOG_MAX_BYTES")
    log_backup_count: int = Field(default=5, alias="LOG_BACKUP_COUNT")
    backup_retention_days: int = Field(default=14, alias="BACKUP_RETENTION_DAYS")
    scoring_model_path: Path = BASE_DIR / "backend" / "config" / "scoring_model.pkl"
    paper_portfolio_value: float = Field(default=1_000_000, alias="PAPER_PORTFOLIO_VALUE")
    paper_intraday_allocation_pct: float = Field(default=0.50, alias="PAPER_INTRADAY_ALLOCATION_PCT")
    paper_investment_allocation_pct: float = Field(default=0.50, alias="PAPER_INVESTMENT_ALLOCATION_PCT")
    paper_risk_per_trade_pct: float = Field(default=0.01, alias="PAPER_RISK_PER_TRADE_PCT")
    paper_max_open_risk_pct: float = Field(default=0.05, alias="PAPER_MAX_OPEN_RISK_PCT")
    backtest_brokerage_per_order: float = Field(default=20.0, alias="BACKTEST_BROKERAGE_PER_ORDER")
    backtest_exchange_charge_rate: float = Field(default=0.0000325, alias="BACKTEST_EXCHANGE_CHARGE_RATE")
    backtest_sebi_charge_rate: float = Field(default=0.000001, alias="BACKTEST_SEBI_CHARGE_RATE")
    backtest_gst_rate: float = Field(default=0.18, alias="BACKTEST_GST_RATE")
    backtest_intraday_stt_rate: float = Field(default=0.00025, alias="BACKTEST_INTRADAY_STT_RATE")
    backtest_delivery_stt_rate: float = Field(default=0.001, alias="BACKTEST_DELIVERY_STT_RATE")
    backtest_intraday_stamp_duty_rate: float = Field(default=0.00003, alias="BACKTEST_INTRADAY_STAMP_DUTY_RATE")
    backtest_delivery_stamp_duty_rate: float = Field(default=0.00015, alias="BACKTEST_DELIVERY_STAMP_DUTY_RATE")
    kill_switch_api_health_ttl_seconds: int = Field(default=60, alias="KILL_SWITCH_API_HEALTH_TTL_SECONDS")
    scoring_weights_ttl_seconds: int = Field(default=300, alias="SCORING_WEIGHTS_TTL_SECONDS")
    finbert_preload_on_startup: bool = Field(default=True, alias="FINBERT_PRELOAD_ON_STARTUP")
    finbert_preload_timeout_seconds: float = Field(default=2.0, alias="FINBERT_PRELOAD_TIMEOUT_SECONDS")
    news_relevance_threshold: float = Field(default=0.55, alias="NEWS_RELEVANCE_THRESHOLD")
    news_future_tolerance_hours: int = Field(default=24, alias="NEWS_FUTURE_TOLERANCE_HOURS")
    news_momentum_sentiment_threshold: float = Field(default=0.35, alias="NEWS_MOMENTUM_SENTIMENT_THRESHOLD")
    news_api_daily_soft_limit: int = Field(default=80, alias="NEWS_API_DAILY_SOFT_LIMIT")
    news_scraper_result_limit: int = Field(default=20, alias="NEWS_SCRAPER_RESULT_LIMIT")
    official_investment_shadow_enabled: bool = Field(default=True, alias="OFFICIAL_INVESTMENT_SHADOW_ENABLED")
    official_nse_bootstrap_url: str = Field(default="https://www.nseindia.com", alias="OFFICIAL_NSE_BOOTSTRAP_URL")
    official_nse_api_base_url: str = Field(default="https://www.nseindia.com/api", alias="OFFICIAL_NSE_API_BASE_URL")
    official_bse_api_base_url: str = Field(default="https://api.bseindia.com/BseIndiaAPI/api", alias="OFFICIAL_BSE_API_BASE_URL")
    official_nse_rate_limit_seconds: float = Field(default=2.5, alias="OFFICIAL_NSE_RATE_LIMIT_SECONDS")
    official_bse_rate_limit_seconds: float = Field(default=1.0, alias="OFFICIAL_BSE_RATE_LIMIT_SECONDS")
    official_weekly_batch_size: int = Field(default=300, alias="OFFICIAL_WEEKLY_BATCH_SIZE")
    official_shadow_quote_state_key: str = Field(default="official_quote_sync_state", alias="OFFICIAL_SHADOW_QUOTE_STATE_KEY")
    official_shadow_weekly_state_key: str = Field(default="official_weekly_sync_state", alias="OFFICIAL_SHADOW_WEEKLY_STATE_KEY")
    official_shadow_summary_key: str = Field(default="official_shadow_summary_state", alias="OFFICIAL_SHADOW_SUMMARY_KEY")
    official_aaa_bond_yield: float = Field(default=7.5, alias="OFFICIAL_AAA_BOND_YIELD")
    news_intraday_catalyst_lookback_hours: int = Field(default=18, alias="NEWS_INTRADAY_CATALYST_LOOKBACK_HOURS")
    news_intraday_catalyst_limit: int = Field(default=12, alias="NEWS_INTRADAY_CATALYST_LIMIT")
    news_financial_profit_surge_pct: float = Field(default=80.0, alias="NEWS_FINANCIAL_PROFIT_SURGE_PCT")
    news_financial_revenue_surge_pct: float = Field(default=20.0, alias="NEWS_FINANCIAL_REVENUE_SURGE_PCT")
    news_financial_catalyst_score_threshold: float = Field(default=0.55, alias="NEWS_FINANCIAL_CATALYST_SCORE_THRESHOLD")
    bearish_buy_penalty_points: float = Field(default=16.0, alias="BEARISH_BUY_PENALTY_POINTS")
    bearish_buy_news_override_score: float = Field(default=0.5, alias="BEARISH_BUY_NEWS_OVERRIDE_SCORE")
    bearish_buy_news_penalty_relief: float = Field(default=8.0, alias="BEARISH_BUY_NEWS_PENALTY_RELIEF")
    backtest_target_worker_cpu_fraction: float = Field(default=0.70, alias="BACKTEST_TARGET_WORKER_CPU_FRACTION")
    market_quote_max_change_pct: float = Field(default=0.35, alias="MARKET_QUOTE_MAX_CHANGE_PCT")
    market_quote_max_jump_vs_cache_pct: float = Field(default=0.25, alias="MARKET_QUOTE_MAX_JUMP_VS_CACHE_PCT")
    fundamentals_future_tolerance_days: int = Field(default=3, alias="FUNDAMENTALS_FUTURE_TOLERANCE_DAYS")
    intraday_universe_limit: int = Field(default=47, alias="INTRADAY_UNIVERSE_LIMIT")
    signal_min_confidence: float = Field(default=55.0, alias="SIGNAL_MIN_CONFIDENCE")
    default_recommendation_confidence: float = Field(default=70.0, alias="DEFAULT_RECOMMENDATION_CONFIDENCE")
    watchlist_entry_zone_buffer_pct: float = Field(default=0.0025, alias="WATCHLIST_ENTRY_ZONE_BUFFER_PCT")
    intraday_overbought_rsi_floor: float = Field(default=60.0, alias="INTRADAY_OVERBOUGHT_RSI_FLOOR")
    intraday_overbought_rsi_ceiling: float = Field(default=70.0, alias="INTRADAY_OVERBOUGHT_RSI_CEILING")
    regime_high_volatility_atr_ratio: float = Field(default=1.6, alias="REGIME_HIGH_VOLATILITY_ATR_RATIO")
    regime_vix_high_volatility_change_pct: float = Field(default=0.10, alias="REGIME_VIX_HIGH_VOLATILITY_CHANGE_PCT")
    regime_adx_trend_threshold: float = Field(default=23.0, alias="REGIME_ADX_TREND_THRESHOLD")
    regime_adx_transition_threshold: float = Field(default=18.0, alias="REGIME_ADX_TRANSITION_THRESHOLD")
    regime_trend_slope_lookback: int = Field(default=5, alias="REGIME_TREND_SLOPE_LOOKBACK")

    market_prep_start: str = "09:00"
    market_open_time: str = "09:15"
    intraday_entry_cutoff_time: str = "15:00"
    intraday_cutoff_time: str = "15:15"
    market_close_time: str = "15:30"
    after_market_start: str = "15:35"
    after_market_end: str = "18:30"

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def market_holidays_path(self) -> Path:
        if self.market_holidays_path_override:
            return Path(self.market_holidays_path_override)

        config_dir = Path(self.market_holidays_dir)
        target_name = f"nse_trading_holidays_{datetime.now(tz=self.tzinfo).year}.json"
        target_path = config_dir / target_name
        if target_path.exists():
            return target_path

        candidates = sorted(config_dir.glob("nse_trading_holidays_*.json"))
        if candidates:
            return max(candidates, key=lambda path: path.name)

        return target_path

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
