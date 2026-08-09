# Knowledge Base Wiki

> A synthesized, human-readable view of the investment-knowledge database — what analysts and channels are saying, distilled from extracted predictions and market views. Karpathy-style: dense links, honest about uncertainty.

## What this is

This wiki is generated directly from the `knowledge_base` Postgres database: every quote, target price, and stance below is pulled from an LLM extraction (currently the `github` / Copilot CLI provider) of a real scraped item (YouTube transcript, HKEJ/Master Insight column, Substack/Patreon post, or blog). Each claim cites its source item by title, date, channel, and external URL.

## Database at a glance

| | |
|---|---|
| Total items ingested | **26,370** |
| Items extracted (LLM) | **74** (of 26,370; 26,295 pending) |
| Predictions extracted | **207** |
| Market views extracted | **474** |
| Distinct tickers with calls | **75** |
| Distinct speakers | **30** |
| Channels (analysts) | **76** |
| Published-date range | **2004-05-06 → 2026-08-02** |

### Sources (by item volume)

| Source | Kind | Items | Extracted |
|---|---|---:|---:|
| YouTube (`youtube`) | youtube | 13,206 | 31 |
| Hong Kong Economic Journal (`hkej`) | newspaper | 5,506 | 19 |
| Master Insight (`master-insight`) | newspaper | 2,796 | 11 |
| Patreon (`patreon`) | membership | 2,349 | 12 |
| Yahoo Finance Hong Kong (`yahoohk`) | newspaper | 1,407 | 0 |
| Blogs (`blog`) | blog | 826 | 1 |
| Substack (`substack`) | membership | 280 | 0 |

### Languages represented
`en` (11,881), `zh-Hant` (5,512), `zh-Hant-HK` (4,203), `en-US` (3,123), `yue` (1,040), `zh-TW` (557), `ko` (19), `vi` (13)

## How to read this wiki

- **[Tickers/](Tickers)** — one page per asset with enough analyst mentions. Consensus direction, conflict flags, notable quotes, and every source item.
- **[Analysts/](Analysts)** — one page per channel that has extracted content: who they are, what they cover, their stance distribution, recent calls.
- **[Themes/](Themes)** — cross-cutting theses inferred from the predictions (AI-semis, gold, oil, rates, electrification, etc.).
- **[_Index](_Index)** — flat alphabetical index of every page.

## ⚠️ Important caveats — read before drawing conclusions

1. **Extraction coverage is very thin.** Only **74** of **26,370** ingested items have been LLM-extracted so far (0.28%). Everything below reflects that small slice — it is **not** a representative sample of the full corpus. Treat consensus counts as directional, not authoritative.
2. **No scores yet.** Predictions in this DB carry `score` fields, but none have been evaluated against market prices (`n_scored=0`). There is no track record / hit-rate data to report — only stated calls.
3. **All extractions are from one provider** (`github`, Copilot CLI). The DB supports multi-provider comparison but only one has run.
4. **Channel metadata is sparse.** Most channels have no bio/url in the DB; analyst pages say so rather than invent.
5. **Re-run to refresh.** After new scrapes/extraction, regenerate with `uv run python scripts/build_llm_wiki.py` (see [README](README)).

## Marquee pages

- [Tickers/CL=F](Tickers/CL=F.md) — 23 analyst mentions
- [Tickers/GC=F](Tickers/GC=F.md) — 18 analyst mentions
- [Tickers/^TNX](Tickers/^TNX.md) — 7 analyst mentions
- [Tickers/SI=F](Tickers/SI=F.md) — 6 analyst mentions
- [Tickers/CAT](Tickers/CAT.md) — 5 analyst mentions

## Recently extracted items

- 2026-06-28 — 零售末日 京東迫爆 (徐家健, `master-insight`)
- 2026-06-27 — 藉文字分析 看《施政》內容演變 (梁天卓, `hkej`)
- 2026-06-26 — 美股市況短評 (20260626) (aminvest, `patreon`)
- 2026-06-26 — C朗玩風投 美斯愛磚頭 (高天佑, `hkej`)
- 2026-06-26 — 宏觀交易過五關 AI落場都輸錢 (李聲揚, `hkej`)
- 2026-06-26 — 海力士槓桿ETF威脅韓股 (何啟聰, `hkej`)
- 2026-06-25 — 拆解伊強弱底牌 對美博弈佔上風 (雷鼎鳴, `master-insight`)
- 2026-06-25 — 美股市況短評 (20260625) (aminvest, `patreon`)
- 2026-06-25 — 毒尿片觸發的消費信任危機 (胡孟青, `master-insight`)
- 2026-06-25 — AI敍事亟需「再驗證」 (高天佑, `hkej`)
- 2026-06-25 — 自駕合法化 有限度先導計劃可取 (梁天卓, `hkej`)
- 2026-06-24 — 美股市況短評 (20260624) (aminvest, `patreon`)
