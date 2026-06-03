"""Global config, env vars, and constants."""

import logging
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.env_resolve import env_var_from_file, is_env_placeholder
from data.storage.pg_connect import is_database_url_placeholder

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    market_data_provider: str = ""
    database_url: str = ""
    influxdb_url: str = ""
    influxdb_token: str = ""
    influxdb_org: str = ""
    influxdb_bucket: str = ""

    # Model / ML
    model_dir: str = "./models"
    production_model_id: str = "lightgbm_production"
    retrain_schedule_days: int = 1
    data_stale_minutes: int = 15

    # LLM — Anthropic only (explanation & news; never executes trades)
    llm_enabled: bool = False
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_max_tokens: int = 350

    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 10.0
    max_positions: int = 3
    default_risk_per_trade_pct: float = 0.5

    min_pattern_sample_size: int = 300
    harmonic_ratio_tolerance_pct: float = 3.0
    elliott_confidence_threshold: float = 0.60
    gann_research_only: bool = True

    paper_trading_enabled: bool = True
    signal_log_dir: str = "./logs/signals"
    chart_watchlist: str = "MES:5m,ES:5m,NQ:5m,MNQ:5m"

    # Coinbase Advanced Trade (crypto execution — live gated separately)
    coinbase_api_key: str = ""
    coinbase_api_secret: str = ""
    coinbase_api_key_file: str = ""
    coinbase_portfolio_id: str = ""
    coinbase_live_enabled: bool = False
    coinbase_max_order_usd: float = 50.0

    # Broker / trading platforms (comma-separated ids: paper,coinbase,robinhood,alpaca,...)
    enabled_brokers: str = "paper"

    robinhood_access_token: str = ""
    webull_app_key: str = ""
    webull_app_secret: str = ""
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    tastytrade_username: str = ""
    tastytrade_password: str = ""
    ibkr_account_id: str = ""
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    tradovate_api_key: str = ""
    tradovate_username: str = ""
    ninjatrader_license_key: str = ""

    # News intelligence
    news_enabled: bool = True
    news_polling_interval_seconds: int = 14400
    news_load_default_calendar: bool = False
    news_calendar_sync_interval_seconds: int = 21600
    news_calendar_catchup_minutes: int = 30
    news_calendar_max_triggers_per_day: int = 50
    news_calendar_days_ahead: int = 14
    news_calendar_trigger_offsets: str = "-15,0,5"

    # Watcher news — read from DB (API owns ingestion); set "local" for standalone watch
    watcher_news_source: str = "db"
    watcher_news_refresh_seconds: int = 60

    # News API keys (optional — RSS + FRED public demo work without keys)
    finnhub_api_key: str = ""
    benzinga_api_key: str = ""
    polygon_api_key: str = ""
    fmp_api_key: str = ""
    newsapi_key: str = ""
    marketaux_api_key: str = ""
    alpha_vantage_key: str = ""
    fred_api_key: str = ""
    eia_api_key: str = ""


def _apply_env_file_fallbacks(settings: Settings) -> Settings:
    updates: dict[str, str] = {}
    if is_database_url_placeholder(settings.database_url):
        fallback = env_var_from_file("DATABASE_URL", _BACKEND_DIR)
        if fallback:
            updates["database_url"] = fallback
    if is_env_placeholder(settings.polygon_api_key):
        fallback = env_var_from_file("POLYGON_API_KEY", _BACKEND_DIR)
        if fallback:
            updates["polygon_api_key"] = fallback
    if updates:
        logger.warning(
            "Ignoring placeholder env vars (%s); using backend/.env",
            ", ".join(k.upper() for k in updates),
        )
        return settings.model_copy(update=updates)
    return settings


@lru_cache
def get_settings() -> Settings:
    return _apply_env_file_fallbacks(Settings())
