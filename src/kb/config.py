"""Settings loaded from .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs"


def _resolve_data_dir(raw: str) -> Path:
    """Resolve a data-dir string into an absolute ``Path``.

    Relative paths resolve against the repo root; absolute paths are used
    as-is. Tilde (``~``) is expanded to the user home.
    """
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


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
    substack_session_cookie: str = ""  # substack.sid cookie value (DevTools → Application → Cookies)

    # Scraper behaviour
    scrape_rate_limit_sec: float = 3.0
    hkej_rate_limit_sec: float = 5.0  # browser loads; keep ≥ global default
    patreon_rate_limit_sec: float = 5.0  # internal API; keep ≥ global default
    substack_rate_limit_sec: float = 3.0  # public archive/post API; keep ≥ global default
    scrape_user_agent: str = "Mozilla/5.0 KB-Personal/0.1"
    scrape_max_retries: int = 3
    yt_dlp_cookies_from_browser: str = ""
    patreon_cookies_from_browser: str = ""  # e.g. chrome, edge — reads session_id if PATREON_SESSION_ID unset
    substack_cookies_from_browser: str = ""  # e.g. chrome, edge — reads substack.sid if SUBSTACK_SESSION_COOKIE unset

    # LLM — which provider `kb extract run` uses by default, and which
    # provider embeddings use (embeddings need an OpenAI-wire-compatible
    # endpoint; only "openai" and "zai" support them today).
    llm_provider: str = "openai"            # openai | github | anthropic | zai
    llm_embedding_provider: str = "openai"  # openai | zai

    # ---- openai (also the default for any OpenAI-compatible endpoint you
    # point LLM_BASE_URL at, e.g. Azure OpenAI or a local Ollama server) ----
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_embedding_model: str = "text-embedding-3-small"

    # ---- github: shells out to the local `copilot` CLI in non-interactive
    # mode instead of calling an HTTP API. Uses whatever Copilot auth is
    # already active for this machine (`copilot /login`); no separate API key.
    github_cli_path: str = "copilot"
    github_model: str = ""  # empty = let the CLI pick its own default model
    github_cli_timeout_sec: int = 180

    # ---- anthropic ----
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""  # empty = SDK default (https://api.anthropic.com)
    anthropic_model: str = "claude-sonnet-4-5"

    # ---- zai (Z.ai / Zhipu GLM, OpenAI-compatible endpoint) ----
    zai_api_key: str = ""
    zai_base_url: str = "https://api.z.ai/api/paas/v4"
    zai_model: str = "glm-4.6"
    zai_embedding_model: str = "embedding-3"

    # Postgres
    postgres_host: str = "localhost"
    postgres_port: int = 5544
    postgres_user: str = "kb"
    postgres_password: str = "kb_local_dev"
    postgres_db: str = "kb"

    # Data layout
    data_dir: str = "data"  # relative to repo root, or an absolute path

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

    @property
    def data_path(self) -> Path:
        """Resolved data directory as an absolute ``Path``."""
        return _resolve_data_dir(self.data_dir)


# Module-level constant — reads the setting once at import time so that all
# `from ..config import DATA_DIR` sites (scrapers, ingest, etc.) pick up the
# configured value without needing a runtime lookup.
DATA_DIR = Settings().data_path


@lru_cache(maxsize=1)
def settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
