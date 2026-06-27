"""Settings loaded from .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Scrapers / auth
    macrovoices_user: str = ""
    macrovoices_pass: str = ""
    hkej_user: str = ""
    hkej_pass: str = ""
    patreon_session_id: str = ""

    # Scraper behaviour
    scrape_rate_limit_sec: float = 3.0
    hkej_rate_limit_sec: float = 5.0  # browser loads; keep ≥ global default
    patreon_rate_limit_sec: float = 5.0  # internal API; keep ≥ global default
    scrape_user_agent: str = "Mozilla/5.0 KB-Personal/0.1"
    scrape_max_retries: int = 3
    yt_dlp_cookies_from_browser: str = ""
    patreon_cookies_from_browser: str = ""  # e.g. chrome, edge — reads session_id if PATREON_SESSION_ID unset

    # LLM
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_embedding_model: str = "text-embedding-3-small"

    # Postgres
    postgres_host: str = "localhost"
    postgres_port: int = 5544
    postgres_user: str = "kb"
    postgres_password: str = "kb_local_dev"
    postgres_db: str = "kb"

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8088

    @property
    def db_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def db_url_async(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
