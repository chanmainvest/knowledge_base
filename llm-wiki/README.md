# llm-wiki

A Karpathy-style synthesized wiki generated **from** the `knowledge_base` Postgres database. Not hand-written — every page is rendered by `scripts/build_llm_wiki.py` from the `item`, `prediction`, `view_market`, `channel`, and `source` tables.

## Regenerate

```bash
# from the repo root, with the postgres container running
uv run python scripts/build_llm_wiki.py
```

The script is **read-only** against the DB and only writes under `llm-wiki/`. It clears the directory first, so it is fully idempotent.

## What it produces

```
llm-wiki/
  Home.md             # overview + DB stats + caveats + marquee pages
  _Index.md           # alphabetical index
  README.md           # this file
  Tickers/   (29 pages)   # one per ticker >= 2 mentions
  Analysts/  (22 pages)   # one per channel with extracted items
  Themes/    (9 pages)   # cross-cutting theses (gold, AI-semis, …)
```

## Data snapshot at generation time

- Generated: **2026-08-03 07:01 UTC**
- Items in DB: **26,370** (extracted: **74**, pending: **26,295**)
- Predictions: **207** · Market views: **474**
- Distinct tickers with calls: **75** · Speakers: **30**
- Published-date range: **2004-05-06 → 2026-08-02**

## Important caveats

1. **Coverage is thin.** The wiki reflects only the items the extraction pipeline has processed so far (a small fraction of the ingested corpus). It will get denser and more accurate as more items are extracted. **Re-run after each scrape/extraction batch.**
2. **No performance scores.** Predictions carry `score` columns but none are evaluated yet (`n_scored=0`). There is no hit-rate / track-record data — only stated calls.
3. **Single extraction provider.** All extractions currently come from the `github` (Copilot CLI) provider. The DB supports multi-provider comparison but only one has run.
4. **Themes are keyword-bucketed**, not semantically clustered — approximate by design.
5. **Quotes are LLM-extracted**, not curated. They can misattribute or trim. Always follow the source-item link to verify.

## How claims are cited

Every prediction/view cites its source item by:
- the item **title** (linked to its external URL — YouTube watch link, HKEJ article, Substack post, etc.),
- the **published date**,
- the **channel/analyst** name,
- and where relevant, the extracted **quote**.

The `external_id` (e.g. a YouTube video id) is the item's stable key in the DB; the URL is its public location.

## Related

- [Home](Home.md)
- [_Index](_Index.md)
- Repo root `AGENTS.md` for the full pipeline architecture.
