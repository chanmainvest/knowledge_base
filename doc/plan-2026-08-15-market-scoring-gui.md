# Plan & implementation record — market data, scoring/leaderboard, GUI overhaul

**Date:** 2026-08-15 (overnight autonomous run)
**Scope asked:** (1) frontend GUI not user-friendly, (2) scoring of
authors/interviewees + leaderboard not really implemented, (3) market-data
pipeline not built. All three are now implemented, tested against the live
DB, and wired into the nightly pipeline. This document is the review guide.

## TL;DR of what exists now

- `kb market sync` bulk-fetches daily prices for every predicted ticker into
  a new `asset_price` store (first run: 99/110 tickers, ~4,000 rows; the 11
  misses are LLM-hallucinated symbols, tracked in `asset_ticker`).
- `kb leaderboard rebuild` scores calls from the store (no network), rolls
  up channel, **speaker** (interviewee/author) and model leaderboards.
  129 directional calls scored as of writing (71 pos / 58 neg); 169
  hold/watch quotes deliberately excluded.
- The GUI: Dashboard is now a landing page (latest items, top speakers/
  channels, pipeline table collapsed), Predictions is a real browser
  (filters, price call→eval columns, pagination), Leaderboard has
  Channels/Speakers/Models tabs, Search state lives in the URL, item pages
  show price sparklines, and `kb api` now serves the built frontend itself.
- A `Score` stage was added to the nightly Jenkins pipeline.

---

## 1. Market-data pipeline (new: `src/kb/marketdata.py`)

**Problem:** the only price code was `leaderboard._price_on`, doing **two
live `yf.download()` calls per prediction** with no cache — 420 predictions
would have meant ~840 downloads per rebuild, un-rate-limited, and scoring
was never scheduled anyway.

**Design:**
- `asset_price` (ticker, day, OHLCV) — PK (ticker, day), upserted.
- `asset_ticker` — per-ticker sync state (`ok | no_data | error`,
  first/last day, last_synced_at). Incremental runs only top up tickers
  whose last day lags today (minus a 4-day overlap); `no_data` tickers are
  retried at most weekly so dead symbols don't hammer Yahoo nightly.
- `sync()` batches 40 tickers per `yf.download` with a 2 s pause, one
  retry, and marks unresolvable tickers instead of crashing.
- `PriceTable` loads the whole (small) store into memory for scoring;
  `series()` powers the API sparklines; `ticker_stats()` powers the ticker
  directory.
- CLI: `kb market sync [--ticker T]... [--full]`, `kb market status`.

**Known data issue (pre-existing, extraction-side):** ~11 of 110 tickers
don't exist on Yahoo in the exact form the LLM emits — `^SOXX` (should be
`SOXX`), `DXY` (should be `DX-Y.NYB`), `100.HK`/`07709.HK` (HK tickers need
4-digit zero-padding), plus pure hallucinations (`DCGL`, `CAMELOT`,
`USD-USD`). Future fix belongs in `extract.py` ticker hygiene or the
extraction prompt, not here; the pipeline already degrades gracefully.

## 2. Scoring + leaderboards (`src/kb/leaderboard.py` rewritten)

**Problem:** channel-only rollups (never the speaker/interviewee making the
call), no incremental logic, rollups counted every provider-comparison run
(double counting), a naive-vs-aware datetime bug that would have crashed
every row, and neutral hold/watch quotes scored 0.0 (dragging averages to
zero and inflating n_scored).

**Design:**
- Score = `sign × return × 5` clamped ±1 (unchanged model; ±20% move over
  the horizon = full score). Prices come from `PriceTable` — zero network.
- **Neutral quotes are left `score = NULL`** (not 0.0). A "watch" is not a
  forecast; scoring it 0 made hedged speakers look average and inflated
  every n_scored. Stance sets now match the API's `_stance()` (`cover`
  bullish, `avoid` bearish).
- **As-of-now scoring:** `eval_at = min(now, made_at + horizon)`. Calls
  whose horizon hasn't elapsed score against the latest close and keep
  refreshing on later rebuilds until the horizon passes, when they freeze
  (`_final()` makes incremental runs skip frozen rows).
- **Primary-run scoping:** channel and speaker rollups count only
  `p.extraction_run_id = i.primary_extraction_run_id`. The
  provider/model leaderboard deliberately still counts every run (that's
  its purpose — comparing extractions of the same article).
- **New `leaderboard_speaker` table:** per-speaker rollup (the
  interviewee/author behind each `prediction.speaker`), across every
  channel they appear on, with their most-frequent channel attached.
- `kb leaderboard rebuild [--rescore] [--no-sync]` — rescore wipes and
  recomputes everything (needed after a scoring-model change).
- Timezone bug fixed: `datetime.now(timezone.utc)` against tz-aware DB
  datetimes (the old `datetime.utcnow()` comparison would TypeError on
  every row).

## 3. API (`src/kb/api/main.py`)

