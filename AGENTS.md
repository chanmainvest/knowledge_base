# Knowledge Base — Agent notes

This file holds only the always-on conventions. Detailed per-subsystem
notes live in `spec/` and are **loaded on demand** — before working in an
area, read the spec file that covers it (table below).

## Conventions

- Python ≥ 3.11, managed by `uv`. Add deps via `uv add <pkg>`; never edit
  `pyproject.toml` and re-run pip.
- All secrets in `.env` (gitignored). Load via `kb.config.settings`. Never
  hardcode user/password.
- The data directory is configurable via `DATA_DIR` in `.env` (or the
  `DATA_DIR` env var). Relative paths resolve against the repo root;
  absolute paths are used as-is. `kb.config.DATA_DIR` is a module-level
  `Path` computed at import time, so all `from ..config import DATA_DIR`
  sites pick up the configured value automatically. Changing `DATA_DIR`
  requires re-running `kb ingest` to refresh `item.md_path` in the database
  (stored **repo-root-relative** — the host/container absolute-path trap is
  detailed in `spec/pipeline.md`).
- Be polite to upstream sites:
  - Per-host rate limit ≥ `SCRAPE_RATE_LIMIT_SEC` (default 3 s).
  - Random jitter; honour `Retry-After` and 429s.
  - One realistic browser User-Agent (set in `.env`).
  - Prefer official feeds (RSS, YouTube transcripts) over scraping HTML.
- Idempotent scrapers: skip an item if its markdown file already exists and
  is non-empty. Re-runnable safely.
- Markdown is the canonical raw form. Each item's markdown front-matter
  carries `source`, `channel`, `external_id`, `url`, `published_at`, `title`,
  `lang`, plus source-specific fields. The DB row is regenerated from the
  markdown by `kb ingest`.
- Database: a single Postgres 16 + pgvector container (`docker compose up
  postgres`). Schema lives in `docker/postgres/init.sql`, which is idempotent
  and is what `kb db migrate` actually replays — that's the real source of
  truth. Numbered files in `migrations/` are historical/manual reference
  only (not auto-applied by any code); when the schema changes, edit
  `init.sql` (using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` etc. so it
  applies cleanly to an already-running DB) and add a matching numbered
  `migrations/NNN_*.sql` file for convention.

## Detailed specs — load on demand

| Spec | Covers | Read when working on |
|------|--------|----------------------|
| `spec/layout.md` | source tree, scripts index, `data/` directory layout | orienting, adding files/modules, data layout changes |
| `spec/pipeline.md` | `DATA_DIR`/`md_path` trap, `--limit` semantics, flat-file layout, progress tracking, discovery catalog | `ingest.py`, `progress.py`, `catalog.py`, `scrapers/base.py`, data layout |
| `spec/blog-scrapers.md` | Joomla pagebreak, MacroVoices two-index + PDF transcripts, Gorozen HubSpot form API, `source.kind`, blog `source_code` mechanics | `macrovoices.py`, `madxcap.py`, `gorozen.py`, new blog sources |
| `spec/youtube-scrapers.md` | folder stability/dedup, transcript paragraphing, date/metadata backfills, SSH SOCKS5 proxy pool, `yue` captions, Whisper ASR | `youtube.py`, `transcribe.py`, `proxy.py`, YouTube backfill scripts |
| `spec/newspaper-scrapers.md` | HKEJ Camoufox browser/session model, Yahoo HK columnist notes, BusinessFocus node_api index | `hkej.py`, `yahoohk.py`, `master_insight.py`, `businessfocus.py`, `docker/camoufox/` |
| `spec/membership-scrapers.md` | Patreon session fallback + curl_cffi Cloudflare bypass, Substack custom-domain/paid-post notes | `patreon.py`, `substack.py`, `patreon_daemon.py` |
| `spec/llm-extraction.md` | `kb.llm` providers, prompt/schema versioning registry, per-ticker prediction consolidation | `llm.py`, `extract.py`, `prompts.py`, `prompts/extraction/` |
| `spec/market-scoring.md` | market-data pipeline, scoring/leaderboards, API serving the frontend SPA | `marketdata.py`, `leaderboard.py`, `api/`, `frontend/` |
| `spec/jenkins-pipeline.md` | nightly Jenkins pipeline, kb Docker image, host mounts, login-gated stage semantics | `Jenkinsfile`, `Dockerfile`, `docker-compose.yml`, nightly failures |

When a spec file and the code disagree, the code wins — fix the spec in the
same change set.

## Documentation

Code changes must keep documentation up to date in the same PR or change set.
Do not leave docs stale after altering behaviour, CLI flags, layout, or
architecture.

| Audience | Location | Update when |
|----------|----------|-------------|
| Humans (quick start) | `README.md` | setup steps, commands, architecture overview, or data layout change |
| AI coding agents (always on) | `AGENTS.md` | cross-cutting conventions or the spec index change |
| AI coding agents (on demand) | `spec/*.md` | the subsystem a spec file covers changes |
| Detailed reference | `doc/` | CLI usage, database design, scrape scripts, frontend usage, or any topic already covered there |

If a change introduces a new concept or workflow, add or extend the relevant
`doc/` page (and link it from `README.md` when appropriate). Prefer updating
existing pages over duplicating content across files.

## Adding a new source

1. Add a scraper module in `src/kb/scrapers/` subclassing `BaseScraper`.
2. Register it in `kb.scrapers.registry.SCRAPERS`.
3. Insert a row into the `source` table (or rely on `init.sql` seed).
4. Run `uv run kb scrape <code>`.

Pick the closest existing source kind and follow its spec file
(`spec/blog-scrapers.md`, `spec/newspaper-scrapers.md`,
`spec/membership-scrapers.md`, `spec/youtube-scrapers.md`) for the
discovery/crawl/catalog pattern to copy.
