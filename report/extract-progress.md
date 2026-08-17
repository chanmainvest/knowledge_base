# How extraction progress is implemented

*Written 2026-08-15. Describes the code as of this date; file/line references
are approximate anchors, not a stable API.*

Extraction progress is tracked in three layers — per-item state, per-attempt
audit, and per-source rollups — with the `item` table acting as the
authoritative source that everything else is reconciled against.

## 1. Per-item state (the source of truth)

Every ingested item carries an `extraction_status` column with a DB default
of `'pending'` (`docker/postgres/init.sql:45`, indexed at line 52), plus
`extracted_at` and `extraction_error`. Ingest itself doesn't touch these —
an item simply starts life as `pending` and only leaves that state when
extraction settles it as `'done'` or `'error'`.

## 2. The batch driver — `extract.py:run()` (src/kb/extract.py:420)

`kb extract run --limit N` works in one pass:

1. Selects up to N item ids with `WHERE extraction_status='pending'`,
   **newest first** (`ORDER BY published_at DESC NULLS LAST`) — so the
   nightly batch always spends its budget on the freshest content.
2. Calls `extract_item()` on each. That function chunks the content, calls
   the LLM per chunk, and aggregates the results.
3. On success (and only when the run is "primary" — i.e. it used the
   configured default provider, so ad-hoc multi-provider comparison runs
   don't disturb the canonical view): it promotes the run via
   `_promote_primary()`, stamps `item.extracted_at = now()`, and calls
   `progress.mark_extracted(source_id, "done")` (src/kb/extract.py:242-253).
4. On LLM failure: the item is flipped to `extraction_status='error'` with
   the message truncated into `extraction_error`, and
   `mark_extracted(source_id, "error")` — this was a deliberate fix so
   failures are visible instead of leaving items silently pending forever
   (src/kb/extract.py:218-236).
5. After the whole batch, it calls `progress.recompute()` once as a safety
   net (src/kb/extract.py:444-449).

## 3. Per-attempt audit — the `extraction_run` table

Every attempt — success or failure — gets its own `extraction_run` row
recording item, provider, model, prompt version, status, duration, error
text, and the raw aggregated response. This is what lets multiple providers
extract the same item without clobbering each other, and drives
`kb extract compare` and the provider/model leaderboard. Each item points at
one `primary_extraction_run_id` which is what the API, frontend, and
leaderboard read by default.

## 4. Per-source rollups — the `source_progress` table (src/kb/progress.py)

The `mark_extracted(source_id, status)` boundary function does an O(1)
UPSERT that increments either `n_extracted` or `n_extract_error` and stamps
`last_extract_at` (src/kb/progress.py:90-111). One known quirk, documented
in the code: these counters track attempts that settled, not net
transitions — re-extracting an already-done item over-counts. That drift is
intentional and harmless because of the reconciliation layer:

- `recompute()` (src/kb/progress.py:114-158) does a full authoritative
  recount straight from the `item` table
  (`COUNT(*) ... WHERE extraction_status = 'done'/'pending'/'error'`) and
  overwrites all the rollup counters. It runs at the end of every
  `extract run` batch, on demand via `kb progress recompute`, and as an
  init.sql backfill.

## 5. What the dashboard actually reads

The `/api/dashboard` endpoint (src/kb/api/main.py:170-231) does **not**
trust the incrementally-maintained cache for extraction numbers. It computes
them live with a single GROUP BY over `item` using
`COUNT(...) FILTER (WHERE extraction_status = ...)`, precisely so
`n_extract_pending` can never drift — it is
`n_ingested - n_extracted - n_extract_error` by construction. The
`source_progress` table only contributes `last_scrape_at` there, and
`n_downloaded` comes from a filesystem scan since downloaded-but-un-ingested
files have no item row yet.

## Summary

Pending items are picked newest-first; each attempt is journaled in
`extraction_run`; item status flips to done/error with a timestamp; cheap
incremental counters keep the rollup table warm during a run; and both a
batch-end recompute and live dashboard queries reconcile everything back to
the `item` table — so the reported progress is always derived from per-item
state rather than trusting the increments.
