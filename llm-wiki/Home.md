# Knowledge Base Wiki

> A synthesized, human-readable view of the investment-knowledge database — what analysts and channels are saying, distilled from extracted predictions and market views. Karpathy-style: dense links, honest about uncertainty.

## What this is

This wiki is generated directly from the `knowledge_base` Postgres database: every quote, target price, and stance below is pulled from an LLM extraction of a real scraped item (YouTube transcript, HKEJ/Master Insight column, Substack/Patreon post, or blog). Each claim cites its source item by title, date, channel, and external URL.

## State of the debate

This corpus is a call-heavy one: from 762 extracted items out of 32,132 catalogued — an honest ~2.4% sample of the full archive — the extraction produced 2,445 predictions against 2,192 broader market views from 191 people across 261 tickers. These commentators commit to falsifiable claims more often than they muse. And the attention is strikingly lopsided: the top eight tickers carry roughly 30% of all calls. That shortlist — gold (208), the S&P 500 (140), crude oil (103), bitcoin (83), the 10-year yield (59), the dollar index (48), silver (45), the yen (37) — is itself the best map of what this crowd argues about.

The defining fault line is gold versus the dollar-rates complex, and the asymmetry is the story. Gold alone draws 208 calls — nearly 50% more than the S&P 500, more than the dollar index and the 10-year yield combined, and roughly a silver-and-then-some doubling when you stack SI=F beside it. This is, by airtime, a hard-asset and debasement-leaning room. Yet the most-quoted voices complicate that tilt. Jeff Snider leads everyone at 72 calls, a man whose reputation rests on Eurodollar-system plumbing and dollar shortage dynamics, and Brent Johnson — 54 calls — is the house salesman for the strong-dollar Milkshake thesis. So the loudest single-asset conviction in the corpus (gold) sits in open tension with two of its five most-quoted people, whose known frameworks argue for dollar strength. That unresolved contradiction is the healthiest thing in the debate, and the wiki pages for each analyst are where you'd adjudicate it — the digest counts calls, not directions, so who's actually long what can't be settled here.

The equity and growth debate runs on a second track. The S&P's 140 calls, plus dedicated themes in AI & Semiconductors and Robotics, show the bull/bear argument over the AI trade is fully alive — with veteran growth manager George Noble (43 calls) among the most-quoted participants. Around it sits a substantial real-economy wing: oil at 103 calls, plus Uranium & Nuclear, Electrification, and Power & Industrials as stand-alone themes, and a Credit/Private Credit cluster suggesting an undercurrent of worry about where the cycle's debt sits. China appears twice as its own theater — Hong Kong equities and property separately — which is more billing than most macro corpora give it. Crypto's 83 bitcoin calls straddle the camps: debasement hedge to some, high-beta risk asset to others.

Two honesty notes. First, the byline list skews macro-podcast — Snider, Chris Semenuk (68 calls), Patrick Ceresna (61), Darius Dale (49), David Woo (44) — with Surge Copper CEO Leif (59) the outlier, a reminder that company interviews, not just pundits, feed this corpus; single-stock analysis is thin by design. Second, "strongest conviction" here means most airtime, not boldest direction — the digest carries no dated stances or flips, so treat the counts as a measure of where the argument's energy is, not who's right. By that measure, the energy is unmistakably on gold.

## Database at a glance

| | |
|---|---|
| Total items ingested | **32,132** |
| Items extracted (LLM) | **762** (of 32,132; 31,334 pending) |
| Predictions extracted | **2,445** |
| Market views extracted | **2,192** |
| Distinct tickers with calls | **261** |
| People with pages | **191** |
| Channels (analysts) | **77** |
| Published-date range | **2004-05-06 → 2026-08-27** |

### Sources (by item volume)

| Source | Kind | Items | Extracted |
|---|---|---:|---:|
| YouTube (`youtube`) | youtube | 20,389 | 529 |
| Hong Kong Economic Journal (`hkej`) | newspaper | 3,182 | 68 |
| Master Insight (`master-insight`) | newspaper | 2,811 | 45 |
| Patreon (`patreon`) | membership | 2,376 | 71 |
| Blogs (`blog`) | blog | 1,673 | 21 |
| Yahoo Finance Hong Kong (`yahoohk`) | newspaper | 1,407 | 0 |
| Substack (`substack`) | membership | 294 | 28 |

