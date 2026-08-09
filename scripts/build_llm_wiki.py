#!/usr/bin/env python
"""Build the Karpathy-style `llm-wiki/` from the knowledge database.

Reads items + predictions + market views + channels + sources from Postgres
and renders a human-readable, densely cross-linked Markdown wiki under
`llm-wiki/`. READ-ONLY against the DB; writes only to `llm-wiki/`.

Idempotent: re-running regenerates the whole tree (clears it first).

    uv run python scripts/build_llm_wiki.py

The wiki reflects DB state at generation time. Re-run after new scrapes /
extraction batches to refresh.
"""
from __future__ import annotations

import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

# Make `kb` importable when run as a script from the repo root.
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from kb.db import engine  # noqa: E402

WIKI_DIR = REPO / "llm-wiki"

# --- thresholds (keep pages meaningful; thin data gets noted, not padded) -----
MIN_TICKER_MENTIONS = 2      # a ticker needs >=2 prediction rows for its own page
MIN_ANALYST_ITEMS = 1        # any channel with an extracted item gets a page
MAX_QUOTE_LEN = 220          # truncate long quotes in summaries


# ---------------------------------------------------------------------------
# Theme taxonomy. A theme groups tickers + market views whose asset name/class
# matches any keyword. This is deliberately a small, hand-curated set derived
# from the asset_class/asset_name spread observed in the DB (precious metals,
# oil/energy, rates, AI/semis, broad macro indices, crypto, electrification/
# industrials, HK/China equities). Buckets are keyword-based so the mapping
# survives minor LLM wording variance.
# ---------------------------------------------------------------------------
THEMES: list[dict[str, Any]] = [
    {
        "slug": "precious-metals",
        "title": "Precious Metals (Gold, Silver, Platinum, Palladium)",
        "blurb": (
            "Calls on the four precious metals — dominated by gold (GC=F), "
            "with silver (SI=F), platinum (PL=F) and palladium (PA=F). "
            "Gold is the single most-predicted asset in the DB."
        ),
        # "gold" is whole-word matched (so "Goldman Sachs" does not match);
        # see _theme_match.
        "keywords": ["gold", "silver", "platinum", "palladium",
                     "precious metal",
                     "GC=F", "SI=F", "PL=F", "PA=F"],
    },
    {
        "slug": "oil-energy",
        "title": "Oil & Energy",
        "blurb": (
            "Crude oil (CL=F, RB=F), the Strategic Petroleum Reserve, and "
            "broader energy-sector views. Often tied to geopolitics "
            "(Strait of Hormuz, Iran)."
        ),
        "keywords": ["crude oil", "wti", "brent", "oil price", "oil",
                     "CL=F", "RB=F", "strategic petroleum", "energy"],
    },
    {
        "slug": "rates-bonds",
        "title": "Rates, Bonds & the Dollar",
        "blurb": (
            "US Treasury yields (^TNX), the dollar index (DX-Y.NYB), and "
            "broad fixed-income / monetary-policy calls. Rates cuts and "
            "the dollar's reserve status are the recurring threads."
        ),
        "keywords": ["treasury", "10-year", "rates", "bond", "fixed income",
                     "interest rate", "dollar", "dixie", "^TNX", "DX-Y.NYB",
                     "monetary policy", "yen", "JPY=X", "USDJPY"],
    },
    {
        "slug": "ai-semiconductors",
        "title": "AI & Semiconductors",
        "blurb": (
            "The AI / chip-stack trade: Nvidia (NVDA), Micron (MU), the "
            "semiconductor ETF (SMH), and adjacent AI-infrastructure names. "
            "Views split between AI-bubble skeptics and compute bulls."
        ),
        "keywords": ["nvidia", "semiconductor", "ai ", "a.i.", "chip", "micron",
                     "compute", "NVDA", "MU", "MRVL", "SMH", "TER",
                     "openai", "microsoft"],
    },
    {
        "slug": "macro-indices",
        "title": "Broad Market & Macro Indices",
        "blurb": (
            "Top-down calls on the S&P 500 (^GSPC, SPY), Nasdaq (^IXIC), "
            "Kospi (^KS11) and macro / economy direction. The "
            "recession-vs-soft-landing debate lives here."
        ),
        "keywords": ["s&p", "sp500", "s&p500", "nasdaq", "^GSPC", "SPY",
                     "^IXIC", "^KS11", "macro", "economy", "gdp",
                     "recession", "depression", "index"],
    },
    {
        "slug": "crypto",
        "title": "Crypto & Stablecoins",
        "blurb": (
            "Bitcoin (BTC-USD) and stablecoin / dollar-peg views. "
            "Crosses into the dedollarization and dollar-theme threads."
        ),
        "keywords": ["bitcoin", "crypto", "stablecoin", "btc", "BTC-USD",
                     "ethereum"],
    },
    {
        "slug": "electrification-industrials",
        "title": "Electrification, Power & Industrials",
        "blurb": (
            "The AI-driven power and electrification build-out: utilities "
            "(NEE, VST, GEV), grid / transformer names (PH, ETN, TKR, POWL, "
            "PWR, PRIM), and the data-center power trade."
        ),
        "keywords": ["utility", "utilities", "power", "electrif",
                     "transformer", "grid", "industrial", "data center",
                     "NEE", "VST", "GEV", "PH", "ETN", "TKR", "POWL", "PWR",
                     "PRIM", "Eaton", "Constellation"],
    },
    {
        "slug": "china-hk-equities",
        "title": "China & Hong Kong Equities",
        "blurb": (
            "Hong Kong / China names and the local macro backdrop — "
            "primarily surfaced from the Chinese-language HKEJ / Master "
            "Insight / Yahoo HK columnists."
        ),
        "keywords": ["hk", "hong kong", "china", "tencent", "alibaba",
                     "jd.com", "京東", "騰訊", "阿里", "港股", "a股",
                     "0700", "海力士"],
    },
    {
        "slug": "uranium-nuclear",
        "title": "Uranium & Nuclear",
        "blurb": (
            "The nuclear renaissance: uranium equities, SMR / OKLO and "
            "data-center baseload power."
        ),
        "keywords": ["uranium", "nuclear", "smr", "oklo", "ccj"],
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(s: str) -> str:
    """Filesystem + URL-safe slug. Keeps CJK characters (removed by some
    slugifiers) by falling back to pinyin-free transliteration: CJK runs are
    dropped, and the remaining ascii is cleaned. For pure-CJK strings the
    caller should pass an explicit latin slug instead."""
    s = (s or "").strip().lower()
    s = re.sub(r"[\s/\\?%*:|\"<>]+", "-", s)
    s = re.sub(r"[^a-z0-9._-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


def stance(action: str | None, direction: str | None) -> str:
    """Classify a quote's stance, mirroring api/main.py `_stance`."""
    a = (action or "").strip().lower()
    d = (direction or "").strip().lower()
    bullish_actions = {"buy", "long", "cover"}
    bearish_actions = {"sell", "short", "avoid"}
    if a in bullish_actions or d == "up":
        return "bullish"
    if a in bearish_actions or d == "down":
        return "bearish"
    return "neutral"


def truncate(s: str | None, n: int = MAX_QUOTE_LEN) -> str:
    if not s:
        return ""
    s = s.strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def fmt_date(dt: Any) -> str:
    if not dt:
        return "n/d"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt[:10]
    return dt.strftime("%Y-%m-%d")


def md_escape(s: str | None) -> str:
    """Escape pipe chars so text is safe inside markdown tables."""
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def rel_link(path: Path, target_page: str, label: str) -> str:
    """Relative markdown link from `path` (a file) to `target_page` (a wiki
    page name relative to llm-wiki root, without .md)."""
    # number of ../ needed = depth of the file's directory under llm-wiki/
    depth = len(path.parent.relative_to(WIKI_DIR).parts)
    prefix = "../" * depth if depth else ""
    return f"[{label}]({prefix}{target_page}.md)"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def fetch_all() -> dict[str, Any]:
    """Pull everything the wiki needs in one connection."""
    with engine().connect() as c:
        # --- sources + counts ------------------------------------------------
        sources = [dict(r) for r in c.execute(text("""
            SELECT s.code, s.name, s.kind,
                   COUNT(i.id) AS n_items,
                   COUNT(i.id) FILTER (WHERE i.extraction_status='done') AS n_extracted,
                   COUNT(i.id) FILTER (WHERE i.extraction_status='pending') AS n_pending
            FROM source s LEFT JOIN item i ON i.source_id=s.id
            GROUP BY s.id ORDER BY n_items DESC
        """)).mappings()]

        # --- global totals ---------------------------------------------------
        totals = dict(c.execute(text("""
            SELECT COUNT(*) AS n_items,
                   COUNT(*) FILTER (WHERE extraction_status='done') AS n_extracted,
                   COUNT(*) FILTER (WHERE extraction_status='pending') AS n_pending,
                   COUNT(*) FILTER (WHERE extraction_status='error') AS n_error,
                   MIN(published_at) AS first_date,
                   MAX(published_at) AS last_date
            FROM item
        """)).mappings().first())
        n_preds = c.execute(text("SELECT COUNT(*) FROM prediction")).scalar()
        n_views = c.execute(text("SELECT COUNT(*) FROM view_market")).scalar()
        n_channels = c.execute(text("SELECT COUNT(*) FROM channel")).scalar()
        n_tickers = c.execute(text(
            "SELECT COUNT(DISTINCT ticker) FROM prediction WHERE ticker IS NOT NULL"
        )).scalar()
        n_speakers = c.execute(text(
            "SELECT COUNT(DISTINCT speaker) FROM prediction WHERE speaker IS NOT NULL"
        )).scalar()
        # language distribution (top 8)
        langs = [dict(r) for r in c.execute(text("""
            SELECT COALESCE(language,'(unknown)') AS lang, COUNT(*) AS n
            FROM item GROUP BY language ORDER BY n DESC LIMIT 8
        """)).mappings()]

        # --- predictions (flat, joined to item + channel) --------------------
        preds = [dict(r) for r in c.execute(text("""
            SELECT p.id, p.ticker, p.asset_name, p.speaker, p.action,
                   p.direction, p.target_price, p.stop_price, p.timeframe,
                   p.made_at, p.quote,
                   i.id AS item_id, i.title AS item_title, i.url AS item_url,
                   i.external_id, i.published_at, i.language,
                   c.handle AS channel_handle, c.name AS channel_name,
                   c.id AS channel_id, s.code AS source
            FROM prediction p
            JOIN item i ON i.id=p.item_id
            LEFT JOIN channel c ON c.id=i.channel_id
            LEFT JOIN source s ON s.id=c.source_id
            ORDER BY p.made_at DESC NULLS LAST, p.id DESC
        """)).mappings()]

        # --- market views ----------------------------------------------------
        views = [dict(r) for r in c.execute(text("""
            SELECT v.id, v.speaker, v.asset_class, v.region, v.direction,
                   v.horizon, v.confidence, v.rationale, v.quote,
                   i.id AS item_id, i.title AS item_title, i.url AS item_url,
                   i.published_at,
                   c.handle AS channel_handle, c.name AS channel_name,
                   c.id AS channel_id
            FROM view_market v
            JOIN item i ON i.id=v.item_id
            LEFT JOIN channel c ON c.id=i.channel_id
            ORDER BY i.published_at DESC NULLS LAST
        """)).mappings()]

        # --- channels with at least one extracted item (analyst pages) -------
        analysts = [dict(r) for r in c.execute(text("""
            SELECT c.id AS channel_id, c.handle AS channel_handle, c.name AS channel_name,
                   c.url, c.metadata, s.code AS source,
                   COUNT(i.id) AS n_extracted,
                   COUNT(DISTINCT i.id) FILTER (
                       WHERE EXISTS (SELECT 1 FROM prediction p
                                     WHERE p.item_id=i.id)) AS n_with_preds
            FROM channel c JOIN source s ON s.id=c.source_id
            JOIN item i ON i.channel_id=c.id
            WHERE i.extraction_status='done'
            GROUP BY c.id, c.handle, c.name, c.url, c.metadata, s.code
            ORDER BY n_extracted DESC, c.name
        """)).mappings()]

        # --- recent extracted items (for Home + analyst "recent calls") ------
        recent_items = [dict(r) for r in c.execute(text("""
            SELECT i.id, i.title, i.url, i.published_at, i.summary, i.language,
                   s.code AS source, c.handle AS channel_handle, c.name AS channel_name
            FROM item i JOIN source s ON s.id=i.source_id
            LEFT JOIN channel c ON c.id=i.channel_id
            WHERE i.extraction_status='done'
            ORDER BY i.published_at DESC NULLS LAST LIMIT 40
        """)).mappings()]

        # --- per-channel extracted item list (analyst link roll) -------------
        channel_items = defaultdict(list)
        for r in c.execute(text("""
            SELECT i.id, i.title, i.url, i.published_at, i.summary,
                   c.id AS channel_id
            FROM item i LEFT JOIN channel c ON c.id=i.channel_id
            WHERE i.extraction_status='done'
            ORDER BY i.published_at DESC NULLS LAST
        """)).mappings():
            channel_items[r["channel_id"]].append(dict(r))

    return {
        "sources": sources,
        "totals": totals,
        "n_preds": n_preds,
        "n_views": n_views,
        "n_channels": n_channels,
        "n_tickers": n_tickers,
        "n_speakers": n_speakers,
        "langs": langs,
        "preds": preds,
        "views": views,
        "analysts": analysts,
        "recent_items": recent_items,
        "channel_items": channel_items,
    }


# ---------------------------------------------------------------------------
# Derived groupings
# ---------------------------------------------------------------------------

def group_predictions_by_ticker(preds: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        tk = (p.get("ticker") or "").strip().upper()
        if tk:
            g[tk].append(p)
    return g


def _theme_match(hay: str, keywords: list[str]) -> bool:
    """True if any keyword matches the haystack. Keywords ending in a
    non-word char (e.g. 'gold ') are treated as substring matches; bare
    alphabetic keywords are matched as whole-word tokens (so 'gold' does
    not catch 'Goldman'). Tickers / codes are matched as substrings."""
    h = hay.lower()
    for kw in keywords:
        k = kw.lower()
        # codes/tickers contain non-alphanumerics or trailing space → substring
        if k[-1:] in " =-." or not k.isalpha():
            if k in h:
                return True
        else:
            # whole-word match for plain words
            if re.search(rf"\b{re.escape(k)}\b", h):
                return True
    return False


def ticker_themes(ticker: str, asset_name: str) -> list[str]:
    """Return theme slugs a ticker belongs to."""
    hay = f"{ticker} {asset_name or ''}"
    return [t["slug"] for t in THEMES if _theme_match(hay, t["keywords"])]


def views_for_theme(theme_slug: str) -> list[dict]:
    theme = next(t for t in THEMES if t["slug"] == theme_slug)
    out = []
    for v in DATA["views"]:
        hay = f"{v.get('asset_class','')} {v.get('region','')} {v.get('rationale','')}"
        if _theme_match(hay, theme["keywords"]):
            out.append(v)
    return out


def preds_for_theme(theme_slug: str) -> list[dict]:
    theme = next(t for t in THEMES if t["slug"] == theme_slug)
    out = []
    for p in DATA["preds"]:
        hay = f"{p.get('ticker','')} {p.get('asset_name','')}"
        if _theme_match(hay, theme["keywords"]):
            out.append(p)
    return out


# Shared data loaded once (module global so render helpers can reach it).
DATA: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Citation helpers
# ---------------------------------------------------------------------------

def item_citation(path: Path, p: dict, *, with_quote: bool = True) -> str:
    """One source-item citation line: '— quote' linked, with date + analyst.

    `path` is the file being rendered (for relative-link depth)."""
    title = (p.get("item_title") or p.get("title") or "untitled").strip()
    url = p.get("item_url") or p.get("url")
    date = fmt_date(p.get("published_at") or p.get("made_at"))
    channel = p.get("channel_name") or p.get("channel_handle") or "?"
    speaker = p.get("speaker")
    by = speaker if speaker and speaker != channel else channel
    label = title if len(title) <= 80 else title[:79] + "…"
    link = f"[{md_escape(label)}]({url})" if url else md_escape(label)
    cite = f"{link} — {date}, {md_escape(by)}"
    if with_quote and p.get("quote"):
        cite += f': "{truncate(p["quote"])}"'
    return cite


def channel_slug(a: dict) -> str:
    """Stable slug for an analyst page. Prefer latin handle, then latin name,
    then a channel-id fallback (for CJK-only handles/names that slugify to
    empty). The display name is always the real channel name; only the URL
    slug is latinized."""
    # Inline slug without the "untitled" fallback so we can detect emptiness.
    def _slug(s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"[\s/\\?%*:|\"<>]+", "-", s)
        s = re.sub(r"[^a-z0-9._-]", "", s)
        return re.sub(r"-+", "-", s).strip("-")
    handle = (a.get("channel_handle") or "").lstrip("@")
    sh = _slug(handle)
    if sh:
        return sh
    sn = _slug(a.get("channel_name") or "")
    if sn:
        return sn
    # CJK-only handle and name — fall back to a stable id-based slug so each
    # such channel still gets its own distinct page.
    return f"ch-{a.get('channel_id')}"


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def write_page(rel_path: str, body: str) -> Path:
    p = WIKI_DIR / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.strip() + "\n", encoding="utf-8")
    return p


def render_home(inv: dict) -> Path:
    t = inv["totals"]
    lines = [
        "# Knowledge Base Wiki",
        "",
        "> A synthesized, human-readable view of the investment-knowledge "
        "database — what analysts and channels are saying, distilled from "
        "extracted predictions and market views. Karpathy-style: dense links, "
        "honest about uncertainty.",
        "",
        "## What this is",
        "",
        "This wiki is generated directly from the `knowledge_base` Postgres "
        "database: every quote, target price, and stance below is pulled from "
        "an LLM extraction (currently the `github` / Copilot CLI provider) of "
        "a real scraped item (YouTube transcript, HKEJ/Master Insight column, "
        "Substack/Patreon post, or blog). Each claim cites its source item by "
        "title, date, channel, and external URL.",
        "",
        "## Database at a glance",
        "",
        "| | |",
        "|---|---|",
        f"| Total items ingested | **{t['n_items']:,}** |",
        f"| Items extracted (LLM) | **{t['n_extracted']:,}** "
        f"(of {t['n_items']:,}; {t['n_pending']:,} pending) |",
        f"| Predictions extracted | **{inv['n_preds']:,}** |",
        f"| Market views extracted | **{inv['n_views']:,}** |",
        f"| Distinct tickers with calls | **{inv['n_tickers']}** |",
        f"| Distinct speakers | **{inv['n_speakers']}** |",
        f"| Channels (analysts) | **{inv['n_channels']}** |",
        f"| Published-date range | **{fmt_date(t['first_date'])} → {fmt_date(t['last_date'])}** |",
        "",
        "### Sources (by item volume)",
        "",
        "| Source | Kind | Items | Extracted |",
        "|---|---|---:|---:|",
    ]
    for s in inv["sources"]:
        lines.append(
            f"| {md_escape(s['name'])} (`{s['code']}`) | {s['kind']} | "
            f"{s['n_items']:,} | {s['n_extracted']:,} |"
        )
    lines += [
        "",
        "### Languages represented",
        ", ".join(f"`{l['lang']}` ({l['n']:,})" for l in inv["langs"]),
        "",
        "## How to read this wiki",
        "",
        "- **[Tickers/](Tickers)** — one page per asset with enough analyst "
        "mentions. Consensus direction, conflict flags, notable quotes, and "
        "every source item.",
        "- **[Analysts/](Analysts)** — one page per channel that has extracted "
        "content: who they are, what they cover, their stance distribution, "
        "recent calls.",
        "- **[Themes/](Themes)** — cross-cutting theses inferred from the "
        "predictions (AI-semis, gold, oil, rates, electrification, etc.).",
        "- **[_Index](_Index)** — flat alphabetical index of every page.",
        "",
        "## ⚠️ Important caveats — read before drawing conclusions",
        "",
        f"1. **Extraction coverage is very thin.** Only **{t['n_extracted']:,}** "
        f"of **{t['n_items']:,}** ingested items have been LLM-extracted so far "
        f"({100*t['n_extracted']/max(t['n_items'],1):.2f}%). Everything below "
        "reflects that small slice — it is **not** a representative sample of "
        "the full corpus. Treat consensus counts as directional, not "
        "authoritative.",
        "2. **No scores yet.** Predictions in this DB carry `score` fields, "
        "but none have been evaluated against market prices (`n_scored=0`). "
        "There is no track record / hit-rate data to report — only stated "
        "calls.",
        "3. **All extractions are from one provider** (`github`, Copilot CLI). "
        "The DB supports multi-provider comparison but only one has run.",
        "4. **Channel metadata is sparse.** Most channels have no bio/url in "
        "the DB; analyst pages say so rather than invent.",
        "5. **Re-run to refresh.** After new scrapes/extraction, regenerate "
        "with `uv run python scripts/build_llm_wiki.py` (see "
        "[README](README)).",
        "",
        "## Marquee pages",
        "",
    ]
    # pick a few marquee ticker pages (most mentions)
    by_tk = group_predictions_by_ticker(inv["preds"])
    top_tk = sorted(by_tk.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]
    for tk, plist in top_tk:
        if len(plist) >= MIN_TICKER_MENTIONS:
            lines.append(f"- [Tickers/{tk}](Tickers/{tk}.md) — "
                         f"{len(plist)} analyst mentions")
    lines += [
        "",
        "## Recently extracted items",
        "",
    ]
    for it in inv["recent_items"][:12]:
        url = it.get("item_url") or ""
        title = (it.get("title") or "untitled").strip()
        label = title if len(title) <= 90 else title[:89] + "…"
        link = f"[{md_escape(label)}]({url})" if url else md_escape(label)
        ch = it.get("channel_name") or it.get("channel_handle") or "?"
        lines.append(f"- {fmt_date(it['published_at'])} — {link} "
                     f"({md_escape(ch)}, `{it.get('source')}`)")
    return write_page("Home.md", "\n".join(lines))


def render_ticker(ticker: str, plist: list[dict]) -> Path | None:
    if len(plist) < MIN_TICKER_MENTIONS:
        return None
    asset_name = next((p.get("asset_name") for p in plist if p.get("asset_name")), ticker)
    stances = [stance(p.get("action"), p.get("direction")) for p in plist]
    n_bull = sum(1 for s in stances if s == "bullish")
    n_bear = sum(1 for s in stances if s == "bearish")
    n_neut = sum(1 for s in stances if s == "neutral")
    conflict = n_bull > 0 and n_bear > 0
    if n_bull > n_bear:
        consensus = "bullish"
    elif n_bear > n_bull:
        consensus = "bearish"
    elif n_bull == n_bear and n_bull > 0:
        consensus = "mixed (conflict)"
    else:
        consensus = "neutral / watch"

    themes = sorted(set(ticker_themes(ticker, asset_name)))
    channels = sorted({(p.get("channel_handle"), p.get("channel_name"))
                       for p in plist if p.get("channel_handle")})

    rel = f"Tickers/{ticker}.md"
    path = WIKI_DIR / rel
    lines = [
        f"# {ticker} — {md_escape(asset_name)}",
        "",
        f"**{len(plist)} extracted prediction(s)** across "
        f"{len(channels)} channel(s). Consensus: **{consensus}** "
        f"({n_bull} bullish / {n_bear} bearish / {n_neut} neutral)."
        + (" ⚠️ **Conflict flag**: analysts disagree on direction." if conflict else ""),
        "",
        "## Themes",
        "",
    ]
    if themes:
        for slug in themes:
            theme = next(t for t in THEMES if t["slug"] == slug)
            lines.append(f"- {rel_link(path, f'Themes/{slug}', theme['title'])}")
    else:
        lines.append("_Not bucketed into any theme (single-name / idiosyncratic)._")
    lines += [
        "",
        "## Stance breakdown",
        "",
        "| Stance | Count |",
        "|---|---:|",
        f"| Bullish | {n_bull} |",
        f"| Bearish | {n_bear} |",
        f"| Neutral / watch | {n_neut} |",
        "",
        "## Notable calls",
        "",
    ]
    # sort newest first; plist already sorted desc by made_at from the query
    for p in plist:
        sp = p.get("speaker") or p.get("channel_name") or "?"
        meta_bits = []
        if p.get("action"):
            meta_bits.append(f"action=`{p['action']}`")
        if p.get("direction"):
            meta_bits.append(f"dir=`{p['direction']}`")
        if p.get("target_price"):
            meta_bits.append(f"target=`{p['target_price']}`")
        if p.get("timeframe"):
            meta_bits.append(f"tf=`{p['timeframe']}`")
        meta = (" (" + ", ".join(meta_bits) + ")") if meta_bits else ""
        lines.append(f"- **{md_escape(sp)}**{meta} — {item_citation(path, p)}")
    lines += [
        "",
        "## Analysts covering this ticker",
        "",
    ]
    for handle, name in channels:
        a = next((x for x in DATA["analysts"]
                  if x["channel_handle"] == handle), None)
        if a:
            slug = channel_slug(a)
            lines.append(f"- {rel_link(path, f'Analysts/{slug}', name or handle)} (`{handle}`)")
        else:
            lines.append(f"- {md_escape(name)} (`{handle}`)")
    lines += [
        "",
        "## Source items",
        "",
    ]
    seen_items = set()
    for p in plist:
        iid = p.get("item_id")
        if iid in seen_items:
            continue
        seen_items.add(iid)
        title = (p.get("item_title") or "untitled").strip()
        url = p.get("item_url")
        label = title if len(title) <= 90 else title[:89] + "…"
        link = f"[{md_escape(label)}]({url})" if url else md_escape(label)
        lines.append(f"- {fmt_date(p.get('published_at'))} — {link} "
                     f"({md_escape(p.get('channel_name') or p.get('channel_handle') or '?')})")
    lines += [
        "",
        "---",
        f"_Page reflects DB state at generation time. Regenerate via "
        "`uv run python scripts/build_llm_wiki.py`._",
    ]
    return write_page(rel, "\n".join(lines))


def render_analyst(a: dict) -> Path:
    cid = a["channel_id"]
    handle = a.get("channel_handle") or "?"
    name = a.get("channel_name") or handle
    slug = channel_slug(a)
    rel = f"Analysts/{slug}.md"
    path = WIKI_DIR / rel

    preds = [p for p in DATA["preds"] if p.get("channel_handle") == handle]
    views = [v for v in DATA["views"] if v.get("channel_handle") == handle]
    items = DATA["channel_items"].get(cid, [])

    # coverage: tickers they call most
    tk_counter = Counter((p.get("ticker") for p in preds if p.get("ticker")))
    top_tickers = tk_counter.most_common(10)
    stances = [stance(p.get("action"), p.get("direction")) for p in preds]
    n_bull = sum(1 for s in stances if s == "bullish")
    n_bear = sum(1 for s in stances if s == "bearish")
    n_neut = sum(1 for s in stances if s == "neutral")

    # market-view stance (asset_class level)
    mv_dirs = Counter((v.get("direction") for v in views if v.get("direction")))

    lines = [
        f"# {md_escape(name)}",
        "",
        f"`{handle}` — source: `{a.get('source')}`  ·  "
        f"channel id: `{cid}`",
        "",
    ]
    url = a.get("url")
    if url:
        lines.append(f"**URL**: {url}")
    else:
        lines.append("_No URL / bio in DB channel metadata._")
    lines += [
        "",
        "## Coverage profile",
        "",
        f"- **Extracted items**: {a['n_extracted']}",
        f"- **Items with predictions**: {a.get('n_with_preds', 0)}",
        f"- **Predictions**: {len(preds)}",
        f"- **Market views**: {len(views)}",
        "",
    ]
    if top_tickers:
        lines += ["**Most-called tickers:**", ""]
        for tk, n in top_tickers:
            ticker_link = (rel_link(path, f"Tickers/{tk}", tk)
                           if any(p for p in DATA["preds"]
                                  if p.get("ticker") == tk
                                  and len([x for x in DATA["preds"]
                                           if x.get("ticker") == tk]) >= MIN_TICKER_MENTIONS)
                           else f"`{tk}`")
            lines.append(f"- {ticker_link} ({n})")
        lines.append("")
    if preds:
        lines += [
            "## Stance distribution (predictions)",
            "",
            "| Stance | Count |",
            "|---|---:|",
            f"| Bullish | {n_bull} |",
            f"| Bearish | {n_bear} |",
            f"| Neutral / watch | {n_neut} |",
            "",
        ]
    if views and sum(mv_dirs.values()):
        lines += [
            "## Market-view direction",
            "",
            "| Direction | Count |",
            "|---|---:|",
        ]
        for d, n in mv_dirs.most_common():
            lines.append(f"| {md_escape(d)} | {n} |")
        lines.append("")
    if preds:
        lines += ["## Recent notable calls", ""]
        for p in preds[:8]:
            tk = p.get("ticker") or "(no ticker)"
            tk_link = (rel_link(path, f"Tickers/{tk}", tk)
                       if tk in group_predictions_by_ticker(DATA["preds"])
                       and len(group_predictions_by_ticker(DATA["preds"])[tk]) >= MIN_TICKER_MENTIONS
                       else f"`{tk}`")
            lines.append(f"- {tk_link}: {item_citation(path, p)}")
        lines.append("")
    if views:
        lines += ["## Recent market views", ""]
        for v in views[:6]:
            ac = v.get("asset_class") or "?"
            d = v.get("direction") or "?"
            lines.append(f"- _{md_escape(ac)} — {md_escape(d)}_: "
                         f"{item_citation(path, v)}")
        lines.append("")
    if items:
        lines += [
            "## Source items (extracted)",
            "",
        ]
        for it in items[:25]:
            url = it.get("url")
            title = (it.get("title") or "untitled").strip()
            label = title if len(title) <= 90 else title[:89] + "…"
            link = f"[{md_escape(label)}]({url})" if url else md_escape(label)
            lines.append(f"- {fmt_date(it.get('published_at'))} — {link}")
        if len(items) > 25:
            lines.append(f"_…and {len(items)-25} more._")
        lines.append("")
    if not preds and not views:
        lines += [
            "_This channel has extracted items but no predictions or market "
            "views surfaced yet (the extraction may have found none, or the "
            "content is non-marketable commentary)._",
            "",
        ]
    lines += [
        "---",
        f"_Page reflects DB state at generation time._",
    ]
    return write_page(rel, "\n".join(lines))


def render_theme(theme: dict) -> Path:
    slug = theme["slug"]
    rel = f"Themes/{slug}.md"
    path = WIKI_DIR / rel
    preds = preds_for_theme(slug)
    views = views_for_theme(slug)

    # constituent tickers (from preds)
    tk_counter = Counter((p.get("ticker") for p in preds if p.get("ticker")))
    tickers = tk_counter.most_common()

    lines = [
        f"# Theme: {theme['title']}",
        "",
        theme["blurb"],
        "",
        f"**{len(preds)} prediction(s)** and **{len(views)} market view(s)** "
        f"match this theme in the current extraction.",
        "",
        "## Constituent tickers",
        "",
    ]
    if tickers:
        lines += ["| Ticker | Mentions |", "|---|---:|"]
        for tk, n in tickers:
            plist = group_predictions_by_ticker(DATA["preds"]).get(tk, [])
            if len(plist) >= MIN_TICKER_MENTIONS:
                link = rel_link(path, f"Tickers/{tk}", tk)
            else:
                link = f"`{tk}`"
            lines.append(f"| {link} | {n} |")
        lines.append("")
    else:
        lines += ["_No tickered predictions fall in this theme yet (signal "
                  "comes from market-view asset_class text only)._", ""]
    # consensus across predictions
    if preds:
        stances = [stance(p.get("action"), p.get("direction")) for p in preds]
        n_bull = sum(1 for s in stances if s == "bullish")
        n_bear = sum(1 for s in stances if s == "bearish")
        lines += [
            "## Consensus across analysts",
            "",
            f"Of {len(preds)} tickered calls: **{n_bull} bullish**, "
            f"**{n_bear} bearish**.",
            "",
        ]
    lines += ["## Notable calls & quotes", ""]
    seen = set()
    for p in preds[:12]:
        key = p.get("id")
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {item_citation(path, p)}")
    if views:
        lines += ["", "## Broad market views", ""]
        seen_v = set()
        for v in views[:10]:
            key = v.get("id")
            if key in seen_v:
                continue
            seen_v.add(key)
            ac = v.get("asset_class") or "?"
            d = v.get("direction") or "?"
            lines.append(f"- _{md_escape(ac)} ({md_escape(d)})_: "
                         f"{item_citation(path, v)}")
    lines += [
        "",
        "---",
        f"_Theme buckets are keyword-based and approximate; an LLM-tagged "
        "taxonomy would be more precise. Regenerate via "
        "`uv run python scripts/build_llm_wiki.py`._",
    ]
    return write_page(rel, "\n".join(lines))


def render_index(pages: dict[str, list[tuple[str, str]]]) -> Path:
    """pages[section] is a list of (slug, display_label)."""
    lines = [
        "# Index",
        "",
        "Every page in the wiki, alphabetical by display label within each "
        "section.",
        "",
        "## Tickers",
        "",
    ]
    for slug, label in sorted(pages.get("Tickers", []), key=lambda x: x[1].lower()):
        lines.append(f"- [{md_escape(label)}](Tickers/{slug}.md)")
    lines += ["", "## Analysts", ""]
    for slug, label in sorted(pages.get("Analysts", []), key=lambda x: x[1].lower()):
        lines.append(f"- [{md_escape(label)}](Analysts/{slug}.md)")
    lines += ["", "## Themes", ""]
    for slug, label in sorted(pages.get("Themes", []), key=lambda x: x[1].lower()):
        lines.append(f"- [{md_escape(label)}](Themes/{slug}.md)")
    lines += ["", "[← Back to Home](Home.md)", ""]
    return write_page("_Index.md", "\n".join(lines))


def render_readme(inv: dict, counts: dict[str, int]) -> Path:
    t = inv["totals"]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# llm-wiki",
        "",
        "A Karpathy-style synthesized wiki generated **from** the "
        "`knowledge_base` Postgres database. Not hand-written — every page is "
        "rendered by `scripts/build_llm_wiki.py` from the `item`, `prediction`, "
        "`view_market`, `channel`, and `source` tables.",
        "",
        "## Regenerate",
        "",
        "```bash",
        "# from the repo root, with the postgres container running",
        "uv run python scripts/build_llm_wiki.py",
        "```",
        "",
        "The script is **read-only** against the DB and only writes under "
        "`llm-wiki/`. It clears the directory first, so it is fully "
        "idempotent.",
        "",
        "## What it produces",
        "",
        "```",
        "llm-wiki/",
        "  Home.md             # overview + DB stats + caveats + marquee pages",
        "  _Index.md           # alphabetical index",
        "  README.md           # this file",
        f"  Tickers/   ({counts.get('Tickers',0)} pages)   # one per ticker >= {MIN_TICKER_MENTIONS} mentions",
        f"  Analysts/  ({counts.get('Analysts',0)} pages)   # one per channel with extracted items",
        f"  Themes/    ({counts.get('Themes',0)} pages)   # cross-cutting theses (gold, AI-semis, …)",
        "```",
        "",
        "## Data snapshot at generation time",
        "",
        f"- Generated: **{ts}**",
        f"- Items in DB: **{t['n_items']:,}** (extracted: **{t['n_extracted']:,}**, "
        f"pending: **{t['n_pending']:,}**)",
        f"- Predictions: **{inv['n_preds']:,}** · Market views: **{inv['n_views']:,}**",
        f"- Distinct tickers with calls: **{inv['n_tickers']}** · Speakers: **{inv['n_speakers']}**",
        f"- Published-date range: **{fmt_date(t['first_date'])} → {fmt_date(t['last_date'])}**",
        "",
        "## Important caveats",
        "",
        "1. **Coverage is thin.** The wiki reflects only the items the "
        "extraction pipeline has processed so far (a small fraction of the "
        "ingested corpus). It will get denser and more accurate as more items "
        "are extracted. **Re-run after each scrape/extraction batch.**",
        "2. **No performance scores.** Predictions carry `score` columns but "
        "none are evaluated yet (`n_scored=0`). There is no hit-rate / "
        "track-record data — only stated calls.",
        "3. **Single extraction provider.** All extractions currently come "
        "from the `github` (Copilot CLI) provider. The DB supports "
        "multi-provider comparison but only one has run.",
        "4. **Themes are keyword-bucketed**, not semantically clustered — "
        "approximate by design.",
        "5. **Quotes are LLM-extracted**, not curated. They can misattribute "
        "or trim. Always follow the source-item link to verify.",
        "",
        "## How claims are cited",
        "",
        "Every prediction/view cites its source item by:",
        "- the item **title** (linked to its external URL — YouTube watch "
        "link, HKEJ article, Substack post, etc.),",
        "- the **published date**,",
        "- the **channel/analyst** name,",
        "- and where relevant, the extracted **quote**.",
        "",
        "The `external_id` (e.g. a YouTube video id) is the item's stable key "
        "in the DB; the URL is its public location.",
        "",
        "## Related",
        "",
        "- [Home](Home.md)",
        "- [_Index](_Index.md)",
        "- Repo root `AGENTS.md` for the full pipeline architecture.",
    ]
    return write_page("README.md", "\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global DATA
    print("loading data from DB …")
    DATA = fetch_all()
    print(f"  {len(DATA['preds'])} predictions, {len(DATA['views'])} views, "
          f"{len(DATA['analysts'])} channels with extractions")

    # clear + recreate the wiki dir
    if WIKI_DIR.exists():
        shutil.rmtree(WIKI_DIR)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)

    pages: dict[str, list[tuple[str, str]]] = {
        "Tickers": [], "Analysts": [], "Themes": []}

    # --- Tickers ---
    by_tk = group_predictions_by_ticker(DATA["preds"])
    for tk, plist in sorted(by_tk.items()):
        p = render_ticker(tk, plist)
        if p:
            asset_name = next((pp.get("asset_name") for pp in plist
                               if pp.get("asset_name")), tk)
            pages["Tickers"].append((tk, f"{tk} — {asset_name}"))

    # --- Analysts ---
    for a in DATA["analysts"]:
        render_analyst(a)
        pages["Analysts"].append(
            (channel_slug(a), a.get("channel_name") or a.get("channel_handle") or "?"))

    # --- Themes ---
    for theme in THEMES:
        # only emit a theme page if it has any preds or views
        if preds_for_theme(theme["slug"]) or views_for_theme(theme["slug"]):
            render_theme(theme)
            pages["Themes"].append((theme["slug"], theme["title"]))

    # --- Home, Index, README ---
    render_home(DATA)
    render_index(pages)
    counts = {k: len(v) for k, v in pages.items()}
    render_readme(DATA, counts)

    print(f"\nwiki written to {WIKI_DIR}")
    print(f"  Tickers:  {counts['Tickers']}")
    print(f"  Analysts: {counts['Analysts']}")
    print(f"  Themes:   {counts['Themes']}")
    total = counts["Tickers"] + counts["Analysts"] + counts["Themes"] + 3
    print(f"  + Home.md, _Index.md, README.md  (total {total} pages)")


if __name__ == "__main__":
    main()
