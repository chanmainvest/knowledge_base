# CLI To Build The Database For AI Use

The CLI is the main automation surface for turning source material into an AI-usable database. The usual flow is:

```text
scrape -> markdown/raw files -> ingest -> item table -> extract -> AI tables -> search/API/frontend
```

## Initial Setup

```pwsh
uv sync
uv run playwright install chromium
docker compose up -d postgres
uv run kb db migrate
uv run kb db status
```

Secrets belong in `.env`. Do not hardcode API keys, database credentials, or site passwords in scripts.

## Build Raw Knowledge

Register sources/channels/authors as needed:

```pwsh
uv run kb youtube add-channel BloorStreetCapital
uv run kb youtube add-channel BloorStreetCapital "Custom Name"
uv run kb youtube add-channel --handle BloorStreetCapital
uv run kb youtube migrate-folders --dry-run
uv run kb youtube migrate-folders --ingest
uv run kb hkej add-author "李聲揚"
uv run kb hkej add-author "何啟聰"
uv run kb hkej add-author "高天佑"
uv run kb patreon list-creators
```

Scrape source material:

```pwsh
uv run kb youtube scrape --limit 20
uv run kb scrape run macrovoices --limit 20
uv run kb scrape run yahoohk --limit 20
uv run kb hkej scrape-author "高天佑"
uv run kb patreon scrape <creator> --limit 20
```

For YouTube, `--limit` is per registered channel, not a total cap across all channels.

For Yahoo Finance Hong Kong, authors are discovered automatically; `kb scrape run yahoohk --limit N` applies `N` per columnist. No separate author-registration command.

For Patreon, verify the saved session first with `uv run kb patreon check-session`.
If the session needs browser refresh, use `uv run kb patreon browser login` and
then re-run the scrape. Patreon output uses the same flat Markdown/raw HTML
layout as other current scrapers.

The scrapers write Markdown plus raw HTML/transcripts. This layer is useful even before database ingest because it is auditable and re-runnable.

## Ingest Markdown Into Postgres

```pwsh
uv run kb ingest
```

Ingest reads `data/**/*.md`, skips `data/raw/**`, parses front matter, upserts channels, and upserts `item` rows.

Use status to check database counts:

```pwsh
uv run kb status
uv run kb db status
```

## Extract AI Structures

Run the LLM extraction pipeline:

```pwsh
uv run kb extract run --limit 50
```

Extraction writes structured records into:

- `view_market`
- `prediction`
- `entity`
- `item_entity`

The LLM client uses OpenAI-compatible settings from `.env`. Extraction should use JSON-schema responses so downstream tables stay structured.

## Build Retrieval And Scoring

Rebuild related-item links:

```pwsh
uv run kb links --k 10
```

Rebuild leaderboard scoring:

```pwsh
uv run kb leaderboard rebuild
```

Start the API for AI tools or humans:

```pwsh
uv run kb api
```

## AI-Oriented Query Surfaces

For direct SQL access, useful tables are:

- `item`: canonical text and metadata.
- `view_market`: extracted market stance records.
- `prediction`: extracted calls and scoring fields.
- `entity` and `item_entity`: entity graph.
- `item_link`: related content.
- `hkej_article_catalog`: HKEJ discovery/download state.
- `patreon_post_catalog`: Patreon creator discovery/download state.

For HTTP access, use:

```text
GET /api/search?q=<query>
GET /api/items/<id>
GET /api/predictions?ticker=<ticker>
GET /api/leaderboard?weeks=12
```

## Recovery Pattern

If a run is interrupted:

1. Re-run the same scrape command. Scrapers skip already cached complete items.
2. Run `uv run kb ingest` to backfill database rows from any newly written Markdown.
3. Run extraction again with a bounded limit.
4. Check `uv run kb db status` and source-specific catalog tables.

For HKEJ, interrupted search discovery is tracked in `hkej_crawl_run`, `hkej_crawl_page`, and `hkej_article_catalog`. The next run can resume compatible page discovery while avoiding stale page alignment when new articles shift search pages.

For Patreon, interrupted creator discovery/downloads are tracked in
`patreon_crawl_run`, `patreon_crawl_page`, and `patreon_post_catalog`. Re-run
`uv run kb patreon scrape <creator>` or the schedulable
`uv run kb patreon scrape-creator` command; downloaded posts are skipped and remaining catalog entries stay
pending.
