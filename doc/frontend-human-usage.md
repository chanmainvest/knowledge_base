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
- `/channels`: source/channel browser with item counts.
- `/items/:id`: item detail view with content, extracted market views, predictions, entities, and related items.
- `/predictions`: extracted prediction/call browser.
- `/leaderboard`: weekly and overall channel scoring.

## API Calls Used By The UI

The frontend API client calls:

```text
GET /api/sources
GET /api/channels?source=<source>
GET /api/search?q=<query>&source=<source>&channel_id=<id>
GET /api/items?source=<source>&channel_id=<id>&limit=<n>&offset=<n>
GET /api/items/<id>
GET /api/items/<id>/raw
GET /api/predictions?ticker=<ticker>&channel_id=<id>&limit=<n>
GET /api/leaderboard?weeks=<n>
```

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