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
- **Multi-page articles (Joomla pagebreak).** Some sources split a single
  article across several pages via Joomla's pagebreak plugin (an "Article
  Index" TOC of `?start=N` links). The bare URL returns only page 1 + the
  TOC, so a naive fetch saves nav chrome instead of the body. `?showall=1`
  collapses the whole article onto one page — MacroVoices' `fetch()` uses
  it, strips the pagination nav (`_strip_pagination_nav`) before
  markdownify, and falls back to walking `?start=1..N` if `showall` ever
  fails to concatenate. When adding a Joomla-ish source, check for an
  Article Index and prefer `?showall=1` over per-page walking.
- **MacroVoices two-index discovery (cross-index dedup gotcha).** The site
  has two episode indices whose coverage differs, and they assign **different
  article IDs to the same episode** (e.g. Lyn Alden ep538 = article 1538 on
  the legacy index, 1537 on the live one). Discovery walks the **live**
  `/podcasts-collection/macrovoices-podcasts` index (20/page, item-offset
  `?start=N`, ~29 pages, spans the whole archive back to 2016); the legacy
  `/podcast-transcripts` index is **frozen at episode 1538 (25 June 2026)**
  and no longer receives new episodes, so it cannot be the discovery source.
  `already_scraped()` must dedup by **episode slug text** (guest+title), not
  article id — it uses a 24-char slug prefix plus a ≥3-guest-token overlap
  fallback to survive diacritic transliteration (`stöferle`→`stoeferle` vs
  `sto-ferle`) and guest-name reordering. Episode links are root-level
  `/<artid>-macrovoices-<NNN>-<slug>`; the `<NNN>` episode number is the
  stable cross-index key and is encoded in the canonical `external_id`
  (`mv-<NNN>-<slug>`).
- **MacroVoices transcript source differs by page format.** Post-redesign
  episode pages (`/<id>-macrovoices-<NNN>-…`) carry **no inline transcript** —
  the body is a "Download the podcast transcript [Click Here]" link to a PDF
  at `/guest-content/list-guest-transcripts/<id>/file`; `fetch()` downloads
  that PDF and text-extracts it with pypdf. Old-format pages
  (`/podcast-transcripts/<id>`) still serve inline text via `?showall=1`.
  `fetch()` branches on URL shape (`_is_new_format`). The live index also
  carries a few dangling links that resolve to the site 404 or homepage
  chrome (title `404Error` / `Welcome to Macro Voices` with no transcript) —
  these are detected and skipped. New-format page titles live in `<h2>` (the
  `<h1>` is the generic site banner), so title extraction prefers `<h2>`.
- **Gorozen (Goehring & Rozencwajg) two-stream scraper.** `gorozen.py`
  scrapes two content streams under one `blog` source, selected by the shared
  `--source-type` flag (same mechanism madxcap uses for dcard/facebook):
  - `blog` — static HTML at `blog.gorozen.com/blog`, paginated
    `/blog/page/N` (page 1 = `/blog`; the bare host root 404s). `discover()`
    increments `N` until a page 404s or yields no new posts. Posts are plain
    server-rendered HTML, so httpx + BeautifulSoup suffices (no JS). The
    article body lives in `div.custom-post-body-content`; the fixed "Want to
    learn more…" CTA line and SEC disclaimer are stripped by text pattern
    (`_strip_blog_chrome`) since they share the body with no markup. Title
    comes from og:title (the on-page `<h1>` is often a sub-headline).
  - `commentary` — quarterly commentaries at
    `gorozen.com/commentaries/<slug>` whose PDF is **form-gated** behind a
    HubSpot embedded form. The form refuses to render its fields under
    automated browsers (Playwright/Camoufox — bot detection), so `fetch()`
    submits it directly via HubSpot's **public form API** instead: it reads
    the per-commentary `portalId`/`formId` (each commentary has its own form)
    from the page's embed config, POSTs to
    `api.hsforms.com/submissions/v3/integration/submit/<portal>/<form>` with
    firstname/lastname/email/category (hard-coded non-secret values at the top
    of `gorozen.py`), and the response either carries a `redirectUri` (→ a
    thank-you page hosting the PDF link) or an `inlineMessage` (→ HTML with the
    PDF link). Required fields differ by form version, so `_submit_hs_form`
    iteratively discovers them from the API's `REQUIRED_FIELD` errors. The PDF
    itself is publicly fetchable once revealed; it's saved to
    `data/raw/blog/gorozen/<year>/` and its text extracted with `pypdf`.
    Commentary slugs come in two shapes: newer `YYYY-q#` (`2026-q1`) and older
    `#qYYYY` (`2q2024`) — `_COMMENTARY_SLUG` matches both. If the form/PDF
    can't be reached the scraper degrades gracefully to the page teaser.
