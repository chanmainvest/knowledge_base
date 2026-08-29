# Knowledge Base Wiki

> A synthesized, human-readable view of the investment-knowledge database — what analysts and channels are saying, distilled from extracted predictions and market views. Karpathy-style: dense links, honest about uncertainty.

## What this is

This wiki is generated directly from the `knowledge_base` Postgres database: every quote, target price, and stance below is pulled from an LLM extraction of a real scraped item (YouTube transcript, HKEJ/Master Insight column, Substack/Patreon post, or blog). Each claim cites its source item by title, date, channel, and external URL.

## Database at a glance

| | |
|---|---|
| Total items ingested | **32,490** |
| Items extracted (LLM) | **855** (of 32,490; 31,595 pending) |
| Predictions extracted | **2,678** |
| Market views extracted | **2,984** |
| Distinct tickers with calls | **276** |
| People with pages | **156** |
| Channels (analysts) | **79** |
| Published-date range | **2004-05-06 → 2026-08-28** |

### Sources (by item volume)

| Source | Kind | Items | Extracted |
|---|---|---:|---:|
| YouTube (`youtube`) | youtube | 20,400 | 611 |
| Hong Kong Economic Journal (`hkej`) | newspaper | 3,184 | 70 |
| Master Insight (`master-insight`) | newspaper | 2,811 | 45 |
| Patreon (`patreon`) | membership | 2,379 | 74 |
| Blogs (`blog`) | blog | 1,694 | 25 |
| Yahoo Finance Hong Kong (`yahoohk`) | newspaper | 1,407 | 0 |
| BusinessFocus (`businessfocus`) | newspaper | 321 | 0 |
| Substack (`substack`) | membership | 294 | 30 |

### Languages represented
`en` (18,758), `zh-Hant-HK` (4,539), `en-US` (3,838), `zh-Hant` (3,190), `yue` (1,123), `zh-TW` (580), `vi` (261), `th` (114)

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

1. **Extraction coverage is very thin.** Only **855** of **32,490** ingested items have been LLM-extracted so far (2.63%). Everything below reflects that small slice — it is **not** a representative sample of the full corpus. Treat consensus counts as directional, not authoritative.
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
- [Tickers/GC=F](Tickers/GC=F.md) — 228 analyst mentions
- [Tickers/^GSPC](Tickers/^GSPC.md) — 155 analyst mentions
- [Tickers/CL=F](Tickers/CL=F.md) — 114 analyst mentions
- [Tickers/BTC-USD](Tickers/BTC-USD.md) — 93 analyst mentions
- [Tickers/^TNX](Tickers/^TNX.md) — 66 analyst mentions

## Recent weeks

- [Week of 2026-08-23](Weekly/2026-08-23.md)
- [Week of 2026-08-16](Weekly/2026-08-16.md)
- [Week of 2026-08-09](Weekly/2026-08-09.md)
- [Week of 2026-08-02](Weekly/2026-08-02.md)
- [Week of 2026-07-26](Weekly/2026-07-26.md)

## Recently extracted items

- 2026-08-28 — 「以AI 系統看清市場真實規律」實體講座 – 報名連結 (AM Invest, `patreon`)
- 2026-08-28 — 獅城如天堂 卻也須「谷B」 (高天佑, `hkej`)
- 2026-08-28 — 供保單6年投入二佰萬賺咗160萬 ⋯點解都有陷阱 ⁉️｜1 Aug 2026《淺見回顧》 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-28 — 反貪腐怎蛻變為黑產業鏈⁉️香港銀行壞帳率響起中國金融風險❗️ 「下水道經濟」救中國⁉️ ｜28 Jul2026《淺見回顧》 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-28 — 準退休亂世點保本❓三大最壞情境、海外帳戶、第二居住地有冇必要⁉️｜Jan2026《淺見回顧》 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-28 — 港應推行「特朗普賬戶」 (李聲揚, `hkej`)
- 2026-08-27 — 倉位快將再破頂，但不宜過急進攻 (AM Invest, `patreon`)
- 2026-08-27 — 美股市況短評 (20260827) (AM Invest, `patreon`)
- 2026-08-27 — 中美非零和博弈 勿陷二囚困局 (雷鼎鳴, `master-insight`)
- 2026-08-27 — AI資本的理性與亢奮 (胡孟青, `master-insight`)
- 2026-08-27 — 美債突破40萬億＝鎖死華府❓打伊朗仲有幾多選項｜點解美國仍然唔會爆煲❓｜29 Aug2026 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-27 — 買樓要問阿爺 短炒不如長揸 (高天佑, `hkej`)
