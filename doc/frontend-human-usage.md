# Frontend For Human Usage

The human-facing app is a Vite + React + Tailwind interface in `frontend/`. It talks to the FastAPI backend through same-origin `/api/*` routes. In development, Vite proxies API requests; in production, the frontend can be served beside the API.

## Start The App

Start the API:

```pwsh
uv run kb api
```

Start the frontend:

```pwsh
npm --prefix frontend install
npm --prefix frontend run dev -- --host 127.0.0.1
```

VS Code tasks are also available:

- `Run KB API`
- `Run KB Frontend`

## Main Views

The React shell routes to these pages:

- `/search`: full-text search across ingested Markdown content.
- `/channels`: sortable/filterable source/channel browser with item counts and prediction stats.
- `/items/:id`: item detail view with content, extracted market views, predictions, entities, and related items.
- `/predictions`: extracted prediction/call browser.
- `/leaderboard`: weekly and overall channel scoring.

## API Calls Used By The UI

The frontend API client calls:

```text
GET /api/sources
GET /api/channels?source=<source>&source=<source2>...
GET /api/search?q=<query>&source=<source>&channel_id=<id>&date_from=<yyyy-mm-dd>&date_to=<yyyy-mm-dd>&has_predictions=<true|false>&limit=<n>&offset=<n>
GET /api/items?source=<source>&channel_id=<id>&date_from=<yyyy-mm-dd>&date_to=<yyyy-mm-dd>&has_predictions=<true|false>&limit=<n>&offset=<n>
GET /api/items/<id>
GET /api/items/<id>/raw
GET /api/predictions?ticker=<ticker>&channel_id=<id>&limit=<n>
GET /api/leaderboard?weeks=<n>
```

`source` and `channel_id` are repeatable for multi-select (e.g.
`?source=hkej&source=youtube`). `q` is optional on `/api/search`; when
omitted (or blank) it behaves like `/api/items` and browses the latest items
by `published_at DESC` instead of ranking by text relevance. `date_from`/
`date_to` filter by `published_at` inclusively. `has_predictions` filters to
items that do/don't have a canonical extraction with at least one prediction.
`/api/search` and `/api/items` both return
`{"items": [...], "total": <n>, "limit": <n>, "offset": <n>}` so the UI can
paginate; `limit` defaults to 25 for search and 50 for items, capped at 200.
`/api/channels` also returns per-channel prediction stats from the canonical
extraction run: `n_calls`, `n_scored`, `avg_score`, `hit_rate` (same
convention/scoring as `/api/leaderboard`, see `doc/llm-extraction.md`).

The Search page (`/search`) puts these filters (date range, prediction
extraction, sources, channels) in a right-hand panel, defaults to browsing the
latest items when no query is entered, and supports a Rows-per-page selector
(25/50/100/200) with Prev/Next pagination.

The Channels page (`/channels`) fetches the full channel list once and does
all sorting/filtering client-side: click any column header to sort by it
(again to reverse direction); the Channel and Source columns additionally get
an Excel-style filter — a dropdown of checkboxes (one per distinct value,
with a search box once there are more than a handful) plus "All"/"None"
shortcuts that apply immediately but deliberately leave the dropdown open so
the selection can be fine-tuned. See `frontend/src/components/ColumnFilter.tsx`
for the reusable filter component.

The API additionally exposes two routes not yet wired into the React UI, for
inspecting/cross-referencing multi-provider extraction (see
`doc/llm-extraction.md`):

```text
GET /api/items/<id>/runs        # every extraction_run for an item (one per
                                 # provider/model tried), with is_primary flag
GET /api/models/leaderboard     # provider/model accuracy, overall + per channel
```

`GET /api/items/<id>` also accepts an optional `?run_id=<extraction_run id>` to
show the market views/predictions from a specific (non-primary) run instead of
the item's canonical one — useful for a future "compare providers" panel.

## Human Workflow

1. Scrape or ingest content.
2. Run extraction so market views and predictions are available.
3. Start API and frontend.
4. Use Search for source material and Item Detail for the full Markdown-backed record.
5. Use Predictions and Leaderboard to inspect extracted calls and channel performance.

## Troubleshooting

If the frontend loads but API calls fail:

- confirm `uv run kb api` is running
- confirm Postgres is running
- run `uv run kb db status`
- check the browser network tab for failing `/api/*` requests

If search returns little or nothing:

- run `uv run kb ingest`
- check that `item.content` is populated
- verify the source/channel filters are not too narrow

If predictions or leaderboard are empty:

- run `uv run kb extract run --limit 50`
- run `uv run kb leaderboard rebuild`