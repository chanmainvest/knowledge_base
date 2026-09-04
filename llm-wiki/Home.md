# Knowledge Base Wiki

> A synthesized, human-readable view of the investment-knowledge database — what analysts and channels are saying, distilled from extracted predictions and market views. Karpathy-style: dense links, honest about uncertainty.

## What this is

This wiki is generated directly from the `knowledge_base` Postgres database: every quote, target price, and stance below is pulled from an LLM extraction of a real scraped item (YouTube transcript, HKEJ/Master Insight column, Substack/Patreon post, or blog). Each claim cites its source item by title, date, channel, and external URL.

## State of the debate

This corpus is a wide net with a small mesh: 932 items extracted out of 32,682 in the underlying feed — under 3% — yet that slice already yields 2,744 predictions and 3,243 market views from 162 people across 283 tickers. The honest caveat up front is that these rankings describe what the extraction captured, not everything these commentators said, and call counts measure attention rather than direction. With that said, the shape of the debate is unmistakable: this is a corpus obsessed with the monetary system itself.

Gold is the loudest instrument in the room. Gold futures (GC=F) drew 232 calls — roughly 50% more than the S&P 500's 156 and double crude oil's 114 — and paired with silver at 48 calls, the precious-metals theme towers over everything else. The people driving that volume fit the pattern: Patrick Ceresna leads the corpus with 115 calls, followed by eurodollar-system critic Jeff Snider at 72, Darius Dale at 63, and dollar-milkshake theorist Brent Johnson at 60, with veteran bear Komal Sri-Kumar close behind at 57 (his name appears in transcripts as "Sree Kumar," so searches may need both spellings). The dollar index (56 calls) and the yen (40) round out a cluster that maps almost perfectly onto the debasement-versus-strong-dollar axis — the central argument of this commentariat, even though the digest's counts alone can't tell you who's currently winning it.

The second tier tells you the debate is not purely monetary. Crude at 114 calls keeps energy firmly in the top three, while bitcoin at 94 calls confirms that crypto — and specifically stablecoins, per the theme list — is now a mainstream macro talking point rather than a niche. The 10-year yield (68 calls) anchors a rates-and-credit cluster that runs through an unusually deep set of themes: bonds, private credit and BDCs, and China property. Notably, AI & semiconductors appears as a full theme alongside electrification, power, uranium, and robotics, but the digest provides no per-asset call count for it — so the apparent centrality of the AI capex debate can't be quantified from these numbers, only flagged.

The roster itself reveals a skew worth knowing before you read further. A single-company voice — the Surge Copper CEO, at 66 calls — ranks fourth in the entire corpus, which strongly suggests the source programming over-samples resource-sector interviews. Combined with the gold dominance, that makes this a hard-asset-heavy feed; equity-bullish and growth-oriented voices like David Woo (44 calls) are present but carry less volume. The sharpest conviction beats, by sheer repetition, are Ceresna's macro calls and the gold/dollar complex generally. What the data cannot show — and this summary will not pretend otherwise — is whether that concentration reflects correctness or merely fashion.

## Database at a glance

| | |
|---|---|
| Total items ingested | **32,682** |
| Items extracted (LLM) | **932** (of 32,682; 31,694 pending) |
| Predictions extracted | **2,744** |
| Market views extracted | **3,243** |
| Distinct tickers with calls | **283** |
| People with pages | **162** |
| Channels (analysts) | **79** |
| Published-date range | **2004-05-06 → 2026-08-29** |

### Sources (by item volume)

| Source | Kind | Items | Extracted |
|---|---|---:|---:|
| YouTube (`youtube`) | youtube | 20,412 | 630 |
| Hong Kong Economic Journal (`hkej`) | newspaper | 3,184 | 70 |
| Master Insight (`master-insight`) | newspaper | 2,811 | 45 |
| Patreon (`patreon`) | membership | 2,380 | 75 |
| Blogs (`blog`) | blog | 1,873 | 73 |
| Yahoo Finance Hong Kong (`yahoohk`) | newspaper | 1,407 | 0 |
| BusinessFocus (`businessfocus`) | newspaper | 321 | 8 |
| Substack (`substack`) | membership | 294 | 31 |

### Languages represented
`en` (18,768), `zh-Hant-HK` (4,539), `en-US` (3,838), `zh-Hant` (3,190), `yue` (1,126), `zh-TW` (759), `vi` (261), `th` (114)

## How to read this wiki