- Scrape `--limit` is source-unit scoped where implemented, not necessarily a
  global output cap. For YouTube, `kb youtube scrape --limit N` inspects up to
  N videos per registered channel and does not stop after N total new files.
- **YouTube folder stability + cross-name dedup.** Channel folders are named
  from the slugified display name, which YouTube can change (``MacroVoices``
  → ``Macro Voices``), historically forking channels into two folders and
  re-downloading everything. Two guards prevent this: (1) the folder slug is
  **pinned** in `channel.metadata['dir_slug']` on first discovery
  (`_channel_dir_slugs`/`_pin_channel_dir` in `youtube.py`) and reused even
  if the display name later changes; (2) `already_scraped()` first consults
  the `item` table by `external_id` (cached per run in
  `_known_video_ids()`, filtered to items whose md file still exists so
  vanished files get re-fetched instead of skipped) — the DB key is immune
  to name drift, unlike the disk-path check. If duplicate folders already
  exist, `scripts/fix_youtube_dup_folders.py` merges them into the canonical
  folder (slug of the current DB name), also deduping same-folder
  `undated/` vs dated copies, and re-points stale `md_path` rows.
- **YouTube proxy** (optional): to avoid YouTube's per-IP rate limiting (HTTP
  429), yt-dlp can route through SOCKS5 tunnels over SSH. `--proxy-hosts
  oc1.hevangel.com,horace.org` opens one `ssh -D` tunnel per host and
  round-robins each yt-dlp call across them; falls back to the
  `YT_DLP_PROXY_HOSTS` env var if the flag is omitted, and to a direct
  connection if neither is set. A single manual tunnel is also supported via
  `YT_DLP_PROXY=socks5://127.0.0.1:1080`. yt-dlp calls get `--proxy` (also
  `--force-ipv4` + `--retries 8 --socket-timeout 30` to survive a tunnel
  dropping mid-transfer). The `youtube-transcript-api` fallback deliberately
  goes **direct** (residential IP), not through the proxy — see the split
  below. Available SSH host aliases (configured in `~/.ssh/config`):
  `hevangel.com`, `oc1/2/3/4.hevangel.com`, `horace.org`, `serv00`. The
  `ProxyPool` tunnel manager lives in `src/kb/scrapers/proxy.py`.
- **Proxy vs transcript split** (important): yt-dlp routes through the SOCKS5
  pool because it uses the innertube API, which tolerates cloud IPs. But
  `youtube-transcript-api` scrapes the `/watch` page, which YouTube blocks
  from cloud-provider IP ranges (Oracle Cloud / AWS / GCP / Azure) with
  `RequestBlocked`. So the transcript fallback goes direct from the
  residential IP, where it works (verified: 16k-char transcript fetched
  direct vs `RequestBlocked` through every proxy egress). Do NOT add the
  proxy to `_fetch_transcript_api`.
- **serv00 excluded from proxy pool**: `serv00` accepts the SSH connection
  and binds the SOCKS port (tunnel appears "up"), but its SOCKS forwarding
  fails for actual requests (curl rc=97 / connection refused). It is omitted
  from the default `YT_DLP_PROXY_HOSTS`; the working hosts are
  `oc1/2/3/4.hevangel.com` and `horace.org`.
