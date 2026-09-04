# llm-wiki

A Karpathy-style synthesized wiki generated **from** the `knowledge_base` Postgres database. Not hand-written — every page is rendered by `scripts/build_llm_wiki.py` from the `item`, `prediction`, `view_market`, `channel`, `source` tables and the per-item speaker lists in `extraction_run`.

## Regenerate

```bash
# from the repo root, with the postgres container running
uv run python scripts/build_llm_wiki.py
# add --no-bios to skip LLM bio generation for People pages
```

The script is read-only against the DB except for one bookkeeping table — `wiki_item_read`, the item-level read tracker, dual-written with `read-state.json` here so the state survives losing the database. Everything else it writes lives under `llm-wiki/` (plus the bio cache `scripts/llm_wiki_bios.json`, so bios are only generated once per person). The build is **incremental**: files are only rewritten when their content changes, stale generated pages are garbage-collected, and any other files you keep here are left alone.

## What it produces

```
llm-wiki/
  Home.md             # overview + DB stats + caveats + marquee pages
  _Index.md           # alphabetical index
  README.md           # this file
  AGENTS.md           # agent notes (also generated — don't hand-edit)
  read-state.json     # item read-tracking (dual-written with the DB's
                      #   wiki_item_read table; survives losing the DB)
  People/    (162 pages)   # one per person (guests, hosts,
                        #   solo authors merged across shows) — opinions
                        #   per topic over time, flips flagged, LLM bios
  Weekly/    (12 pages)   # Sunday→Saturday weekly digests: what people
                        #   talked about, where they disagreed, who changed
                        #   their mind that week
  Tickers/   (150 pages)   # one per ticker >= 2 mentions,
                        #   incl. rates/bond-yield backdrop
  Analysts/  (46 pages)   # one per channel with extracted items
  Themes/    (14 pages)   # cross-cutting theses (gold, AI-semis, …)
  Syntheses/ (3 pages)   # opinion shifts · disagreements · timeline
  Studies/   (1 pages)   # deep dives (e.g. how a channel
                        #   recycles its own material over time)
```

## Data snapshot at generation time

- Generated: **2026-08-30 09:37 UTC**
- Items in DB: **32,682** (extracted: **932**, pending: **31,694**)
- Predictions: **2,744** · Market views: **3,243**
- Distinct tickers with calls: **283** · People pages: **162**
- Published-date range: **2004-05-06 → 2026-08-29**

## Important caveats

1. **Coverage is thin.** The wiki reflects only the items the extraction pipeline has processed so far (a small fraction of the ingested corpus). It will get denser and more accurate as more items are extracted. **Re-run after each scrape/extraction batch.**
2. **No performance scores.** Predictions carry `score` columns but none are evaluated yet (`n_scored=0`). There is no hit-rate / track-record data — only stated calls.
3. **The narrative sections are LLM-written** (GLM 5.3 Flash) from a digest of the DB facts, with ready-made citation links — they can misread or over-summarise. The tables and timelines below each narrative are the verbatim DB record; bios are LLM-written too. Verify anything load-bearing against the cited source items.
4. **Themes are keyword-bucketed**, not semantically clustered — approximate by design.
5. **Quotes are LLM-extracted**, not curated. They can misattribute or trim. Always follow the source-item link to verify.

## How claims are cited

Every prediction/view cites its source item by:
- the item **title** (linked to its external URL — YouTube watch link, HKEJ article, Substack post, etc.),
- the **published date**,
- the **channel/analyst** name (and, where attributed, the person),
- and where relevant, the extracted **quote**.

The `external_id` (e.g. a YouTube video id) is the item's stable key in the DB; the URL is its public location.

## Related

- [Home](Home.md)
- [_Index](_Index.md)
- Repo root `AGENTS.md` for the full pipeline architecture.
