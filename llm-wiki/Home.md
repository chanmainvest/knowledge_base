# Knowledge Base Wiki

> A synthesized, human-readable view of the investment-knowledge database — what analysts and channels are saying, distilled from extracted predictions and market views. Karpathy-style: dense links, honest about uncertainty.

## What this is

This wiki is generated directly from the `knowledge_base` Postgres database: every quote, target price, and stance below is pulled from an LLM extraction of a real scraped item (YouTube transcript, HKEJ/Master Insight column, Substack/Patreon post, or blog). Each claim cites its source item by title, date, channel, and external URL.

## Database at a glance

| | |
|---|---|
| Total items ingested | **34,332** |
| Items extracted (LLM) | **228** (of 34,332; 34,101 pending) |
| Predictions extracted | **420** |
| Market views extracted | **556** |
| Distinct tickers with calls | **110** |
| People with pages | **53** |
| Channels (analysts) | **77** |
| Published-date range | **2004-05-06 → 2026-08-13** |

### Sources (by item volume)

| Source | Kind | Items | Extracted |
|---|---|---:|---:|
| YouTube (`youtube`) | youtube | 20,284 | 126 |
| Hong Kong Economic Journal (`hkej`) | newspaper | 5,536 | 33 |
| Master Insight (`master-insight`) | newspaper | 2,804 | 27 |
| Patreon (`patreon`) | membership | 2,349 | 23 |
| Blogs (`blog`) | blog | 1,672 | 12 |
| Yahoo Finance Hong Kong (`yahoohk`) | newspaper | 1,407 | 0 |
| Substack (`substack`) | membership | 280 | 7 |

### Languages represented
`en` (18,573), `zh-Hant` (5,542), `zh-Hant-HK` (4,211), `en-US` (3,883), `yue` (1,102), `zh-TW` (559), `vi` (261), `th` (114)

## How to read this wiki

- **[People/](People)** — one page per *person*: interview guests, show hosts and solo authors, merged across every show they appear on. Each page tracks their opinions per topic **over time** and flags where they changed their mind.
- **[Tickers/](Tickers)** — one page per asset with enough mentions. Consensus direction, conflict flags, the people on each side, a rates/bond-yield backdrop, and every source item.
- **[Themes/](Themes)** — cross-cutting theses inferred from the predictions (AI-semis, gold, oil, rates, electrification, …).
- **[Syntheses/](Syntheses)** — the multi-dimensional views: where the *same person* flipped stance, where *different people* disagree on the same topic, and a global timeline of calls.
- **[Analysts/](Analysts)** — one page per channel that has extracted content, with the people who appear on it.
- **[_Index](_Index)** — flat alphabetical index of every page.

## ⚠️ Important caveats — read before drawing conclusions

1. **Extraction coverage is very thin.** Only **228** of **34,332** ingested items have been LLM-extracted so far (0.66%). Everything below reflects that small slice — it is **not** a representative sample of the full corpus. Treat consensus counts as directional, not authoritative.
2. **No scores yet.** Predictions in this DB carry `score` fields, but none have been evaluated against market prices (`n_scored=0`). There is no track record / hit-rate data to report — only stated calls.
3. **People bios are LLM-written** (from public knowledge + this DB's context) and can be wrong. Stances/timelines, by contrast, are strictly DB-derived from extracted quotes.
4. **Channel metadata is sparse.** Most channels have no bio/url in the DB; analyst pages say so rather than invent.
5. **Re-run to refresh.** After new scrapes/extraction, regenerate with `uv run python scripts/build_llm_wiki.py` (see [README](README)).

## Marquee pages

- [aminvest](People/aminvest.md) — 11 appearance(s), 36 extracted call(s)
- [高天佑](People/person-008.md) — 10 appearance(s), 16 extracted call(s)
- [Jeff Snider](People/jeff-snider.md) — 9 appearance(s), 61 extracted call(s)
- [何啟聰](People/person-001.md) — 4 appearance(s), 24 extracted call(s)
- [梁天卓](People/person-005.md) — 4 appearance(s), 2 extracted call(s)
- [Tickers/GC=F](Tickers/GC=F.md) — 39 analyst mentions
- [Tickers/CL=F](Tickers/CL=F.md) — 33 analyst mentions
- [Tickers/spacex](Tickers/spacex.md) — 14 analyst mentions
- [Tickers/^GSPC](Tickers/^GSPC.md) — 13 analyst mentions
- [Tickers/^TNX](Tickers/^TNX.md) — 13 analyst mentions

## Recently extracted items

- 2026-08-13 — 日軍國幽靈重現 保釣風雲再起？ (雷鼎鳴, `master-insight`)
- 2026-08-13 — 跨界算力的機遇 (胡孟青, `master-insight`)
- 2026-08-13 — A Big Move Is Right Around The Corner (Figuring Out Money, `youtube`)
- 2026-08-13 — MacroVoices #545 Michael Howell: Warsh vs. The Markets (MacroVoices, `blog`)
- 2026-08-13 — Massive Money Printing Alert: Next Asset To 'Vertical Moonshot' \| Clem Chambers (David Lin, `youtube`)
- 2026-08-13 — When And How Does The Iran War End? Expert Reveals The Endgame \| Trita Parsi (David Lin, `youtube`)
- 2026-08-12 — These Markets To Double Next, Gold To $9,000: The Debasement Trade Is Exploding \| Jim Tho… (David Lin, `youtube`)
- 2026-08-11 — 全民AI 不如利民AI？ (徐家健, `master-insight`)
- 2026-08-11 — Christopher Whalen: The Fed Has Lost Control of Interest Rates (VRIC Media, `youtube`)
- 2026-08-11 — Michael Howell: Liquidity Has Turned, Low Quality Returns Ahead for Stocks, and Real Driv… (The Julia La Roche Show, `youtube`)
- 2026-08-10 — 羅奇心目中的舊香港逝不足惜 (施永青, `master-insight`)
- 2026-08-10 — 'The Supercycle Is Not Coming, It's Here'; Frank Giustra & Ian Harris On Ultimate 'Shorta… (David Lin, `youtube`)
