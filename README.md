# Knowledge Base

Personal investment knowledge base. Scrapes podcasts, YouTube channels, and HKEJ
columnists into markdown, extracts structured views/predictions with an LLM,
and serves a search + leaderboard webapp.

## Architecture

```mermaid
flowchart LR
    subgraph SRC["Sources"]
        MV[MacroVoices podcast]
        YT[YouTube channels]
        HKEJ[HKEJ Wealth Mgmt]
    end

    subgraph SCRAPE["Scrapers (Python · Playwright · yt-dlp)"]
        S1[macrovoices.py]
        S2[youtube.py]
        S3[hkej.py]
    end

    DISK[("data/&lt;source&gt;/&lt;channel&gt;/&lt;YYYY-MM-DD&gt;__&lt;id&gt;/content.md")]

    LLM[["LLM extractor<br/>(views · predictions · tickers)"]]

    DB[("Postgres<br/>pgvector · FTS · pg_trgm")]

    API[FastAPI / uvicorn]
    UI[React + Vite + Tailwind]

    MV --> S1
    YT --> S2
    HKEJ --> S3
    S1 --> DISK
    S2 --> DISK
    S3 --> DISK
    DISK -->|kb ingest| DB
    DISK -->|kb extract run| LLM --> DB
    DB --> API --> UI
```


### Data layout on disk

```
data/
  macrovoices/<episode_slug>/{episode.md, slides.pdf, raw.html}
  youtube/<channel_handle>/<YYYY-MM-DD>__<video_id>/{transcript.md, info.json}
  hkej/<author_slug>/<YYYY-MM-DD>__<article_id>/{article.md, raw.html}
```

`data/**` is git-ignored (only structure committed). The DB is the source of
truth for search; the markdown files are the canonical raw content.

## Quick start

```pwsh
cd knowledge_base
copy .env.example .env   # fill in your secrets
uv sync
uv run playwright install chromium

docker compose up -d postgres
uv run kb db migrate

# scrape (each runs as its own job; safe in parallel)
uv run kb scrape youtube  --limit 5
uv run kb scrape macrovoices --limit 3
uv run kb scrape hkej --limit 20

# extract structure
uv run kb extract run --limit 50
uv run kb leaderboard rebuild

# serve api + frontend
uv run kb api
cd frontend && npm install && npm run dev
```

See `AGENTS.md` for design notes and conventions.
