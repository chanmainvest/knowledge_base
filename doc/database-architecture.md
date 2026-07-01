# Database Architecture

The database is Postgres 16 with pgvector, trigram search, and generated full-text vectors. The schema is initialized by `docker/postgres/init.sql` and applied with:

```pwsh
docker compose up -d postgres
uv run kb db migrate
```

## Core Tables

`source` stores source definitions such as `macrovoices`, `youtube`, `hkej`, `yahoohk`, `patreon`, and `substack`.

`channel` stores channels, creators, or authors inside each source. Examples include YouTube handles, Patreon creators, Substack publications, and HKEJ author handles.

`item` stores ingested content. Each item points to a source and optionally a channel, and carries the external ID, title, URL, publication date, Markdown path, raw path, content, summary, and extraction status.

`chunk` stores passage chunks and optional vector embeddings for semantic retrieval.

## Search Indexes

The `item.tsv` column is generated from title and content:

```sql
setweight(to_tsvector('simple', coalesce(title,'')), 'A') ||
setweight(to_tsvector('simple', coalesce(content,'')), 'B')
```

Important indexes:

- `item_tsv_idx`: full-text search over title and content.
- `item_title_trgm_idx`: trigram title matching.
- `item_published_at_idx`: recent-item ordering.
- `chunk_emb_idx`: pgvector cosine index for embedding search.

## Extraction Tables

`extraction_run` stores one row per (item, LLM provider, model,
prompt_version) extraction attempt: status (`running`/`done`/`error`),
error message, summary, full raw aggregated response (JSONB), and timing.
Running the same item through several providers keeps every provider's rows
side by side instead of one overwriting another. See `doc/llm-extraction.md`
for the full design and why it exists.

`item.primary_extraction_run_id` points at the `extraction_run` considered
canonical for that item — the one the API/frontend/per-channel leaderboard
use by default. Ordinary `kb extract run` calls always promote their result
to primary; `kb extract compare` never does.

`view_market` stores extracted market views: speaker, asset class, region,
direction, horizon, confidence, rationale, and quote — each tagged with the
`extraction_run_id` that produced it.

`prediction` stores extracted calls with ticker, asset, action, target,
stop, timeframe, direction, quote, and scoring fields — also tagged with
`extraction_run_id`.

`entity` and `item_entity` store people, companies, countries, themes, and
their links to items.

`item_link` stores similarity links between items.

`leaderboard_weekly` stores weekly channel scoring rollups.

`provider_model_leaderboard` stores accuracy rollups grouped by
`(provider, model)` and, separately, by `(provider, model, channel_id)` —
lets you cross-reference which LLM provider/model is the most accurate
reader of a given source. Rebuilt by `uv run kb leaderboard rebuild`.

## HKEJ Crawl Catalog

HKEJ has extra tables because search pages can shift when new articles are published and long downloads can be interrupted.

`hkej_author_state` stores per-author crawl state:

- current HKEJ search total
- max search page
- number of cataloged articles
- last seen timestamp
- last full crawl timestamp

`hkej_crawl_run` stores one row per discovery/download run:

- status: `running`, `partial`, or `finished`
- search total and max page observed
- pages crawled
- pages reused from a compatible previous run

`hkej_crawl_page` stores each crawled search page:

- run ID
- channel ID
- page number
- search total and max page at the time
- article IDs in page order
- page fingerprint

`hkej_article_catalog` stores one row per discovered HKEJ article:

- date
- title
- article URL
- external ID
- first/last seen run
- last seen page
- downloaded flag
- Markdown and raw HTML paths

## HKEJ Resume Rule

On every HKEJ run, page 1 is crawled fresh. Previously crawled pages are reused only when all of these match:

1. Current search total.
2. Current max page.
3. Page-1 fingerprint.

If any of those differ, the scraper assumes page alignment may have shifted and crawls fresh pages instead of trusting old page numbers.

## Patreon Crawl Catalog

Patreon has a similar DB-backed catalog because creator feeds can be long and
downloads may be interrupted.

`patreon_creator_state` stores per-creator crawl state:

- Patreon campaign ID
- current total post count when available
- number of cataloged posts
- last seen timestamp
- last full crawl timestamp

`patreon_crawl_run` stores one row per creator crawl:

- status: `running`, `partial`, or `finished`
- total posts observed
- pages crawled
- pages reused from a compatible previous run

`patreon_crawl_page` stores each API page:

- run ID
- channel ID
- page number
- post IDs in page order
- page fingerprint
- next-page cursor URL

`patreon_post_catalog` stores one row per discovered Patreon post:

- publication date and year
- title and post URL
- external post ID
- first/last seen run
- last seen page
- downloaded flag
- Markdown path

On every Patreon crawl, page 1 is fetched fresh. Previously crawled pages are
reused only when the page-1 fingerprint and compatible total post count match,
so new posts do not cause stale page alignment. Downloads first read the
post-detail API; when API content is empty, the scraper renders the post page
with Playwright and extracts visible `.patreon-post-content` text.

## Regenerating Database Content

Markdown remains the canonical raw source. Rebuild item rows from files with:

```pwsh
uv run kb ingest
```

Then rebuild derived AI/search structures:

```pwsh
uv run kb extract run --limit 50
uv run kb links --k 10
uv run kb leaderboard rebuild
```

See `doc/llm-extraction.md` for exactly how extraction turns Markdown into
`view_market`/`prediction` rows, how those are scored, and how to run/compare
multiple LLM providers against the same items.