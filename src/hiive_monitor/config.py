"""Application settings — read from environment variables with typed defaults."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # API — OpenRouter
    openrouter_api_key: str = ""

    # Clock
    clock_mode: str = "simulated"  # "real_time" | "simulated"

    # Server
    port: int = 8000

    # Database
    domain_db_path: str = "domain.db"
    checkpoint_db_path: str = "agent_checkpoints.db"

    # Monitor
    attention_threshold: float = 0.45  # lowered from 0.6 so WATCH-tier signals pass screening
    tick_interval_seconds: int = 60
    max_investigations_per_tick: int = 12  # raised from 5 to surface mid-tier deals beyond the top escalations

    # Suppression (FR-LOOP-02)
    suppression_ticks: int = 3
    suppression_multiplier: float = 0.2

    # Logging
    log_format: str = "human"  # "human" | "json"
    logs_path: str = "out/logs.jsonl"

    # LLM models — OpenRouter format (provider/model); swap via SLM_MODEL / LLM_MODEL in .env
    slm_model: str = "google/gemma-4-31b-it"
    llm_model: str = "anthropic/claude-sonnet-4.6:exacto"

    # Feature flags
    enable_ts06_doc_tracking: bool = False  # TS06: document collection tracking
    enable_ts09_portfolio_patterns: bool = True  # TS09: portfolio-level cluster detection
    enable_ts10_snooze: bool = True  # TS10: analyst deal snooze

    # LLM limits
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3
    llm_max_tokens: int | None = None  # None = let the model decide


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
