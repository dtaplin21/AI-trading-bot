"""Global config, env vars, and constants."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    retrain_schedule_days: int = 7
    data_stale_minutes: int = 15

    # LLM explanation (never used for execution)
    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
