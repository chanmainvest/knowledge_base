# Knowledge Base Wiki

> A synthesized, human-readable view of the investment-knowledge database — what analysts and channels are saying, distilled from extracted predictions and market views. Karpathy-style: dense links, honest about uncertainty.

## What this is

This wiki is generated directly from the `knowledge_base` Postgres database: every quote, target price, and stance below is pulled from an LLM extraction of a real scraped item (YouTube transcript, HKEJ/Master Insight column, Substack/Patreon post, or blog). Each claim cites its source item by title, date, channel, and external URL.

## State of the debate

This corpus is, first and foremost, a hard-money panel: gold is the single most-called asset at 42 tracked calls (GC=F), well ahead of oil at 34 (CL=F), and the Precious Metals theme — gold, silver, platinum and palladium — runs noticeably deeper than anything else in the extraction. The commentator roster reinforces that tilt. Jeffrey Christian, the longtime CPM Group metals analyst, carries 34 calls, and the top of the most-quoted list (Chris Semenuk at 68, Jeff Snider at 61, George Noble at 43) is dominated by voices known for macro-skeptical, gold-friendly commentary rather than index-hugging. The striking tell is the ratio: the S&P 500 (^GSPC) draws just 14 calls, a third of gold's count. This group argues about money more than it argues about earnings.

The second fault line is the dollar and rates, where the panel lines up into its most familiar opposing camps. The 10-year yield (^TNX, 13 calls) and the dollar index (DX-Y.NYB, 9 calls) are active tickers, and the two most prolific commentators on that axis — Brent Johnson (42 calls) and Jeff Snider (61 calls) — are publicly known for opposite conclusions, Johnson's Dollar Milkshake case for a violently stronger dollar and Snider's Eurodollar framework warning of monetary disorder beneath the surface. David Woo (30 calls), a career rates and FX strategist, and Patrick Ceresna (37) crowd the same territory, alongside a 36-call account posting under the handle "aminvest." One honest caveat: this summary digest contains counts, not individual call-level stances, so where each of these voices sits in this particular snapshot — and whether Snider and Johnson are still trading the same blows — has to be verified against the underlying predictions before this page declares a winner.

Around the edges, the periphery is more interesting than the center. SpaceX, a private company, draws as many calls as the S&P 500 itself (14), which tells you this panel prizes idiosyncratic stories over beta. Bitcoin shows up with 12 calls under a Crypto & Stablecoins theme, silver with 10, and the long tail of themes — Uranium & Nuclear, China Property & Real Estate, Robotics, Automation & Autonomy, Agriculture & Softs, Credit, Private Credit & BDCs — reads like a checklist of contrarian hunting grounds rather than consensus allocation. In short: a corpus arguing about monetary regime change and energy, with equities treated almost as an afterthought.

Coverage honesty matters here, so state it plainly: only 266 of 31,932 source items have been extracted so far — well under one percent — yielding 459 predictions and 588 market views across 53 people and 118 tickers. Everything above describes the shape of a partial slice, not a settled map, and this digest included no individual prediction citations, so the "strongest conviction calls" — who said what, when, and at what level — cannot yet be ranked without fabricating them. The asset and theme rankings are robust to that limitation at current coverage; the person-level verdicts are not, and should be treated as provisional until extraction widens.

## Database at a glance

| | |
|---|---|
| Total items ingested | **31,932** |
| Items extracted (LLM) | **266** (of 31,932; 31,661 pending) |
| Predictions extracted | **459** |
| Market views extracted | **588** |
| Distinct tickers with calls | **118** |
| People with pages | **53** |
| Channels (analysts) | **77** |
| Published-date range | **2004-05-06 → 2026-08-13** |

### Sources (by item volume)