- **[People/](People)** — one page per *person*: interview guests, show hosts and solo authors, merged across every show they appear on. Each page tracks their opinions per topic **over time** and flags where they changed their mind.
- **[Tickers/](Tickers)** — one page per asset with enough mentions. Consensus direction, conflict flags, the people on each side, a rates/bond-yield backdrop, and every source item.
- **[Themes/](Themes)** — cross-cutting theses inferred from the predictions (AI-semis, gold, oil, rates, electrification, …).
- **[Syntheses/](Syntheses)** — the multi-dimensional views: where the *same person* flipped stance, where *different people* disagree on the same topic, and a global timeline of calls.
- **[Weekly/](Weekly)** — one digest per Sunday→Saturday week: what dominated the discourse, where commentators disagreed, and who changed their mind that week.
- **[Studies/](Studies)** — deep single-subject dives, e.g. how one channel recycles its own material over years.
- **[Analysts/](Analysts)** — one page per channel that has extracted content, with the people who appear on it.
- **[_Index](_Index)** — flat alphabetical index of every page.

## ⚠️ Important caveats — read before drawing conclusions

1. **Extraction coverage is very thin.** Only **932** of **32,682** ingested items have been LLM-extracted so far (2.85%). Everything below reflects that small slice — it is **not** a representative sample of the full corpus. Treat consensus counts as directional, not authoritative.
2. **No scores yet.** Predictions in this DB carry `score` fields, but none have been evaluated against market prices (`n_scored=0`). There is no track record / hit-rate data to report — only stated calls.
3. **People bios are LLM-written** (from public knowledge + this DB's context) and can be wrong. Stances/timelines, by contrast, are strictly DB-derived from extracted quotes.
4. **Channel metadata is sparse.** Most channels have no bio/url in the DB; analyst pages say so rather than invent.
5. **Re-run to refresh.** After new scrapes/extraction, regenerate with `uv run python scripts/build_llm_wiki.py` (see [README](README)).

## Marquee pages

- [高天佑](People/person-010.md) — 10 appearance(s), 16 extracted call(s)
- [Jeff Snider](People/jeff-snider.md) — 9 appearance(s), 72 extracted call(s)
- [Patrick Ceresna](People/patrick-ceresna.md) — 6 appearance(s), 115 extracted call(s)
- [何啟聰](People/person-001.md) — 4 appearance(s), 24 extracted call(s)
- [梁天卓](People/person-006.md) — 4 appearance(s), 2 extracted call(s)
- [Tickers/GC=F](Tickers/GC=F.md) — 232 analyst mentions
- [Tickers/^GSPC](Tickers/^GSPC.md) — 156 analyst mentions
- [Tickers/CL=F](Tickers/CL=F.md) — 114 analyst mentions
- [Tickers/BTC-USD](Tickers/BTC-USD.md) — 94 analyst mentions
- [Tickers/^TNX](Tickers/^TNX.md) — 68 analyst mentions

## Recent weeks

- [Week of 2026-08-23](Weekly/2026-08-23.md)
- [Week of 2026-08-16](Weekly/2026-08-16.md)
- [Week of 2026-08-09](Weekly/2026-08-09.md)
- [Week of 2026-08-02](Weekly/2026-08-02.md)
- [Week of 2026-07-26](Weekly/2026-07-26.md)

## Recently extracted items

- 2026-08-29 — 美加8,723億貿易攤牌❗️美國點解只先用5%貨品（200億）作先頭部隊❓加拿大頂得住嗎❓｜29 Aug2026 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-29 — Nvidia Might Not Survive This... (George Gammon, `youtube`)
- 2026-08-29 — 交叉驗證⋯曾國衛下台極有別情⋯涉「諜報系統、港中、海關利益鏈」❓｜3 Feb2026《淺見回顧》 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-28 — 美股市況短評 (20260828) (AM Invest, `patreon`)
- 2026-08-28 — 「以AI 系統看清市場真實規律」實體講座 – 報名連結 (AM Invest, `patreon`)
- 2026-08-28 — Canada-US Trade Deal Falls Apart (The Plain Bagel, `youtube`)
- 2026-08-28 — Understand 98% of the Eurodollar System in 18 Minutes (Eurodollar University, `youtube`)
- 2026-08-28 — Market Bubble Trigger: 'Immediate Recession' Once This Happens Says Big Short's Steve Eis… (David Lin, `youtube`)
- 2026-08-28 — 準退休亂世點保本❓三大最壞情境、海外帳戶、第二居住地有冇必要⁉️｜Jan2026《淺見回顧》 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-28 — Heap-Leach Gold in Nevada, But Can They Build it on Schedule? \| Western Exploration CEO I… (Resource Talks, `youtube`)
- 2026-08-28 — 反貪腐怎蛻變為黑產業鏈⁉️香港銀行壞帳率響起中國金融風險❗️ 「下水道經濟」救中國⁉️ ｜28 Jul2026《淺見回顧》 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-28 — 獅城如天堂 卻也須「谷B」 (高天佑, `hkej`)
