# Runtime image for the `kb` CLI — used by the nightly Jenkins pipeline.
#
# The Jenkins container has no Python/uv/Playwright/yt-dlp, so each pipeline
# stage does `docker compose run --rm kb <cmd>` against this image. It talks to
# the host's existing Postgres (host.docker.internal:5544) and mounts the host
# data/ dir, so scraped content and session cookies persist on the host exactly
# as they do for `uv run kb` from a shell.
#
# Build:  docker compose build kb
# Run:    docker compose run --rm kb <subcommand>   (e.g. kb status, kb scrape ...)
#
# All secrets come from .env (env_file in docker-compose.yml) — none are baked
# into this image.

FROM python:3.12-slim

# ---- System dependencies -----------------------------------------------------
# curl/git: uv bootstrap + repo ops. openssh-client: YouTube SOCKS5 proxy pool
# (src/kb/scrapers/proxy.py runs `ssh -D` for yt-dlp). fonts-noto-cjk: Chinese
# content (HKEJ / 狂徒). The Playwright chromium OS deps are installed later by
# `playwright install --with-deps chromium` (it knows the exact package names
# for the Debian release), so we don't hand-pin fragile lib names here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        openssh-client \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# ---- uv (matches the host toolchain) ----------------------------------------
# Pinned for reproducibility; update alongside the host `uv` if needed.
ARG UV_VERSION=0.5.11
RUN curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# ---- Install dependencies (cached unless pyproject/lock change) -------------
# Copy only the manifest first so dependency resolution is layer-cached.
COPY pyproject.toml uv.lock ./
# --frozen honours uv.lock exactly; --no-dev skips pytest/ruff/mypy.
# faster-whisper + nvidia-cublas-cu12 are unconditional top-level deps and will
# install — they're only used by scripts/transcribe_missing.py (GPU), so they
# are dead weight here but harmless on CPU and keep the lockfile valid.
RUN uv sync --frozen --no-dev

# ---- Install the app itself (puts `kb` + `yt-dlp` on PATH) ------------------
COPY src ./src
COPY docker/postgres/init.sql ./docker/postgres/init.sql
RUN uv pip install --system .

# ---- Playwright chromium for Substack/Patreon headless fallbacks ------------
# `--with-deps` is a no-op for the libs already apt-installed above; it fills any
# gaps. Browser binary is ~300 MB but cached in the image.
RUN uv run playwright install --with-deps chromium

ENV PYTHONUNBUFFERED=1

# Entrypoint that stages the host ~/.ssh into /root/.ssh with strict perms
# (see docker/kb-entrypoint.sh) before running the kb CLI. Without this, the
# Windows bind-mount leaves keys/config world-writable and SSH rejects them,
# silently breaking every YouTube SOCKS5 proxy tunnel.
COPY docker/kb-entrypoint.sh /kb-entrypoint.sh
RUN chmod +x /kb-entrypoint.sh

ENTRYPOINT ["/kb-entrypoint.sh"]
CMD ["--help"]
