# Knowledge Base Wiki

> A synthesized, human-readable view of the investment-knowledge database — what analysts and channels are saying, distilled from extracted predictions and market views. Karpathy-style: dense links, honest about uncertainty.

## What this is

This wiki is generated directly from the `knowledge_base` Postgres database: every quote, target price, and stance below is pulled from an LLM extraction of a real scraped item (YouTube transcript, HKEJ/Master Insight column, Substack/Patreon post, or blog). Each claim cites its source item by title, date, channel, and external URL.

## State of the debate

What this corpus of commentators is arguing about, at least so far, is a very old argument wearing new clothes: what money is actually worth. The early extraction — just 228 items out of 31,932 source documents, carrying 420 explicit predictions and 556 market views from 53 named voices across 110 tickers — skews hard-money and macro rather than equity-analyst, and the asset leaderboard reflects it. Gold leads every instrument at 39 calls, with crude oil close behind at 33, the 10-year yield (^TNX) at 13, the dollar index at 9 and silver at 8. This is a debate about purchasing power, the dollar complex, and where to hide from both — not a stock-pickers' forum.

The roster of most-quoted names tells you where the fault lines sit. Chris Semenuk (68 calls) and Jeff Snider (61) set the pace on volume, with George Noble (43), Brent Johnson (42), Patrick Ceresna (37), aminvest (36), Jeffrey Christian (34) and David Woo (30) behind them. Put Johnson — best known for the Dollar Milkshake argument that the dollar strengthens violently against a weakening world — in the same lineup as Christian, whose career is gold and silver research at CPM Group, and Snider, the Eurodollar-system skeptic who has long argued the global monetary plumbing is structurally broken, and the corpus's central axis comes pre-loaded: dollar strength versus hard assets, with the long end of the Treasury curve as the scoreboard. Whether the extracted calls actually line up along that axis — who turned bullish or bearish on what, and when — is not yet visible in the summary statistics, so treat that framing as a reading of the lineup rather than the ledger.

The more interesting signal is what sits just below the top of the board. SpaceX, a private company, draws 14 calls — more than the S&P 500 (13) or Bitcoin (12) — which says something about where conviction lives in this crowd: a meaningful slice of it is willing to attach named calls to an asset that doesn't trade publicly. The theme tags run wide, from uranium and nuclear to China property, robotics and autonomy, private credit and BDCs, electrification and agriculture, but each of those is thin so far; the weight of the argument rests squarely on Precious Metals, Oil & Energy, and Rates, Bonds & the Dollar, with AI & Semiconductors and the broad macro indices as the secondary battleground.

Coverage honesty requires saying plainly: this summary rests on well under one percent of the archive, and the extracted statistics count calls without yet ranking them by direction, timing or conviction. What can be said with confidence today is where the volume concentrates — gold, oil and the dollar complex — and who is generating it, with Semenuk and Snider the most prolific callers by a wide margin. The strongest conviction calls by stance, the reversals, and whether the dollar-versus-gold divide genuinely maps onto these particular voices are precisely what the remaining 31,700-odd items should settle as extraction proceeds.

## Database at a glance

| | |
|---|---|
| Total items ingested | **31,932** |
| Items extracted (LLM) | **228** (of 31,932; 31,701 pending) |
| Predictions extracted | **420** |
| Market views extracted | **556** |
| Distinct tickers with calls | **110** |
| People with pages | **53** |
| Channels (analysts) | **77** |
| Published-date range | **2004-05-06 → 2026-08-13** |

### Sources (by item volume)

| Source | Kind | Items | Extracted |
|---|---|---:|---:|
| YouTube (`youtube`) | youtube | 20,259 | 126 |
| Hong Kong Economic Journal (`hkej`) | newspaper | 3,162 | 33 |
| Master Insight (`master-insight`) | newspaper | 2,804 | 27 |
| Patreon (`patreon`) | membership | 2,348 | 23 |
| Blogs (`blog`) | blog | 1,672 | 12 |
| Yahoo Finance Hong Kong (`yahoohk`) | newspaper | 1,407 | 0 |
| Substack (`substack`) | membership | 280 | 7 |

### Languages represented
`en` (18,548), `zh-Hant-HK` (4,211), `en-US` (3,882), `zh-Hant` (3,168), `yue` (1,102), `zh-TW` (559), `vi` (261), `th` (114)

## How to read this wiki

- **[People/](People)** — one page per *person*: interview guests, show hosts and solo authors, merged across every show they appear on. Each page tracks their opinions per topic **over time** and flags where they changed their mind.
- **[Tickers/](Tickers)** — one page per asset with enough mentions. Consensus direction, conflict flags, the people on each side, a rates/bond-yield backdrop, and every source item.
- **[Themes/](Themes)** — cross-cutting theses inferred from the predictions (AI-semis, gold, oil, rates, electrification, …).
- **[Syntheses/](Syntheses)** — the multi-dimensional views: where the *same person* flipped stance, where *different people* disagree on the same topic, and a global timeline of calls.
- **[Analysts/](Analysts)** — one page per channel that has extracted content, with the people who appear on it.
- **[_Index](_Index)** — flat alphabetical index of every page.

## ⚠️ Important caveats — read before drawing conclusions

1. **Extraction coverage is very thin.** Only **228** of **31,932** ingested items have been LLM-extracted so far (0.71%). Everything below reflects that small slice — it is **not** a representative sample of the full corpus. Treat consensus counts as directional, not authoritative.
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
