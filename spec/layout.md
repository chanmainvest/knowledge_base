# Spec — Repository and data layout

Read this when orienting in the codebase, adding files, or changing where
scraped content lives.

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
  prompts.py           # versioned extraction prompt/schema registry — loads
                       # src/kb/prompts/extraction/<ver>/{system.md,schema.json}
                       # pairs; dir name = prompt_version (enforced vs front-matter)
  progress.py          # pipeline progress tracking — boundary counters
                       # (mark_downloaded/ingested/extracted) + recompute();
                       # backs /api/dashboard and `kb progress status`
  catalog.py           # discovery catalog — records every discovered item so
                       # "discovered but not downloaded" is queryable and
                       # `kb scrape resume` can re-fetch pending items
  leaderboard.py       # score predictions vs the price store; speaker/channel/
                       # model rollups (primary-run scoped; neutral = unscored)
  marketdata.py        # market-data pipeline — bulk yfinance → asset_price
                       # store (`kb market sync`/`status`); PriceTable used
                       # by scoring; series()/ticker_stats() for the API
  transcribe.py        # Whisper ASR for YouTube items without subtitles
                       # (opt-in; `kb youtube transcribe` / scrape --transcribe)
  api/
    main.py            # FastAPI app (search, items, predictions, leaderboard
                       # incl. speakers, market prices/tickers, dashboard,
                       # sources, channels) + serves frontend/dist as the SPA
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
  backfill_youtube_transcripts.py  # re-fetch subtitles (yue-aware) for files
                        # carrying the no-transcript marker; over-polite by
                        # design (429 cooldowns, daily slices, resumable)
  build_llm_wiki.py       # regenerate `llm-wiki/` (Karpathy-style synthesized
                        # wiki) from the DB — read-only against Postgres
                        # except the wiki_item_read bookkeeping table (the
                        # item-level read tracker, dual-written with
                        # llm-wiki/read-state.json so the state survives
                        # losing the DB: unchanged items are never re-read);
                        # INCREMENTAL: never wipes llm-wiki/, rewrites pages
                        # only when content changes, GCs stale generated
                        # pages via scripts/.llm_wiki_manifest.json, leaves
                        # any other files in llm-wiki/ untouched. Pages open
                        # with GLM 5.3 Flash-written narrative (portrait/
                        # debate/essay) from a digest of DB facts, cached in
                        # scripts/llm_wiki_prose.json by digest hash
                        # (--no-prose skips; --no-bios, --provider/--model
                        # also available). People registry is gated by a
                        # cached LLM pass (scripts/llm_wiki_people.json)
                        # that drops non-person labels the extraction emits
                        # (Grok/x.ai, companies, boilerplate) and
                        # canonicalizes misspelled/unnamed-CEO variants.
                        # Weekly/ holds one Sunday→Saturday
                        # digest per week (what people talked about, where
                        # they disagreed, who changed their mind; reads
                        # tracked under purpose 'weekly-digest'). Studies/
                        # holds LLM deep dives (e.g. how a channel recycles
                        # its own material), cached in
                        # scripts/llm_wiki_studies.json. Re-run after each
                        # scrape/extract batch. Details: `llm-wiki/AGENTS.md`.
                        # NOTE: the Mimosa write hook flags single-line
                        # `text("SELECT …")` calls as SQL-injection — keep
                        # all inline SQL in the multi-line triple-quoted
                        # form inside execute().
```

## Data directory structure

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
