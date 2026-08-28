# llm-wiki — agent notes

**Every page here is generated.** Do not hand-edit pages — they are
rewritten by `scripts/build_llm_wiki.py` whenever their content
changes. To change anything, change the script (or the DB it
reads) and re-run:

```bash
uv run python scripts/build_llm_wiki.py [--no-bios]
```

## Structure

- `People/` — one page per person (interview guests, show hosts,
  solo authors), merged across every show they appear on via
  alias/generic-name resolution. Opinions per topic in chronological
  order; stance flips (bullish→bearish across *different days*) are
  flagged. GLM-written bios are cached in
  `scripts/llm_wiki_bios.json` (pass `--no-bios` to skip generation;
  failed lookups are retried on the next run).
- `Weekly/` — one page per Sunday→Saturday week, named by its
  Sunday date (`Weekly/2026-08-23.md` = Aug 23–29): an LLM-written
  digest of what dominated that week's discourse, the sharpest
  differing viewpoints, and who changed their mind, over the
  mechanical record (hot-topic table, bull/bear splits, flips,
  every extracted item of the week). The current, still-open week
  is regenerated as new items land.
- `Tickers/` — one page per topic with >= 2 predictions. Pages are keyed by Yahoo
  symbol when the ticker is symbol-like, otherwise by the
  asset/company name (the extract pipeline nulls non-symbol
  tickers into `asset_name`, so fragments like "PAN MINE" become
  name-keyed pages). Each page carries a rates/bond-yield backdrop
  section for macro context.
- `Analysts/` — one page per channel with extracted items, plus the
  people who appear on it.
- `Themes/` — hand-curated keyword buckets (CJK-aware matching); a
  theme page only exists when the current extraction has matching
  rows.
- `Syntheses/` — cross-cutting views: Opinion Shifts (same person
  changes stance), Disagreements (people on opposite sides of one
  topic, each side's latest call), and the global Timeline.
- `Studies/` — deep single-subject analyses. Current subject: how a
  channel recycles its own material over time — per-item LLM topic
  inventories over a chronologically spread sample of full
  transcripts, every item title classified into the derived
  taxonomy, then narrative synthesis. Cached in
  `scripts/llm_wiki_studies.json`.

## Provenance rules

- Each page opens with an **LLM-written narrative** (GLM 5.3 Flash) generated from a compact digest of DB facts, cached in
  `scripts/llm_wiki_prose.json` keyed by digest hash (unchanged
  data is never re-written; `--no-prose` skips generation).
- The mechanical sections below each narrative — timelines, stance
  counts, appearance tables, source-item lists — are the
  **verbatim DB record** and the ground truth to check the prose
  against.
- Bios are LLM-written from public knowledge + DB context.
- Read tracking: every item the build consumes is recorded (item id,
  purpose, sha256 of exactly what was read) in BOTH the
  `wiki_item_read` DB table and `read-state.json` in this folder —
  dual-written and reconciled on load (latest read_at wins; entries
  whose item id vanished after a DB rebuild are re-attached by
  source+external id). Unchanged items are never re-read, so no
  tokens are wasted. The JSON is committed with the wiki: after
  losing the DB, re-ingest the `data/` markdown and re-run — the
  read state (and the wiki) rebuild from the repo alone.
- Coverage mirrors extraction progress; the Home page states the
  extracted/total fraction. Re-run after each `kb extract run`
  batch.

Wiki-wide conventions and the pipeline architecture live in the
repo root `AGENTS.md`.
