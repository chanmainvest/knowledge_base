# Knowledge Base — Agent notes

## Conventions

- Python ≥ 3.11, managed by `uv`. Add deps via `uv add <pkg>`; never edit
  `pyproject.toml` and re-run pip.
- All secrets in `.env` (gitignored). Load via `kb.config.settings`. Never
  hardcode user/password.
- The data directory is configurable via `DATA_DIR` in `.env` (or the
  `DATA_DIR` env var). Relative paths resolve against the repo root; absolute
  paths are used as-is. `kb.config.DATA_DIR` is a module-level `Path` computed
  at import time from the setting, so all `from ..config import DATA_DIR`
  sites pick up the configured value automatically. Changing `DATA_DIR`
  requires re-running `kb ingest` to refresh `item.md_path` in the database.
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
- **YouTube proxy** (optional): to avoid YouTube's per-IP rate limiting (HTTP
  429), yt-dlp can route through SOCKS5 tunnels over SSH. `--proxy-hosts
  oc1.hevangel.com,serv00` opens one `ssh -D` tunnel per host (ports 1081+)
  and round-robins each yt-dlp call across them; falls back to the
  `YT_DLP_PROXY_HOSTS` env var if the flag is omitted, and to a direct
  connection if neither is set. A single manual tunnel is also supported via
  `YT_DLP_PROXY=socks5://127.0.0.1:1080`. The proxy covers both yt-dlp calls
  (via `--proxy`) and the `youtube-transcript-api` fallback (via
  `HTTPS_PROXY`/`ALL_PROXY` env vars set around the call). Available SSH host
  aliases (configured in `~/.ssh/config`): `hevangel.com`,
  `oc1/2/3/4.hevangel.com`, `horace.org`, `serv00`. The `ProxyPool` tunnel
  manager lives in `src/kb/scrapers/proxy.py`.
- Markdown is the canonical raw form. Each item's markdown front-matter
  carries `source`, `channel`, `external_id`, `url`, `published_at`, `title`,
  `lang`, plus source-specific fields. The DB row is regenerated from the
  markdown by `kb ingest`.
- **Flat-file layout** (blog, hkej, yahoohk, youtube, substack): content lives at
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
  (macrovoices, madxcap/狂徒 are channels under a single `blog` source — each
  site keeps its own scraper class but they share the `blog` source code and
  `source_code = "blog"` on the scraper); `newspaper` = resumable multi-author
  crawlers with their own catalog tables (hkej, yahoohk, master-insight);
  `youtube` and `membership` (patreon, substack) are their own kinds.
- Blog scrapers set `source_code = "blog"` on the scraper class; the
  `effective_source_code` property returns the DB source code to write to
  markdown front-matter. The registry still keys by the unique scraper `code`
  (e.g. `macrovoices`, `madxcap`). Use `kb blog scrape <site>` to scrape a
  specific blog, or the generic `kb scrape run <code>`.
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
- **Per-ticker prediction consolidation (read-time).** The LLM extracts per
  chunk, so the same ticker can appear as several flat `prediction` rows for
  one item (one per quote). Those rows are the source of truth for scoring
  and the leaderboard and are **not** merged in the DB. The item-detail
  endpoint collapses them at read time via `_consolidate_predictions()` in
  `src/kb/api/main.py` into one entry per ticker with a `quotes[]` array, a
  consensus `direction`, and a `conflict` flag set when the same ticker has
  both a bullish and a bearish call in the same article. The flat
  `/api/predictions` list still returns raw rows. The frontend item page
  renders one card per ticker (with an amber **conflict** badge) and makes
  each quote clickable to jump to and highlight it in the article body.
