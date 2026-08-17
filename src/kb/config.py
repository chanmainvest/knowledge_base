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
    youtube_rate_limit_sec: float = 5.0  # subtitle/timedtext endpoint 429s hard; keep generous
    # When the SOCKS5 proxy pool is down and yt-dlp falls back to a *direct*
    # connection, YouTube throttles the single residential IP far harder than a
    # round-robined egress pool. Use this larger base interval proactively (not
    # reactively after a 429) so direct scrapes don't get rate-limited at all.
    youtube_direct_rate_limit_sec: float = 15.0
    scrape_user_agent: str = "Mozilla/5.0 KB-Personal/0.1"
    scrape_max_retries: int = 3
    yt_dlp_cookies_from_browser: str = ""
    yt_dlp_proxy: str = ""        # single proxy URL, e.g. socks5://127.0.0.1:1080 (manual tunnel)
    yt_dlp_proxy_hosts: str = ""  # comma-separated SSH aliases, e.g. oc1.hevangel.com,serv00 (auto tunnels)
    patreon_cookies_from_browser: str = ""  # e.g. chrome, edge — reads session_id if PATREON_SESSION_ID unset
    substack_cookies_from_browser: str = ""  # e.g. chrome, edge — reads substack.sid if SUBSTACK_SESSION_COOKIE unset

    # HKEJ browser / Camoufox — Docker is the default launch mode; the browser
    # runs in a container exposing a Playwright WS endpoint. "local" falls back
    # to an on-host Camoufox (the historical daemon path).
    hkej_browser_mode: str = "docker"  # docker | local
    hkej_login_mode: str = "auto"      # auto (creds→auto-fill, else manual) | manual
    hkej_camoufox_endpoint: str = "ws://127.0.0.1:9222/hkej"  # container Playwright WS endpoint
    hkej_docker_image: str = "kb-camoufox:latest"             # built from docker/camoufox
    hkej_docker_container: str = "kb_camoufox"                # container name for kb hkej docker
    hkej_camoufox_port: int = 9222       # host port mapped to the container's WS endpoint
    hkej_docker_novnc_port: int = 7900   # host port mapped to the container's noVNC web UI

    # LLM — which provider `kb extract run` uses by default, and which
    # provider embeddings use (embeddings need an OpenAI-wire-compatible
    # endpoint; only "openai" and "zai" support them today).
    llm_provider: str = "zai"                # openai | github | anthropic | zai
    llm_embedding_provider: str = "zai"      # openai | zai

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
    zai_model: str = "glm-5.3"
    zai_embedding_model: str = "embedding-3"

    # ---- LLM retry / rate-limit backoff ----
    # Providers (esp. OpenRouter free tier) return HTTP 429 under sustained load.
    # chat_json()/embed() retry with a quiet period: honour the server's
    # Retry-After header when present, otherwise pause llm_rate_limit_pause_sec
    # for a 429 and use exponential backoff (2..llm_rate_limit_pause_sec) for
    # other transient errors, up to llm_max_retries attempts.
    llm_max_retries: int = 8
    llm_rate_limit_pause_sec: int = 20

    # ---- Extraction prompt/schema versioning ----
    # Pin the extraction prompt+schema version (directory name under
    # src/kb/prompts/extraction/). Empty = always use the highest version
    # present, so adding a new version dir adopts it on the next run.
    extraction_prompt_version: str = ""

    # Postgres
    postgres_host: str = "localhost"
    postgres_port: int = 5544
    postgres_user: str = "kb"
    postgres_password: str = "kb_local_dev"
    postgres_db: str = "kb"

    # Data layout
    data_dir: str = "data"  # relative to repo root, or an absolute path

    # Whisper / ASR transcription (faster-whisper + large-v3 on GPU).
    # Disabled by default — enable per run with `kb youtube scrape --transcribe`
    # or use the dedicated `kb youtube transcribe` command.
    whisper_enabled: bool = False
    whisper_model: str = "large-v3"        # faster-whisper model size
    whisper_device: str = "cuda"           # cuda | cpu
    whisper_compute_type: str = "float16"  # float16 (GPU), int8 (CPU)
    whisper_beam_size: int = 5             # beam search width
    whisper_language: str = ""             # empty = auto-detect, or "en"/"yue"/etc.
    whisper_max_duration_sec: int = 0      # 0 = no limit; otherwise skip videos longer than this
    # Transient audio download dir. Relative paths resolve against DATA_DIR,
    # matching the data/raw/<source>/ layout; absolute paths are used as-is.
    whisper_tmp_dir: str = "raw/youtube/tmp"

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
