# llm-wiki

A Karpathy-style synthesized wiki generated **from** the `knowledge_base` Postgres database. Not hand-written — every page is rendered by `scripts/build_llm_wiki.py` from the `item`, `prediction`, `view_market`, `channel`, `source` tables and the per-item speaker lists in `extraction_run`.

## Regenerate

```bash
# from the repo root, with the postgres container running
uv run python scripts/build_llm_wiki.py
# add --no-bios to skip LLM bio generation for People pages
```

The script is **read-only** against the DB and only writes under `llm-wiki/` (plus the bio cache `scripts/llm_wiki_bios.json`, so bios are only generated once per person). The build is **incremental**: files are only rewritten when their content changes, stale generated pages are garbage-collected, and any other files you keep here are left alone.

## What it produces

```
llm-wiki/
  Home.md             # overview + DB stats + caveats + marquee pages
  _Index.md           # alphabetical index
  README.md           # this file
  AGENTS.md           # agent notes (also generated — don't hand-edit)
  People/    (53 pages)   # one per person (guests, hosts,
                        #   solo authors merged across shows) — opinions
                        #   per topic over time, flips flagged, LLM bios
  Tickers/   (52 pages)   # one per ticker >= 2 mentions,
                        #   incl. rates/bond-yield backdrop
  Analysts/  (37 pages)   # one per channel with extracted items
  Themes/    (14 pages)   # cross-cutting theses (gold, AI-semis, …)
  Syntheses/ (3 pages)   # opinion shifts · disagreements · timeline
```

## Data snapshot at generation time

- Generated: **2026-08-15 06:29 UTC**
- Items in DB: **31,932** (extracted: **228**, pending: **31,701**)
- Predictions: **420** · Market views: **556**
- Distinct tickers with calls: **110** · People pages: **53**
- Published-date range: **2004-05-06 → 2026-08-13**

## Important caveats

1. **Coverage is thin.** The wiki reflects only the items the extraction pipeline has processed so far (a small fraction of the ingested corpus). It will get denser and more accurate as more items are extracted. **Re-run after each scrape/extraction batch.**
2. **No performance scores.** Predictions carry `score` columns but none are evaluated yet (`n_scored=0`). There is no hit-rate / track-record data — only stated calls.
3. **People bios are LLM-generated** (default provider: GLM via `zai`) from public knowledge + DB context, and cached. Stances, timelines, shifts and disagreements are strictly DB-derived from extracted quotes.
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
