# llm-wiki — agent notes

**This whole directory is generated.** Do not hand-edit pages —
every file is rewritten by `scripts/build_llm_wiki.py`, which
clears the tree first. To change anything, change the script (or
the DB it reads) and re-run:

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

## Provenance rules

- Stances, timelines, disagreements and counts are **strictly
  DB-derived** from `item` / `prediction` / `view_market` /
  `extraction_run.raw_response->speakers` — never synthesized.
- Bios are the only LLM-written content and are marked as such on
  the page.
- Coverage mirrors extraction progress; the Home page states the
  extracted/total fraction. Re-run after each `kb extract run`
  batch.

Wiki-wide conventions and the pipeline architecture live in the
repo root `AGENTS.md`.