- **ProxyPool port & process hygiene** (Windows-specific gotcha): each tunnel
  binds the first *free* port at or above 1081, not a fixed `base_port+i`, and
  `stop()` kills ssh via `taskkill /F /T` (not `terminate()`, which ssh.exe on
  Windows routinely ignores). This matters because: (1) orphaned ssh
  processes from a prior run hold their port, and `ExitOnForwardFailure=yes`
  then kills the new ssh silently — the symptom is every tunnel "dying within
  seconds", which was previously misdiagnosed as host-side instability. (2)
  Without `taskkill`, torn-down tunnels orphan and squat ports, breaking the
  next run. `next()` also calls `_reap()` to skip any tunnel whose ssh has
  exited, so a dead connection is never handed to yt-dlp (the cause of its
  `4 bytes missing` SOCKS5 EOFError).
- Markdown is the canonical raw form. Each item's markdown front-matter
  carries `source`, `channel`, `external_id`, `url`, `published_at`, `title`,
  `lang`, plus source-specific fields. The DB row is regenerated from the
  markdown by `kb ingest`.
- **Flat-file layout** (blog, hkej, yahoohk, youtube, substack): content lives at
  `data/<source>/[<channel>/]<YYYY>/<YYYY-MM-DD>-<title>.md`; raw HTML at
  `data/raw/<source>/[<channel>/]<YYYY>/<YYYY-MM-DD>-<title>.html`.
  Set `flat_layout=True` on `ScrapedItem` to use this layout; `BaseScraper.write_md()`
  handles both old (patreon) and new layouts automatically.