- `/api/predictions` — was a raw 100-row dump with no pagination, no
  primary-run scoping, and one filter. Now: `{items,total,limit,offset}`
  with `ticker/speaker/channel_id/direction/scored/date range/order`
  filters, `all_runs=true` to opt into provider-comparison rows.
- `/api/leaderboard` — overall now primary-run scoped; adds `speakers[]`
  from the new rollup.
- New: `/api/market/tickers` (ticker directory with sync state),
  `/api/market/prices` (downsampled daily-close series).
- `/api/dashboard` totals add `n_predictions / n_scored / n_speakers`.
- **The API now serves the built GUI** from `frontend/dist` (assets mount +
  catch-all SPA fallback), so `uv run kb api` alone is a working local
  deployment. Previously `dist/` was built but nothing served it.

## 4. Frontend GUI (`frontend/src`)

**Problems found:** Predictions page was a bare 200-row table (no loading/
error/empty states, unused `as any` cast, no pagination); Dashboard was a
12-column ops table with no way into the content; Leaderboard had no error
state (a failed fetch = stuck "Loading…" forever) and ignored
`/api/models/leaderboard`; Search filter state wasn't in the URL (back
button/shareable links broken); `Th` + comparators were copy-pasted between
pages; no 404 route; no per-page titles.

**What changed:**
- **`components/ui.tsx` (new):** Spinner, ErrorBanner, EmptyState, Pager,
  ScoreSpan/HitRateSpan, `useSort`/SortTh/compareValues (extracted from the
  duplicated page code), `useTitle`.
- **Dashboard:** landing page — stat cards (items/extracted/predictions/
  scored/speakers, all clickable), Latest items, Top speakers & channels
  (link into the new deep links), pipeline table kept but collapsed into a
  `<details>`.
- **Predictions:** full rebuild — server-driven filters (ticker with
  datalist from the ticker directory, speaker, channel, direction,
  scored-state, sort), pagination, Call→Eval price columns with % move,
  speaker links, "pending" hint for unscored calls.
- **Leaderboard:** Channels / Speakers / Models tabs. Speakers is the new
  "score the interviewee" view (cross-channel, click-through to their
  calls). Models surfaces the previously-unused
  `/api/models/leaderboard`. Error state added; weekly chart kept.
- **Search:** all filter state lives in the URL (`useSearchParams`), so
  back/forward and shareable filtered links work; stale channel selections
  are pruned when their source is deselected.
- **Item:** price sparkline per prediction card (from
  `/api/market/prices`, green/red by direction), speaker links, "Raw
  markdown" link, per-page title.
- **Shell:** 404 route, sticky header, brand block, footer, Suspense
  spinner.
- Cross-page deep links: channel → `/search?channel_id=`, speaker →
  `/predictions?speaker=…`, scored-calls stat → `/predictions?scored=true`.

## 5. Nightly pipeline (Jenkinsfile)

New `Score` stage after `Extract`: `kb leaderboard rebuild` (market sync +
scoring + rollups). UNSTABLE-not-FAILED on error — a flaky Yahoo night
shouldn't turn the pipeline red; scores refresh next successful run.

## 6. Tests & verification performed

- New `tests/test_leaderboard.py` (horizon mapping, stance precedence,
  score math incl. clamps, frozen-horizon logic) and
  `tests/test_marketdata.py` (PriceTable date semantics, NaN handling,
  DataFrame row extraction, batching). `uv run pytest`: 63 passed; the only
  failures (4, in `test_youtube.py`) pre-date this work — they're from the
  uncommitted `youtube.py` changes already in the tree, whose test fakes
  don't match the new `min_interval` kwarg.
- Live run against the real DB: `kb db migrate` → `kb market sync`
  (99/110 tickers, 4,040 rows) → `kb leaderboard rebuild` (129 scored,
  30 speakers) → API smoke tests of every new endpoint (see §3) incl. SPA
  serving (`/` and `/leaderboard` return the built HTML).
- Frontend: `npx tsc --noEmit` clean, `npm run build` clean.

## 7. Deliberately not done (future work)

Ranked by value, roughly as `doc/llm-extraction.md` Part 2 proposes:
1. **Benchmark-relative scoring (alpha)** — the price store now makes this
   cheap (fetch ^GSPC/^HSI alongside); biggest analytical upgrade.
2. **Ticker hygiene at extraction** (the 11 dead symbols) — prompt fix or
   a normalization pass (`100.HK`→`0100.HK`, `DXY`→`DX-Y.NYB`, `^SOXX`→`SOXX`).
3. **Permabull/directional-variance flag** and **confidence calibration**
   (Brier buckets) — `confidence` is extracted on market_views but still
   unused by scoring.
4. **Small-sample floor on leaderboards** (min scored-calls before ranking).
5. Persisting `speakers[]` from extraction into a table (currently speakers
   come from prediction rows only).
6. Semantic (pgvector) search endpoint — embeddings are computed but no API
   uses them.
