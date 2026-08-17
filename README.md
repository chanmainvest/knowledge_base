# Knowledge Base

Personal investment knowledge base. Scrapes blogs (MacroVoices, MadX), YouTube
channels, HKEJ columnists, Yahoo Finance Hong Kong columnists, Master Insight
columnists, Patreon creators, and Substack publications into
markdown, extracts structured views/predictions with an LLM, and serves a search
+ leaderboard webapp.

## Architecture

```mermaid
flowchart LR
    subgraph SRC["Sources"]
        BLOG[Blogs<br/>(MacroVoices · MadX)]
        YT[YouTube channels]
        HKEJ[HKEJ Wealth Mgmt]
        YHK[Yahoo Finance HK]
        MI[Master Insight]
        PAT[Patreon creators]
        SUB[Substack publications]
    end

    subgraph SCRAPE["Scrapers (Python · Playwright · yt-dlp)"]
        S1[macrovoices.py · madxcap.py]
        S2[youtube.py]
        S3[hkej.py]
        S5[yahoohk.py]
        S6[master_insight.py]
        S4[patreon.py]
        S7[substack.py]
    end

    DISK[("data/&lt;source&gt;/[&lt;channel&gt;/]&lt;YYYY&gt;/&lt;YYYY-MM-DD&gt;-&lt;title&gt;.md")]

    LLM[["LLM extractor<br/>(views · predictions · tickers)"]]

    DB[("Postgres<br/>pgvector · FTS · pg_trgm")]

    API[FastAPI / uvicorn]
    UI[React + Vite + Tailwind]

    BLOG --> S1
    YT --> S2
    HKEJ --> S3
    YHK --> S5
    MI --> S6
    PAT --> S4
    SUB --> S7
    S1 --> DISK
    S2 --> DISK
    S3 --> DISK
    S5 --> DISK
    S6 --> DISK
    S4 --> DISK
    S7 --> DISK
    DISK -->|kb ingest| DB
    DISK -->|kb extract run| LLM --> DB
    DB --> API --> UI
```


### Data layout on disk

```
data/
  hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md        # content
  raw/hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html  # raw HTML

  yahoohk/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
  raw/yahoohk/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

  master-insight/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
  raw/master-insight/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

  blog/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.md           # MacroVoices, MadX, …
  raw/blog/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.html     # [.slides.pdf …]

  youtube/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.md

  substack/<handle>/<YYYY>/<YYYY-MM-DD>-<title>.md
  raw/substack/<handle>/<YYYY>/<YYYY-MM-DD>-<title>.html

    patreon/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.md
    raw/patreon/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.html
```

Content files carry YAML front-matter (`source`, `channel`, `external_id`,
`url`, `published_at`, `title`, …). Raw HTML and supplementary files live
under `data/raw/` mirroring the same path structure.

Scraped content lives in `data/` (local git repo; not part of this
repository). A public subset can be published in the `data_public/` git
submodule ([`chanmainvest/data_knowledge_base`](https://github.com/chanmainvest/data_knowledge_base))
with `uv run python scripts/copy_to_data_public.py` (see
`doc/scrape-util-scripts.md`).
The DB is the source of truth for search; the markdown files are the canonical
raw content.

The data directory defaults to `data/` but can be changed by setting `DATA_DIR`
in `.env` (or the `DATA_DIR` environment variable). Relative paths resolve
against the repo root; absolute paths are used as-is. For example:

```pwsh
# .env
DATA_DIR=data

# one-off override (PowerShell)
$env:DATA_DIR = "D:\my_scrape_data"; uv run kb youtube scrape --limit 5

# after changing DATA_DIR, re-index the database so item.md_path is refreshed:
uv run kb ingest
```

To migrate an existing checkout to the current layout:
```pwsh
uv run python scripts/migrate_data_layout.py   # safe to re-run; no-ops on already-flat files
uv run kb ingest                                # re-index DB with new paths
```

## Quick start

```pwsh
git clone --recurse-submodules git@github.com:chanmainvest/knowledge_base.git
cd knowledge_base
copy .env.example .env   # fill in your secrets
uv sync
uv run playwright install chromium

docker compose up -d postgres
uv run kb db migrate

# scrape (each runs as its own job; safe in parallel)
uv run kb youtube scrape --limit 5
uv run kb youtube backfill-dates        # repair items scraped without a publish date
uv run kb youtube backfill-metadata --limit 50   # refill empty descriptions (rate-limited, resumable)
uv run kb blog scrape macrovoices --limit 3
uv run kb blog scrape madxcap --limit 5
# HKEJ is behind Cloudflare → needs the Camoufox browser container (default mode).
docker compose build camoufox
uv run kb hkej docker up        # then scrape; open http://localhost:7900 if Cloudflare needs a human
uv run kb hkej scrape-author 高天佑 --limit 1
uv run kb scrape run yahoohk --limit 5
uv run kb master-insight add-author tangwenliang
uv run kb scrape run master-insight --limit 5
uv run kb patreon scrape <creator> --limit 3
uv run kb substack prime-session          # log in once, interactively (headed browser)
uv run kb substack scrape <handle> --limit 3

# extract structure
uv run kb extract run --limit 50

# market data + scoring: fetch daily prices for every predicted ticker into
# the asset_price store (bulk yfinance, incremental), score calls against it,
# rebuild the channel/speaker/model leaderboards
uv run kb market sync
uv run kb market status            # per-ticker coverage
uv run kb leaderboard rebuild      # sync + score + rollups in one command

# regenerate the synthesized wiki (people, tickers, themes, syntheses)
uv run python scripts/build_llm_wiki.py

# serve api + frontend
uv run kb api                      # serves the API on :8088 and, if
                                  # frontend/dist exists, the built GUI too
cd frontend && npm install && npm run dev   # or the Vite dev server w/ proxy
```

The `llm-wiki/` directory is a generated, Karpathy-style synthesized wiki
built from the extracted data: one page per **person** (interview guests,
hosts and solo authors, merged across every show they appear on, with their
opinions per topic over time and stance-change detection), per **ticker**
(with a rates/bond-yield backdrop), per **channel**, per **theme**, plus
**Syntheses** pages (same-person opinion shifts, cross-person
disagreements, and a global timeline of calls). See `llm-wiki/README.md`.

See `AGENTS.md` for design notes and conventions. Scraper details live in
`doc/scrape-util-scripts.md`. For exactly how extraction turns Markdown into
scored predictions, how to judge which channels are worth following, and how
to run/compare multiple LLM providers, see `doc/llm-extraction.md`.

## Nightly Jenkins pipeline

A [`Jenkinsfile`](Jenkinsfile) runs the full scrape → ingest → extract →
score pipeline nightly at 03:00, one stage per source category. Jenkins
shells out to a self-contained `kb` Docker image (`docker compose build kb`)
rather than installing Python/uv/yt-dlp itself; the container mounts `data/`
and reaches the host Postgres, so it shares your existing items. The final
`Score` stage runs `kb leaderboard rebuild` (market-data sync + prediction
scoring + leaderboard rollups). Secrets come from the Jenkins Credentials
store (or the gitignored `.env`). See
[`doc/jenkins-pipeline.md`](doc/jenkins-pipeline.md) for build, one-time
session priming (HKEJ/Patreon/Substack), and job-creation steps.