| Source | Kind | Items | Extracted |
|---|---|---:|---:|
| YouTube (`youtube`) | youtube | 20,259 | 157 |
| Hong Kong Economic Journal (`hkej`) | newspaper | 3,162 | 35 |
| Master Insight (`master-insight`) | newspaper | 2,804 | 28 |
| Patreon (`patreon`) | membership | 2,348 | 26 |
| Blogs (`blog`) | blog | 1,672 | 12 |
| Yahoo Finance Hong Kong (`yahoohk`) | newspaper | 1,407 | 0 |
| Substack (`substack`) | membership | 280 | 8 |

### Languages represented
`en` (18,548), `zh-Hant-HK` (4,211), `en-US` (3,882), `zh-Hant` (3,168), `yue` (1,102), `zh-TW` (559), `vi` (261), `th` (114)

## How to read this wiki

- **[People/](People)** — one page per *person*: interview guests, show hosts and solo authors, merged across every show they appear on. Each page tracks their opinions per topic **over time** and flags where they changed their mind.
- **[Tickers/](Tickers)** — one page per asset with enough mentions. Consensus direction, conflict flags, the people on each side, a rates/bond-yield backdrop, and every source item.
- **[Themes/](Themes)** — cross-cutting theses inferred from the predictions (AI-semis, gold, oil, rates, electrification, …).
- **[Syntheses/](Syntheses)** — the multi-dimensional views: where the *same person* flipped stance, where *different people* disagree on the same topic, and a global timeline of calls.
- **[Studies/](Studies)** — deep single-subject dives, e.g. how one channel recycles its own material over years.
- **[Analysts/](Analysts)** — one page per channel that has extracted content, with the people who appear on it.
- **[_Index](_Index)** — flat alphabetical index of every page.

## ⚠️ Important caveats — read before drawing conclusions

1. **Extraction coverage is very thin.** Only **266** of **31,932** ingested items have been LLM-extracted so far (0.83%). Everything below reflects that small slice — it is **not** a representative sample of the full corpus. Treat consensus counts as directional, not authoritative.
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
- [Tickers/GC=F](Tickers/GC=F.md) — 42 analyst mentions
- [Tickers/CL=F](Tickers/CL=F.md) — 34 analyst mentions
- [Tickers/^GSPC](Tickers/^GSPC.md) — 14 analyst mentions
- [Tickers/spacex](Tickers/spacex.md) — 14 analyst mentions
- [Tickers/^TNX](Tickers/^TNX.md) — 13 analyst mentions

## Recently extracted items

- 2026-08-13 — 日軍國幽靈重現 保釣風雲再起？ (雷鼎鳴, `master-insight`)
- 2026-08-13 — 跨界算力的機遇 (胡孟青, `master-insight`)
- 2026-08-13 — A Big Move Is Right Around The Corner (Figuring Out Money, `youtube`)
- 2026-08-13 — When And How Does The Iran War End? Expert Reveals The Endgame \| Trita Parsi (David Lin, `youtube`)
- 2026-08-13 — MacroVoices #545 Michael Howell: Warsh vs. The Markets (MacroVoices, `blog`)
- 2026-08-13 — Massive Money Printing Alert: Next Asset To 'Vertical Moonshot' \| Clem Chambers (David Lin, `youtube`)
- 2026-08-12 — These Markets To Double Next, Gold To $9,000: The Debasement Trade Is Exploding \| Jim Tho… (David Lin, `youtube`)
- 2026-08-11 — 全民AI 不如利民AI？ (徐家健, `master-insight`)
- 2026-08-11 — Michael Howell: Liquidity Has Turned, Low Quality Returns Ahead for Stocks, and Real Driv… (The Julia La Roche Show, `youtube`)
- 2026-08-11 — Christopher Whalen: The Fed Has Lost Control of Interest Rates (VRIC Media, `youtube`)
- 2026-08-10 — 羅奇心目中的舊香港逝不足惜 (施永青, `master-insight`)
- 2026-08-10 — 'The Supercycle Is Not Coming, It's Here'; Frank Giustra & Ian Harris On Ultimate 'Shorta… (David Lin, `youtube`)
