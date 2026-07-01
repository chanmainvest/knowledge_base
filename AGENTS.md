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
- **Flat-file layout** (hkej, macrovoices, yahoohk, youtube, substack): content lives at
  `data/<source>/[<channel>/]<YYYY>/<YYYY-MM-DD>-<title>.md`; raw HTML at
  `data/raw/<source>/[<channel>/]<YYYY>/<YYYY-MM-DD>-<title>.html`.
  Set `flat_layout=True` on `ScrapedItem` to use this layout; `BaseScraper.write_md()`
  handles both old (patreon) and new layouts automatically.
- Database: a single Postgres 16 + pgvector container (`docker compose up
  postgres`). Schema lives in `docker/postgres/init.sql`, which is idempotent
  and is what `kb db migrate` actually replays — that's the real source of
  truth. Numbered files in `migrations/` are historical/manual reference
  only (not auto-applied by any code); when the schema changes, edit
  `init.sql` (using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` etc. so it
  applies cleanly to an already-running DB) and add a matching numbered
  `migrations/NNN_*.sql` file for convention.
- `source.kind` groups sources by scraping/discovery shape, and drives `kb
  scrape list --kind` and the Search page's source list: `blog` = one-off,
  homepage-discovery scrapers with no per-author crawl/catalog state
  (macrovoices, madxcap/狂徒); `newspaper` = resumable multi-author crawlers
  with their own catalog tables (hkej, yahoohk, master-insight); `youtube` and
  `membership` (patreon, substack) are their own kinds.
- LLM calls go through `kb.llm.chat_json(system, user, schema, provider,
  model)`, which supports four providers: `openai` (or any OpenAI-compatible
  endpoint via `LLM_BASE_URL`, e.g. Azure OpenAI, GitHub Models, Ollama),
  `github` (shells out to the local `copilot` CLI in non-interactive mode —
  no separate API key, uses existing `copilot /login` auth), `anthropic`
  (Anthropic Messages API via forced tool-call JSON), and `zai` (Z.ai/Zhipu
  GLM, OpenAI-wire-compatible). `LLM_PROVIDER` in `.env` picks the default;
  override per call with `provider=`/`--provider`. Every extraction attempt
  is recorded in `extraction_run` (one row per item/provider/model/prompt
  version), so multiple providers can extract the same item without
  clobbering each other — see `doc/llm-extraction.md` for the full design,
  including how `kb extract compare`/`provider_model_leaderboard` let you
  cross-reference which provider/model is most accurate.

## Documentation

Code changes must keep documentation up to date in the same PR or change set.
Do not leave docs stale after altering behaviour, CLI flags, layout, or
architecture.

| Audience | Location | Update when |
|----------|----------|-------------|
| Humans (quick start) | `README.md` | setup steps, commands, architecture overview, or data layout change |
| AI coding agents | `AGENTS.md` | conventions, project layout, scraper/ingest patterns, or agent workflow change |
| Detailed reference | `doc/` | CLI usage, database design, scrape scripts, frontend usage, or any topic already covered there |

If a change introduces a new concept or workflow, add or extend the relevant
`doc/` page (and link it from `README.md` when appropriate). Prefer updating
existing pages over duplicating content across files.

## Layout

```
src/kb/
  config.py            # settings (.env)
  db.py                # SQLAlchemy engine + helpers
  io_md.py             # markdown read/write with front-matter
  ratelimit.py         # per-host async limiter
  llm.py               # multi-provider LLM client (openai/github/anthropic/zai)
                       # + JSON-schema chat_json()/embed()
  cli.py               # `kb` command
  scrapers/
    base.py            # ScrapedItem (flat_layout flag), BaseScraper.write_md()
    macrovoices.py
    youtube.py
    hkej.py
    yahoohk.py         # Yahoo Finance HK columnists (GraphQL feed + article HTML)
    master_insight.py  # Master Insight columnists (paginated author pages + article HTML)
    patreon.py         # Patreon posts (session cookie + browser fallback + DB crawl catalog)
    substack.py        # Substack posts (public archive/post API + browser fallback for paid content)
  ingest.py            # md -> Postgres (globs *.md, skips data/raw/)
  extract.py           # LLM structured extraction; extraction_run tracking,
                       # primary-run promotion, multi-provider compare
  leaderboard.py       # score predictions vs market; provider/model rollup
  api/
    main.py            # FastAPI app
    routes_*.py
frontend/              # Vite + React + Tailwind
docker/postgres/init.sql
migrations/
scripts/
  migrate_data_layout.py   # one-shot migration to flat-file layout
  fix_yahoohk_titles.py   # backfill misnamed Yahoo HK columnist files
```

### Data directory structure

```
data/
 hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
 raw/hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

 yahoohk/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
 raw/yahoohk/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

  master-insight/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
  raw/master-insight/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

  macrovoices/<YYYY>/<YYYY-MM-DD>-<ep_id>-<title>.md
  raw/macrovoices/<YYYY>/<YYYY-MM-DD>-<ep_id>-<title>.html  [.slides.pdf …]

  youtube/<channel-name-slug>/<YYYY>/<YYYY-MM-DD>-<title>.md

  substack/<handle>/<YYYY>/<YYYY-MM-DD>-<title>.md
  raw/substack/<handle>/<YYYY>/<YYYY-MM-DD>-<title>.html

  patreon/<channel>/<YYYY-MM-DD>__<id>/content.md     # legacy folder layout
```

## Adding a new source

1. Add a scraper module in `src/kb/scrapers/` subclassing `BaseScraper`.
2. Register it in `kb.scrapers.registry.SCRAPERS`.
3. Insert a row into the `source` table (or rely on `init.sql` seed).
4. Run `uv run kb scrape <code>`.

### Yahoo HK columnist notes

- Authors are discovered from the contributors index; channels are auto-upserted on
  first scrape. No manual author registration step.
- The feed often labels items `雅虎香港財經`; `yahoohk.py` takes the article
  headline from the page/body (second `#` heading when the first is generic) and
  strips columnist chrome before saving.
- Older files saved with the generic filename stem can be repaired with
  `uv run python scripts/fix_yahoohk_titles.py`.

### Substack notes

- Handles (e.g. `michaelwgreen` from `https://substack.com/@michaelwgreen`) are
  resolved to a publication `subdomain` once via the public
  `https://substack.com/api/v1/user/<handle>/public_profile` endpoint and cached
  in `channel.metadata`, mirroring how `patreon.py` caches `campaign_id`. Discovery
  (`.../api/v1/archive`) and post bodies (`.../api/v1/posts/<slug>`) come from
  that publication's own public API — no login needed for free posts.
- Some publications force a *custom domain* (`custom_domain_optional: false`,
  e.g. `michaelwgreen` → `www.yesigiveafig.com`); Substack 301-redirects every
  `.substack.com` request for these, and `httpx` follows the redirect
  transparently. Others (`custom_domain_optional: true`, or no custom domain)
  serve directly from `<subdomain>.substack.com`.
- Substack's `substack.sid` auth cookie is scoped to `.substack.com` and does
  **not** carry over to a custom domain for a plain HTTP client. Paid
  (`audience != "everyone"`) posts whose API body looks truncated relative to
  the post's own `wordcount` are re-fetched with a headless, cookie-injected
  Playwright browser navigating straight to the post's `canonical_url` — the
  same cross-domain auth-sync a real logged-in browser performs for a human
  reader.
- Get a session with `kb substack prime-session` (opens a real browser window
  to log in manually, saves `substack.sid` to `data/substack/.session.json`),
  or set `SUBSTACK_SESSION_COOKIE` / `SUBSTACK_COOKIES_FROM_BROWSER` in `.env`.
  `kb substack check-session` validates it; `kb substack resolve <handle>`
  resolves a handle without needing a session.