- **HKEJ browser + session model.** HKEJ is behind Cloudflare, so it drives a
  real anti-detect browser (Camoufox). Two launch modes via
  `HKEJ_BROWSER_MODE`: `docker` (default) runs Camoufox in a container exposing
  a Playwright WS endpoint (`ws://127.0.0.1:9222/hkej`); the scraper connects
  over WS, restores login cookies from `data/hkej/.browser_state.json`
  (Playwright `storage_state`, so you log in once — same model as the
  substack/patreon `.session.json` cookie files), scrapes, and disconnects.
  `local` falls back to an on-host Camoufox kept warm by the daemon
  (`kb hkej browser start`). Docker mode bypasses the local daemon entirely
  (the container *is* the persistent browser). Build/run the container:
  `docker compose build camoufox` then `kb hkej docker up`. The container's
  noVNC web UI (`http://localhost:7900`) lets a human solve interactive
  Cloudflare challenges / log in. Login is auto-filled from `HKEJ_USER`/
  `HKEJ_PASS` (`HKEJ_LOGIN_MODE=auto`, default); set `manual` to force a
  human wait. Override per-run with `--browser-mode`/`--login` on
  `kb hkej scrape-author`. See `hkej.py:_docker_session` /
  `_browser_context` for the dispatch.
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
  (macrovoices, madxcap/狂徒, gorozen are channels under a single `blog` source — each
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
- **Whisper ASR transcription (`src/kb/transcribe.py`, opt-in).** YouTube
  videos where no subtitle/transcript could be fetched
  (`has_transcript=false`) can be transcribed locally with
  `kb youtube transcribe` using faster-whisper + large-v3 on GPU (RTX
  3060 Ti). The pipeline is **disabled by default**: it only runs via the
  dedicated command, or after `kb youtube scrape --transcribe` (which
  transcribes only the videos that scrape just fetched; `--no-transcribe`
  or unset `WHISPER_ENABLED` keeps scrape transcript-free). It runs **one
  video at a time** (sequential — no parallel GPU load) and downloads audio
  to `data/raw/youtube/tmp/` (`WHISPER_TMP_DIR`, resolved against
  `DATA_DIR` to match the `data/raw/<source>/` layout; gitignored, deleted
  after each item). Language is auto-detected by Whisper (Cantonese → `yue`,
  English → `en`, etc.) when `WHISPER_LANGUAGE` is empty. The full lifecycle
  is tracked in `item.transcription_status`: `NULL` → `pending` →
  `audio_downloaded` → `transcribing` → `done` (or `failed` with
  `transcription_error`). On success, `has_transcript` is flipped to `true`,
  the transcript replaces the `_(no transcript available)_` marker in the
  `.md` file, and `ingest_file()` updates the DB. Use
  `kb youtube transcribe --reset-stuck` to clear stale `transcribing` rows
  after a crash, `--retry-failed` to re-attempt failed items, and `--list`
  to preview candidates without transcribing.
- **Nightly Jenkins pipeline (`Jenkinsfile`).** A declarative pipeline runs
  every source as its own stage at 03:00 daily, then a catch-up `kb ingest`,
  `kb extract run`, and `kb progress recompute`. The Jenkins controller has no
  Python/uv/Playwright/yt-dlp, so each stage does
  `docker compose run --rm kb <cmd>` against the self-contained `kb` image
  (root `Dockerfile`, `kb` service in `docker-compose.yml`). The container
  mounts the host `data/` dir (so scraped content + HKEJ/Patreon/Substack
  session-cookie files are shared with the local `uv run kb` workflow) and
  reaches the host Postgres via `POSTGRES_HOST_DOCKER=host.docker.internal`
  (the host-side `POSTGRES_HOST` stays `localhost`). It also mounts `~/.ssh`
  read-only for the YouTube SOCKS5 proxy pool. `failFast` is off; login-gated
  stages (HKEJ/Patreon/Substack) are wrapped in `catchError` and downgrade to
  UNSTABLE (session expired → re-prime interactively), while the core
  scrape/ingest/extract stages stay red. Secrets come from the Jenkins
  Credentials store (IDs in the `environment{}` block) or fall back to `.env`
  via the service's `env_file`. See `doc/jenkins-pipeline.md` for the full
  setup (build, one-time session priming, job creation).

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
    gorozen.py         # Goehring & Rozencwajg blog + commentary PDFs (source_code='blog')
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
  transcribe.py        # Whisper ASR for YouTube items without subtitles
                       # (opt-in; `kb youtube transcribe` / scrape --transcribe)
  api/
    main.py            # FastAPI app (search, items, predictions, leaderboard,
                       # dashboard, sources, channels)
    routes_*.py
frontend/              # Vite + React + Tailwind
  src/pages/Dashboard.tsx   # pipeline progress overview (landing page)
docker/postgres/init.sql
docker/camoufox/        # Dockerfile + entrypoint.sh for the Camoufox browser
                        # container (HKEJ default browser mode; exposes a
                        # Playwright WS endpoint + noVNC web UI)
Dockerfile              # kb runtime image for the Jenkins pipeline (Python 3.12,
                        # uv, yt-dlp, Playwright chromium, ssh); `kb` service in
                        # docker-compose.yml builds this. Also runnable locally
                        # via `docker compose run --rm kb <cmd>`.
Jenkinsfile             # nightly 03:00 pipeline — one stage per source category,
                        # shells out to the kb image; see doc/jenkins-pipeline.md
migrations/
scripts/
  migrate_data_layout.py   # one-shot migration to flat-file layout
  migrate_to_blog_source.py   # consolidate macrovoices+madxcap into data/blog/
  copy_to_data_public.py   # publish configured data/ subset to data_public/
  build_data_readmes.py    # README indexes for data/ or data_public/
  fix_yahoohk_titles.py   # backfill misnamed Yahoo HK columnist files
  fix_youtube_dup_folders.py  # merge duplicate data/youtube/ channel folders
                        # (display-name drift) + undated/dated dupes; re-point
                        # stale item.md_path
  build_llm_wiki.py       # regenerate `llm-wiki/` (Karpathy-style synthesized
                        # wiki) from the DB — read-only against Postgres,
                        # clears and rewrites llm-wiki/; sections: People/
                        # (interview guests + hosts + solo authors merged
                        # across shows, opinions per topic over time with
                        # stance-flip detection, GLM-generated bios cached in
                        # scripts/llm_wiki_bios.json — `--no-bios` skips),
                        # Tickers/ (incl. rates/bond-yield backdrop),
                        # Analysts/, Themes/ (14 keyword buckets, CJK-aware),
                        # Syntheses/ (opinion shifts, cross-person
                        # disagreements, global timeline). Re-run after each
                        # scrape/extract batch to refresh. NOTE: the Mimosa
                        # write hook flags single-line `text("SELECT …")`
                        # calls as SQL-injection — keep all inline SQL in the
                        # multi-line triple-quoted form inside execute().
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
