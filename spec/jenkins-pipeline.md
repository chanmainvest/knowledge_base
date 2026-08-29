# Spec — Nightly Jenkins pipeline (Docker runtime, mounts, login-gated stages)

Read this when touching `Jenkinsfile`, `Dockerfile`, `docker-compose.yml`,
or debugging nightly-run failures. Full operational setup (build, session
priming, job creation): `doc/jenkins-pipeline.md`.

- **Nightly Jenkins pipeline (`Jenkinsfile`).** A declarative pipeline runs
  every source as its own stage at 03:00 daily, then a catch-up `kb ingest`,
  `kb extract run`, and `kb progress recompute`. The Jenkins controller has no
  Python/uv/Playwright/yt-dlp, so each stage does
  `docker compose run --rm kb <cmd>` against the self-contained `kb` image
  (root `Dockerfile`, `kb` service in `docker-compose.yml`). The container
  mounts the host `data/` dir (so scraped content + HKEJ/Patreon/Substack
  session-cookie files are shared with the local `uv run kb` workflow) — via
  the `DATA_DIR_HOST`/`LOGS_DIR_HOST` **absolute host paths** in `.env`
  (`/host_mnt/b/...` Docker-Desktop aliases): a relative `./data` source is
  resolved by the Docker daemon inside the Docker Desktop VM when Jenkins
  runs compose from its container, and nightly scrapes silently vanished
  into an orphan VM dir that way until 2026-08-14 (same rule as
  `SSH_KEY_DIR`). It reaches the host Postgres via
  `POSTGRES_HOST_DOCKER=host.docker.internal`
  (the host-side `POSTGRES_HOST` stays `localhost`). It also mounts `~/.ssh`
  read-only for the YouTube SOCKS5 proxy pool. `failFast` is off; login-gated
  stages (HKEJ/Patreon/Substack) are wrapped in `catchError` and downgrade to
  UNSTABLE (session expired → re-prime interactively; per-source session
  details in `spec/newspaper-scrapers.md` and
  `spec/membership-scrapers.md`), while the core scrape/ingest/extract
  stages stay red. The HKEJ stage needs the `kb_camoufox` browser container
  already running on the host (`docker compose up -d camoufox` — the
  pipeline does not start it; a missing container fails every author with
  `ENETUNREACH` on `ws://host.docker.internal:9222/hkej`). Any
  `docker compose run` inside a `while read` loop in the Jenkinsfile must
  take `</dev/null` (otherwise it swallows the loop's stdin and only the
  first item ever runs), and per-item failures are tallied so the stage can
  actually reach UNSTABLE — see the gotchas in `doc/jenkins-pipeline.md`.
  Secrets come from the Jenkins Credentials store (IDs in the
  `environment{}` block) or fall back to `.env` via the service's
  `env_file`. See `doc/jenkins-pipeline.md` for the full setup (build,
  one-time session priming, job creation).
