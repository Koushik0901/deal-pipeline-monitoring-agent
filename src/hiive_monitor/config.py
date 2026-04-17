"""Application settings — read from environment variables with typed defaults."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # API
    anthropic_api_key: str = ""

    # Clock
    clock_mode: str = "simulated"  # "real_time" | "simulated"

    # Server
    port: int = 8000

    # Database
    domain_db_path: str = "domain.db"
    checkpoint_db_path: str = "agent_checkpoints.db"

    # Monitor
    attention_threshold: float = 0.6
    tick_interval_seconds: int = 60
    max_investigations_per_tick: int = 5

    # Suppression (FR-LOOP-02)
    suppression_ticks: int = 3
    suppression_multiplier: float = 0.2

    # Logging
    log_format: str = "human"  # "human" | "json"

    # LLM models
    haiku_model: str = "claude-haiku-4-5-20251001"
    sonnet_model: str = "claude-sonnet-4-6"

    # LLM limits
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
