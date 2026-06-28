# Knowledge Base — Agent notes

## Conventions

- Python ≥ 3.11, managed by `uv`. Add deps via `uv add <pkg>`; never edit
  `pyproject.toml` and re-run pip.
- All secrets in `.env` (gitignored). Load via `kb.config.settings`. Never
  hardcode user/password.
- Be polite to upstream sites:
  - Per-host rate limit ≥ `SCRAPE_RATE_LIMIT_SEC` (default 3 s).
  - Random jitter; honour `Retry-After` and 429s.
  - One realistic browser User-Agent (set in `.env`).
  - Prefer official feeds (RSS, YouTube transcripts) over scraping HTML.
- Idempotent scrapers: skip an item if its markdown file already exists and
  is non-empty. Re-runnable safely.
- Scrape `--limit` is source-unit scoped where implemented, not necessarily a
  global output cap. For YouTube, `kb youtube scrape --limit N` inspects up to
  N videos per registered channel and does not stop after N total new files.
- Markdown is the canonical raw form. Each item's markdown front-matter
  carries `source`, `channel`, `external_id`, `url`, `published_at`, `title`,
  `lang`, plus source-specific fields. The DB row is regenerated from the
  markdown by `kb ingest`.
- **Flat-file layout** (hkej, macrovoices, youtube): content lives at
  `data/<source>/[<channel>/]<YYYY>/<YYYY-MM-DD>-<title>.md`; raw HTML at
  `data/raw/<source>/[<channel>/]<YYYY>/<YYYY-MM-DD>-<title>.html`.
  Set `flat_layout=True` on `ScrapedItem` to use this layout; `BaseScraper.write_md()`
  handles both old (patreon) and new layouts automatically.
- Database: a single Postgres 16 + pgvector container (`docker compose up
  postgres`). Migrations live as plain SQL in `migrations/` and run via
  `kb db migrate`.
- LLM calls go through `kb.llm.client` which uses the OpenAI-compatible
  `LLM_BASE_URL` / `LLM_API_KEY` (works with OpenAI, Azure, GitHub Models, or
  Ollama). Always pass `response_format=json_schema` for extraction.

## Layout

```
src/kb/
  config.py            # settings (.env)
  db.py                # SQLAlchemy engine + helpers
  io_md.py             # markdown read/write with front-matter
  ratelimit.py         # per-host async limiter
  llm.py               # OpenAI client + JSON-schema extractor
  cli.py               # `kb` command
  scrapers/
    base.py            # ScrapedItem (flat_layout flag), BaseScraper.write_md()
    macrovoices.py
    youtube.py
    hkej.py
  ingest.py            # md -> Postgres (globs *.md, skips data/raw/)
  extract.py           # LLM structured extraction
  leaderboard.py       # score predictions vs market
  api/
    main.py            # FastAPI app
    routes_*.py
frontend/              # Vite + React + Tailwind
docker/postgres/init.sql
migrations/
scripts/
  migrate_data_layout.py   # one-shot migration to flat-file layout
```

### Data directory structure

```
data/
  hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
  raw/hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

  macrovoices/<YYYY>/<YYYY-MM-DD>-<ep_id>-<title>.md
  raw/macrovoices/<YYYY>/<YYYY-MM-DD>-<ep_id>-<title>.html  [.slides.pdf …]

  youtube/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.md

  patreon/<channel>/<YYYY-MM-DD>__<id>/content.md     # legacy folder layout
```

## Adding a new source

1. Add a scraper module in `src/kb/scrapers/` subclassing `BaseScraper`.
2. Register it in `kb.scrapers.registry.SCRAPERS`.
3. Insert a row into the `source` table (or rely on `init.sql` seed).
4. Run `uv run kb scrape <code>`.
