# Spec — Market data, scoring, leaderboards, API/frontend serving

Read this when touching `src/kb/marketdata.py`, `src/kb/leaderboard.py`,
`src/kb/api/`, or `frontend/`.

- **Market-data pipeline (`src/kb/marketdata.py`).** Daily prices for every
  ticker referenced by an extracted prediction live in the `asset_price`
  table, bulk-fetched from Yahoo Finance by `kb market sync` (batched
  `yf.download`, 40 tickers/batch with a 2 s pause; incremental — only
  tickers whose last stored day lags today get topped up; no-data tickers
  like LLM-hallucinated symbols are recorded in `asset_ticker` and retried
  at most weekly). Scoring (`kb leaderboard rebuild` = sync + score +
  rollups in one command; a nightly Jenkins stage) reads the store via an
  in-memory `PriceTable` and **never hits the network**. Scores are
  `sign × return × 5` clamped ±1 over the call's horizon; neutral quotes
  (hold/watch) are left NULL rather than scored 0; calls whose horizon
  hasn't elapsed carry an as-of-now score that refreshes until it does.
  Rollups: `leaderboard_weekly` (channel×week) and `leaderboard_speaker`
  (interviewee/author, cross-channel, with most-frequent channel) count
  **primary extraction runs only**; `provider_model_leaderboard`
  deliberately counts every run. The API serves the built frontend SPA
  from `frontend/dist` when it exists (assets mount + catch-all fallback),
  so `kb api` alone serves the GUI; run `npm run build` in `frontend/`
  after frontend changes.
- **Insights tab (llm-wiki, disk-only).** The generated wiki under
  `llm-wiki/` is **not** in the DB (only build read-tracking is, in
  `wiki_item_read`). `/api/insights` lists sections/pages and
  `/api/insights/page?section=&page=` / `/api/insights/home` return the raw
  markdown read straight from disk at request time, so a wiki rebuild shows
  up without any API restart. Section names are a fixed allowlist and page
  slugs must match a safe-filename regex (no traversal). The frontend
  `Insights` tab (`frontend/src/pages/Insights.tsx`) renders them with
  react-markdown, navigating via `?section=&page=` query params.
- **Theming.** The frontend supports dark (default) and light themes: the
  semantic tailwind colors (`bg`/`panel`/`border`/`ink`/`mute`/`accent`)
  resolve to CSS variables in `index.css` that flip on the `dark` class of
  `<html>`; the header toggle persists the choice to localStorage
  (`kb-theme`) and a pre-paint script in `index.html` applies it. Chart
  series colors pick a palette per theme via `useTheme()`; status colors
  pair `text-*-700` with `dark:text-*-400` variants.
- **Chat widget.** The floating 💬 on item pages and the Insights tab chats
  about the page you're on via `POST /api/chat`, which takes either
  `item_id`, an llm-wiki `section`+`page`, or `home: true`, and answers via
  the primary provider with an OpenRouter fallback (`OPENROUTER_API_KEY`;
  response names the `model` used). Stateless; history supplied by the
  client; content head-truncated at 80k chars.