### Languages represented
`en` (18,748), `zh-Hant-HK` (4,218), `en-US` (3,838), `zh-Hant` (3,188), `yue` (1,119), `zh-TW` (559), `vi` (261), `th` (114)

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

1. **Extraction coverage is very thin.** Only **762** of **32,132** ingested items have been LLM-extracted so far (2.37%). Everything below reflects that small slice — it is **not** a representative sample of the full corpus. Treat consensus counts as directional, not authoritative.
2. **No scores yet.** Predictions in this DB carry `score` fields, but none have been evaluated against market prices (`n_scored=0`). There is no track record / hit-rate data to report — only stated calls.
3. **People bios are LLM-written** (from public knowledge + this DB's context) and can be wrong. Stances/timelines, by contrast, are strictly DB-derived from extracted quotes.
4. **Channel metadata is sparse.** Most channels have no bio/url in the DB; analyst pages say so rather than invent.
5. **Re-run to refresh.** After new scrapes/extraction, regenerate with `uv run python scripts/build_llm_wiki.py` (see [README](README)).

## Marquee pages

- [aminvest](People/aminvest.md) — 11 appearance(s), 36 extracted call(s)
- [高天佑](People/person-015.md) — 10 appearance(s), 16 extracted call(s)
- [Jeff Snider](People/jeff-snider.md) — 9 appearance(s), 72 extracted call(s)
- [何啟聰](People/person-002.md) — 4 appearance(s), 24 extracted call(s)
- [梁天卓](People/person-010.md) — 4 appearance(s), 2 extracted call(s)
- [Tickers/GC=F](Tickers/GC=F.md) — 208 analyst mentions
- [Tickers/^GSPC](Tickers/^GSPC.md) — 140 analyst mentions
- [Tickers/CL=F](Tickers/CL=F.md) — 103 analyst mentions
- [Tickers/BTC-USD](Tickers/BTC-USD.md) — 83 analyst mentions
- [Tickers/^TNX](Tickers/^TNX.md) — 59 analyst mentions

## Recent weeks

- [Week of 2026-08-23](Weekly/2026-08-23.md)
- [Week of 2026-08-16](Weekly/2026-08-16.md)
- [Week of 2026-08-09](Weekly/2026-08-09.md)
- [Week of 2026-08-02](Weekly/2026-08-02.md)
- [Week of 2026-07-26](Weekly/2026-07-26.md)

## Recently extracted items

- 2026-08-27 — 中美非零和博弈 勿陷二囚困局 (雷鼎鳴, `master-insight`)
- 2026-08-27 — AI資本的理性與亢奮 (胡孟青, `master-insight`)
- 2026-08-27 — 魯莽投機➡️掩蓋更大真相⋯霸菱銀行31年後惡夢又被一26歲交易員重演❓｜22 Jul2026《淺見回顧》 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-27 — 買樓要問阿爺 短炒不如長揸 (高天佑, `hkej`)
- 2026-08-27 — Druck Calls Out Bessent & Will Jackson Hole Derail The Debasement Trade? \| Weekly Roundup (Forward Guidance, `youtube`)
- 2026-08-27 — 《戰局背後93》美對伊第三次轉戰略⋯逼強國二選一⁉️\|27 Aug2026 (Dr Ng Ming Tak, Victor, `youtube`)
- 2026-08-27 — AI愈聰明 為何我們還未收工？ (梁天卓, `hkej`)
- 2026-08-27 — The Entire House of Cards Is About To Fall (US Treasury Is Terrified) (George Gammon, `youtube`)
- 2026-08-26 — 美股市況短評 (20260826) (AM Invest, `patreon`)
- 2026-08-26 — Vol Surfaces 101 (dampedspring, `substack`)
- 2026-08-26 — Why Rick Rule Is Buying Gold Now: Monetary Shock Incoming (David Lin, `youtube`)
- 2026-08-26 — 香港走了數十萬人，銀行存款為何反增⁉️存款多≠資金留下❗️｜香港仲有幾多成機會翻身❓｜26 Aug2026 (Dr Ng Ming Tak, Victor, `youtube`)