- **Pipeline progress tracking.** The scrape → ingest → extract pipeline
  records its progress in two places: per-item stage timestamps
  (`item.ingested_at`, `item.extracted_at`) and a per-source rollup table
  `source_progress` (one row per source: `n_downloaded`, `n_ingested`,
  `n_extracted`, `n_extract_pending`, `n_extract_error`, plus last-run
  timestamps). The boundary functions in `src/kb/progress.py`
  (`mark_downloaded` / `mark_ingested` / `mark_extracted`) are called from
  `scrapers.base.write_md`, `ingest.ingest_file`, and the extract success /
  error paths respectively, so all sources are tracked from one instrumented
  boundary each. `recompute()` does an authoritative full recount from the
  `item` table and is called at the end of every `kb extract run` batch and
  on demand via `kb progress recompute` (which also re-derives
  `n_downloaded` from the hkej/patreon catalog tables). `n_downloaded` is
  best-effort for the filesystem-discovery sources (no historical catalog to
  reconstruct from) — it accrues from when the feature shipped forward.
- **Discovery catalog.** Every item a filesystem-discovery scraper sees during
  `discover()` is upserted into a generic `discovery_catalog` table (one row
  per `(source_id, external_id)`, with a `downloaded` flag and the full
  original discovery `descriptor` stored as JSONB) via the
  `_recording_discover()` wrapper in `scrapers/base.py`. `write_md()` flips
  the row to `downloaded=true`. So "discovered but not downloaded" (a scrape
  that died halfway) is queryable, and `kb scrape resume <code>` re-attempts
  just those items without re-discovering the whole source. hkej and patreon
  keep their richer native catalogs (`hkej_article_catalog` /
  `patreon_post_catalog`) with run/page fingerprinting + resume cursors and do
  NOT use the generic table; their pending counts are unioned in at read time
  by `catalog.pending_counts()`. `src/kb/catalog.py` is the module.

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
                       # BaseScraper.source_code / effective_source_code
    macrovoices.py     # MacroVoices podcast (source_code='blog')
    madxcap.py         # MadX 狂徒投資 blog (source_code='blog')
    youtube.py
    hkej.py
    yahoohk.py         # Yahoo Finance HK columnists (GraphQL feed + article HTML)
    master_insight.py  # Master Insight columnists (paginated author pages + article HTML)
    patreon.py         # Patreon posts (session cookie + browser fallback + DB crawl catalog)
    substack.py        # Substack posts (public archive/post API + browser fallback for paid content)
  ingest.py            # md -> Postgres (globs *.md, skips data/raw/); stamps
                       # item.ingested_at and bumps source_progress
  extract.py           # LLM structured extraction; extraction_run tracking,
                       # primary-run promotion, multi-provider compare;
                       # stamps item.extracted_at and bumps source_progress
  progress.py          # pipeline progress tracking — boundary counters
                       # (mark_downloaded/ingested/extracted) + recompute();
                       # backs /api/dashboard and `kb progress status`
  catalog.py           # discovery catalog — records every discovered item so
                       # "discovered but not downloaded" is queryable and
                       # `kb scrape resume` can re-fetch pending items
  leaderboard.py       # score predictions vs market; provider/model rollup
  api/
    main.py            # FastAPI app (search, items, predictions, leaderboard,
                       # dashboard, sources, channels)
    routes_*.py
frontend/              # Vite + React + Tailwind
  src/pages/Dashboard.tsx   # pipeline progress overview (landing page)
docker/postgres/init.sql
migrations/
scripts/
  migrate_data_layout.py   # one-shot migration to flat-file layout
  migrate_to_blog_source.py   # consolidate macrovoices+madxcap into data/blog/
  copy_to_data_public.py   # publish configured data/ subset to data_public/
  build_data_readmes.py    # README indexes for data/ or data_public/
  fix_yahoohk_titles.py   # backfill misnamed Yahoo HK columnist files
```

### Data directory structure

`data/` is a separate local git repo (scraped markdown + raw files). This
repository tracks a `data` gitlink for convenience but does not vendor the
content. Public releases go in the `data_public/` submodule
(`git@github.com:chanmainvest/data_knowledge_base.git`).

```
data/
 hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
 raw/hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

 yahoohk/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
 raw/yahoohk/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

  master-insight/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
  raw/master-insight/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

  blog/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.md        # MacroVoices, 狂徒, …
  raw/blog/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.html   # [.slides.pdf …]

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
