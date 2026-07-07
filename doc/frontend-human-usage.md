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

## Build / Production

```pwsh
npm --prefix frontend run build
```

Outputs static files to `frontend/dist/`. Vite is configured to code-split so
the initial page load stays small (~180 kB JS gzipped for the default
`/search` route instead of a single 730 kB bundle):

- **Route-based lazy loading** — `frontend/src/main.tsx` wraps every page in
  `React.lazy()` + `<Suspense>`, so each route is its own chunk.
- **`build.rollupOptions.output.manualChunks`** (in `vite.config.ts`) splits
  stable third-party libs into long-cacheable vendor chunks (`react`,
  `react-dom`, `router`) and isolates heavy page-specific libs into their own
  chunks (`recharts` for `/leaderboard`, `markdown` for `/items/:id`) that only
  download when the corresponding route is visited.

When adding a new page or heavy dependency, prefer keeping it within a
lazily-loaded route so it does not bloat the initial bundle.

## Main Views

The React shell routes to these pages:

- `/dashboard`: per-source pipeline progress overview — discovered / pending-download / downloaded / ingested / extracted / pending-extraction / error counts (the latter two flag amber/red when > 0), plus last-run timestamps and an upstream total where the source API exposes one. The landing route (`/`) redirects here. "Pending download" = items the scraper discovered but never finished fetching; re-attempt with `kb scrape resume <code>`.
- `/search`: full-text search across ingested Markdown content.
- `/channels`: sortable/filterable source/channel browser with item counts and prediction stats.
- `/items/:id`: item detail view with content, extracted market views, predictions, entities, and related items.
- `/predictions`: extracted prediction/call browser.
- `/leaderboard`: weekly and overall channel scoring.

### Item detail (`/items/:id`)

The Predictions panel shows **one card per ticker**, not one per flat row.
The backend groups same-ticker predictions for an item into a single entry
with a `quotes[]` array (see "Within-article consolidation" in
`doc/llm-extraction.md`), so multiple quotes on the same asset in the same
article appear together rather than as duplicate cards. Each card shows the
ticker, asset name, a consensus `direction` (color-coded), an amber
**conflict** badge when the same ticker has both bullish and bearish calls in
the article, and the list of underlying quotes. Clicking a quote smooth-scrolls
to and briefly highlights that text inside the article body (matching is
whitespace-normalized; if the LLM excerpt doesn't appear verbatim in the
markdown, nothing happens).

## API Calls Used By The UI

The frontend API client calls:

```text
GET /api/sources
GET /api/dashboard               # per-source pipeline progress + global totals
GET /api/channels?source=<source>&source=<source2>...
GET /api/search?q=<query>&source=<source>&channel_id=<id>&date_from=<yyyy-mm-dd>&date_to=<yyyy-mm-dd>&has_predictions=<true|false>&limit=<n>&offset=<n>
GET /api/items?source=<source>&channel_id=<id>&date_from=<yyyy-mm-dd>&date_to=<yyyy-mm-dd>&has_predictions=<true|false>&limit=<n>&offset=<n>
GET /api/items/<id>              # predictions consolidated per ticker (quotes[] + conflict flag)
GET /api/items/<id>/raw
GET /api/predictions?ticker=<ticker>&channel_id=<id>&limit=<n>
GET /api/leaderboard?weeks=<n>&date_from=<yyyy-mm-dd>&date_to=<yyyy-mm-dd>
```

`source` and `channel_id` are repeatable for multi-select (e.g.
`?source=hkej&source=youtube`). `q` is optional on `/api/search`; when
omitted (or blank) it behaves like `/api/items` and browses the latest items
by `published_at DESC` instead of ranking by text relevance. `date_from`/
`date_to` filter by `published_at` inclusively. `has_predictions` filters to
items that do/don't have a canonical extraction with at least one prediction.
On `/api/leaderboard`, `date_from`/`date_to` override the `weeks` window with
an explicit inclusive range — applied to `leaderboard_weekly.week_start` for
the weekly series and to `item.published_at` for the overall aggregate.
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

The Leaderboard page (`/leaderboard`) offers the same interactive table
(sortable column headers with Excel-style Channel/Source filters) for the
overall ranking. At the top, a channel-name search box sits beside an
inclusive "Date range" (From/To); the text query filters channel names
client-side while the date range overrides the "Window" selector
(4/12/26/52 weeks) and is applied to both the weekly chart series and the
overall aggregate — greying out the weeks buttons until cleared.

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