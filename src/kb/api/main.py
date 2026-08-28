"""FastAPI app: search, items, predictions, leaderboard, market data."""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from ..config import ROOT, settings
from ..db import engine

app = FastAPI(title="KB API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)


def _list_filters(
    source: list[str] | None,
    channel_id: list[int] | None,
    date_from: str | None,
    date_to: str | None,
    has_predictions: str | None = None,
) -> tuple[list[str], dict[str, Any], list[str]]:
    """Build shared WHERE clauses/params for the item-list and search
    endpoints so both support multi-select sources/channels, a published_at
    date range, and a with/without-prediction-extraction filter identically.

    `has_predictions` filters on the item's canonical (primary) extraction
    run: 'true' keeps items with at least one extracted prediction there,
    'false' keeps items with none (including items never extracted); 'bull'
    / 'bear' keep items with at least one bullish/bearish call, classified
    the same way `_stance` does (action keywords, or direction free text
    containing bullish/bearish / up / down / higher / lower / positive /
    negative).

    Returns (clauses, params, expanding_param_names). Callers must call
    `.bindparams(bindparam(name, expanding=True))` for each name in the third
    element on their `text()` query.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    expanding: list[str] = []
    if source:
        clauses.append("s.code IN :sources")
        params["sources"] = list(source)
        expanding.append("sources")
    if channel_id:
        clauses.append("i.channel_id IN :channel_ids")
        params["channel_ids"] = list(channel_id)
        expanding.append("channel_ids")
    if date_from:
        clauses.append("i.published_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append("i.published_at < (CAST(:date_to AS date) + INTERVAL '1 day')")
        params["date_to"] = date_to
    if has_predictions is not None:
        # Enum comes from the query string; anything unknown is ignored so a
        # hand-edited URL can't inject SQL (values are only compared to a
        # fixed allowlist, never interpolated into the statement).
        if has_predictions in ("true", "false", "bull", "bear"):
            exists_sql = (
                "EXISTS (SELECT 1 FROM prediction p "
                "WHERE p.item_id=i.id AND p.extraction_run_id=i.primary_extraction_run_id"
            )
            if has_predictions == "bull":
                exists_sql += (
                    " AND (p.action IN ('buy','long','cover')"
                    " OR p.direction ILIKE '%bullish%'"
                    " OR p.direction IN ('up','higher','positive'))"
                )
            elif has_predictions == "bear":
                exists_sql += (
                    " AND (p.action IN ('sell','short','avoid')"
                    " OR p.direction ILIKE '%bearish%'"
                    " OR p.direction IN ('down','lower','negative'))"
                )
            exists_sql += ")"
            clauses.append(
                f"NOT {exists_sql}" if has_predictions == "false" else exists_sql
            )
    return clauses, params, expanding


# --- prediction consolidation ------------------------------------------------
#
# Predictions are extracted per-chunk by the LLM, so the same ticker can show
# up as several flat `prediction` rows for one item -- each with its own quote.
# The item-detail endpoint groups those rows into a single entry per ticker
# that exposes every quote, so the UI isn't littered with duplicates. Scoring
# and the leaderboard still read the underlying flat rows, so this is purely a
# read-time view; nothing in the DB changes.

_BULLISH_ACTIONS = {"buy", "long", "cover"}
_BEARISH_ACTIONS = {"sell", "short", "avoid"}


def _stance(action: str | None, direction: str | None) -> str:
    """Classify a single quote's directional stance.

    Returns 'bullish', 'bearish', or 'neutral'. A quote counts as bullish if
    its action or direction points up, bearish if either points down; hold /
    watch / flat / unspecified quotes are neutral and never cause a conflict.

    `prediction.direction` is LLM free text, not a clean enum — alongside the
    canonical 'up'/'down' it holds values like 'bullish (conditional)',
    'bearish_on_capex_sustainability' or 'higher', so match on the
    bullish/bearish keywords anywhere in the string (bearish checked first so
    a hedge like 'short-term bearish despite long position' reads bearish).
    """
    a = (action or "").strip().lower()
    d = (direction or "").strip().lower()
    if a in _BULLISH_ACTIONS:
        return "bullish"
    if a in _BEARISH_ACTIONS:
        return "bearish"
    if "bearish" in d or d in ("down", "lower", "negative"):
        return "bearish"
    if "bullish" in d or d in ("up", "higher", "positive"):
        return "bullish"
    return "neutral"


def _consolidate_predictions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group flat prediction rows for one item by ticker into one entry with
    a ``quotes[]`` array. Sets ``conflict`` when the same ticker has at least
    one bullish and one bearish quote, and ``direction`` to the consensus.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for r in rows:
        tk = (r.get("ticker") or "").strip().upper()
        # Rows without a ticker are each their own group (keyed on the row id)
        # so untickered predictions aren't all lumped together.
        key = tk or f"__noticker__:{r['id']}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    out: list[dict[str, Any]] = []
    for key in order:
        grp = groups[key]
        stances = [_stance(r.get("action"), r.get("direction")) for r in grp]
        has_bull = any(s == "bullish" for s in stances)
        has_bear = any(s == "bearish" for s in stances)
        if has_bull and has_bear:
            direction = "mixed"
            conflict = True
        elif has_bull:
            direction = "up"
            conflict = False
        elif has_bear:
            direction = "down"
            conflict = False
        else:
            direction = "neutral"
            conflict = False

        first = grp[0]
        out.append({
            "ticker": first.get("ticker"),
            "asset_name": first.get("asset_name"),
            "speaker": first.get("speaker"),
            "direction": direction,
            "conflict": conflict,
            "quotes": [{
                "id": r["id"],
                "action": r.get("action"),
                "direction": r.get("direction"),
                "target_price": r.get("target_price"),
                "stop_price": r.get("stop_price"),
                "timeframe": r.get("timeframe"),
                "quote": r.get("quote"),
                "score": r.get("score"),
                "made_at": r.get("made_at"),
            } for r in grp],
        })
    return out


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/sources")
def sources() -> list[dict[str, Any]]:
    with engine().connect() as c:
        rows = c.execute(text(
            "SELECT s.id, s.code, s.name, s.kind, COUNT(i.id) AS n_items "
            "FROM source s LEFT JOIN item i ON i.source_id=s.id "
            "GROUP BY s.id ORDER BY s.name"
        )).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    """Per-source pipeline progress (download → ingest → extract) and global
    totals.

    Counters are computed **live** from the `item` table (a single GROUP BY),
    so they are always authoritative and can never drift the way the
    incrementally-maintained `source_progress` cache did — `n_extract_pending`
    is exactly `n_ingested - n_extracted - n_extract_error` by construction.
    Two fields still need the cache/helpers: `n_downloaded` (from a disk scan,
    since not every downloaded file has an `item` row yet) and `last_scrape_at`
    (not derivable from items). `n_pending_download` (discovered but not yet
    downloaded) and `total_known` (upstream total where the source API exposes
    one) come from the discovery catalog helpers."""
    from .. import catalog
    from ..progress import count_downloaded_on_disk
    pending_dl = catalog.pending_counts()
    totals_known = catalog.known_totals()
    disk = count_downloaded_on_disk()
    with engine().connect() as c:
        rows = c.execute(text("""
            SELECT s.id, s.code, s.name, s.kind,
                   COUNT(i.id)                                                    AS n_ingested,
                   COUNT(i.id) FILTER (WHERE i.extraction_status = 'done')        AS n_extracted,
                   COUNT(i.id) FILTER (WHERE i.extraction_status = 'pending')     AS n_extract_pending,
                   COUNT(i.id) FILTER (WHERE i.extraction_status = 'error')       AS n_extract_error,
                   COUNT(i.id) FILTER (WHERE i.has_transcript = false)            AS n_no_transcript,
                   MAX(i.ingested_at)                                             AS last_ingest_at,
                   MAX(i.extracted_at)                                            AS last_extract_at,
                   sp.last_scrape_at
            FROM source s
            LEFT JOIN item i ON i.source_id = s.id
            LEFT JOIN source_progress sp ON sp.source_id = s.id
            GROUP BY s.id, s.code, s.name, s.kind, sp.last_scrape_at
            ORDER BY s.name
        """)).mappings().all()
        ptot = dict(c.execute(text("""
            SELECT COUNT(*) AS n_calls,
                   COUNT(p.score) AS n_scored,
                   COUNT(DISTINCT p.speaker) FILTER (
                       WHERE p.speaker IS NOT NULL AND p.speaker <> '') AS n_speakers
            FROM prediction p JOIN item i ON i.id = p.item_id
            WHERE p.extraction_run_id = i.primary_extraction_run_id
        """)).mappings().one())
    sources_list = []
    for r in rows:
        d = dict(r)
        # n_downloaded is the only count not derivable from the item table:
        # downloaded-but-not-yet-ingested files have no item row, and the disk
        # is the ground truth for what's been scraped.
        d["n_downloaded"] = disk.get(d["code"], 0)
        d["n_pending_download"] = pending_dl.get(d["code"], 0)
        d["total_known"] = totals_known.get(d["code"])
        sources_list.append(d)
    totals = {
        "n_downloaded":       sum(r["n_downloaded"] for r in sources_list),
        "n_ingested":         sum(r["n_ingested"] for r in sources_list),
        "n_extracted":        sum(r["n_extracted"] for r in sources_list),
        "n_extract_pending":  sum(r["n_extract_pending"] for r in sources_list),
        "n_extract_error":    sum(r["n_extract_error"] for r in sources_list),
        "n_pending_download": sum(r["n_pending_download"] for r in sources_list),
        "n_no_transcript":    sum(r["n_no_transcript"] for r in sources_list),
        "n_predictions":      ptot["n_calls"],
        "n_scored":           ptot["n_scored"],
        "n_speakers":         ptot["n_speakers"],
    }
    return {"sources": sources_list, "totals": totals}


@app.get("/api/channels")
def channels(source: list[str] | None = Query(None)) -> list[dict[str, Any]]:
    """Channels, optionally filtered to one or more source codes (multi-select).
    Includes per-channel prediction stats (canonical extraction run only, same
    convention as `has_predictions` elsewhere) so the Channels page can show
    them as sortable/filterable columns: `n_calls` (predictions extracted),
    `n_scored` (of those, evaluated against market prices), `avg_score` and
    `hit_rate` (see `leaderboard.py` for how scoring works)."""
    sql = ("SELECT c.id, c.handle, c.name, s.code AS source, "
           "COUNT(DISTINCT i.id) AS n_items, "
           "COUNT(p.id) AS n_calls, "
           "COUNT(p.score) AS n_scored, "
           "AVG(p.score) AS avg_score, "
           "AVG(CASE WHEN p.score>0 THEN 1.0 WHEN p.score<0 THEN 0.0 END) AS hit_rate "
           "FROM channel c JOIN source s ON s.id=c.source_id "
           "LEFT JOIN item i ON i.channel_id=c.id "
           "LEFT JOIN prediction p ON p.item_id=i.id "
           "AND p.extraction_run_id=i.primary_extraction_run_id ")
    params: dict[str, Any] = {}
    if source:
        sql += "WHERE s.code IN :sources "
        params["sources"] = list(source)
    sql += "GROUP BY c.id, s.code ORDER BY n_items DESC, c.name"
    stmt = text(sql)
    if source:
        stmt = stmt.bindparams(bindparam("sources", expanding=True))
    with engine().connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()
    return [dict(r) for r in rows]


MAX_PAGE_SIZE = 200


@app.get("/api/search")
def search(q: str | None = Query(None),
           source: list[str] | None = Query(None),
           channel_id: list[int] | None = Query(None),
           date_from: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
           date_to: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
           has_predictions: str | None = Query(
               None, description="true/false = with/without extracted "
                                  "predictions; bull/bear = at least one "
                                  "bullish/bearish call"),
           limit: int = 25,
           offset: int = 0) -> dict[str, Any]:
    """Search items by keyword (optional) with multi-select source/channel
    filters, a published_at date range, a with/without-prediction-extraction
    filter, and pagination. When `q` is omitted, results are just the latest
    items matching the filters (browse mode), so the search page can show
    recent posts by default."""
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    clauses, params, expanding = _list_filters(
        source, channel_id, date_from, date_to, has_predictions)

    has_q = bool(q and q.strip())
    if has_q:
        clauses.insert(0, "i.tsv @@ plainto_tsquery('simple', :q)")
        params["q"] = q
        order_sql = "rank DESC, i.published_at DESC NULLS LAST"
        select_extra = (
            "ts_headline('simple', i.content, plainto_tsquery('simple', :q), "
            "'MaxFragments=2,MinWords=8,MaxWords=30,ShortWord=2') AS snippet, "
            "ts_rank(i.tsv, plainto_tsquery('simple', :q)) AS rank"
        )
    else:
        order_sql = "i.published_at DESC NULLS LAST, i.id DESC"
        select_extra = "NULL AS snippet, NULL AS rank"

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    count_sql = text(f"SELECT COUNT(*) FROM item i JOIN source s ON s.id=i.source_id {where_sql}")
    list_sql = text(f"""
        SELECT i.id, i.title, i.url, i.published_at, i.summary,
               s.code AS source, c.handle AS channel, c.name AS channel_name,
               i.channel_id,
               EXISTS (SELECT 1 FROM prediction p
                       WHERE p.item_id=i.id AND p.extraction_run_id=i.primary_extraction_run_id
                      ) AS has_predictions,
               {select_extra}
        FROM item i
        JOIN source s ON s.id=i.source_id
        LEFT JOIN channel c ON c.id=i.channel_id
        {where_sql}
        ORDER BY {order_sql}
        LIMIT :lim OFFSET :off
    """)
    for name in expanding:
        count_sql = count_sql.bindparams(bindparam(name, expanding=True))
        list_sql = list_sql.bindparams(bindparam(name, expanding=True))
    with engine().connect() as conn:
        total = conn.execute(count_sql, params).scalar() or 0
        rows = conn.execute(list_sql, {**params, "lim": limit, "off": offset}).mappings().all()
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/items/{item_id}")
def get_item(item_id: int, run_id: int | None = None) -> dict[str, Any]:
    with engine().connect() as conn:
        row = conn.execute(text("""
            SELECT i.*, s.code AS source, c.handle AS channel, c.name AS channel_name
            FROM item i JOIN source s ON s.id=i.source_id
            LEFT JOIN channel c ON c.id=i.channel_id WHERE i.id=:i
        """), {"i": item_id}).mappings().first()
        if not row:
            raise HTTPException(404)
        item = dict(row)
        # Show market_views/predictions for an explicit ?run_id= (e.g. to
        # inspect a non-canonical provider's output) or, by default, the
        # item's primary (canonical) extraction run.
        effective_run_id = run_id if run_id is not None else item.get("primary_extraction_run_id")
        item["market_views"] = [dict(r) for r in conn.execute(text(
            "SELECT * FROM view_market WHERE item_id=:i AND extraction_run_id=:r"),
            {"i": item_id, "r": effective_run_id}).mappings()]
        item["predictions"] = _consolidate_predictions(
            [dict(r) for r in conn.execute(text(
                """SELECT id, speaker, ticker, asset_name, action,
                          target_price::float8 AS target_price,
                          stop_price::float8 AS stop_price,
                          direction, timeframe, quote, made_at,
                          price_at_call::float8 AS price_at_call,
                          price_at_eval::float8 AS price_at_eval,
                          eval_at, score
                   FROM prediction WHERE item_id=:i AND extraction_run_id=:r
                   ORDER BY id"""),
                {"i": item_id, "r": effective_run_id}).mappings()])
        item["extraction_runs"] = [dict(r) for r in conn.execute(text("""
            SELECT id, provider, model, status, duration_ms
            FROM extraction_run WHERE item_id=:i ORDER BY id DESC
        """), {"i": item_id}).mappings()]
        item["entities"] = [dict(r) for r in conn.execute(text("""
            SELECT e.id, e.kind, e.name, e.ticker, ie.weight
            FROM item_entity ie JOIN entity e ON e.id=ie.entity_id
            WHERE ie.item_id=:i ORDER BY ie.weight DESC, e.name
        """), {"i": item_id}).mappings()]
        item["related"] = [dict(r) for r in conn.execute(text("""
            SELECT i2.id, i2.title, i2.published_at, c.name AS channel_name,
                   l.similarity
            FROM item_link l JOIN item i2 ON i2.id=l.b_id
            LEFT JOIN channel c ON c.id=i2.channel_id
            WHERE l.a_id=:i ORDER BY l.similarity DESC LIMIT 10
        """), {"i": item_id}).mappings()]
    return item


@app.get("/api/items/{item_id}/runs")
def item_runs(item_id: int) -> list[dict[str, Any]]:
    """All extraction attempts for an item (one per provider/model/prompt
    version), so you can compare what each LLM extracted from the same
    article side by side."""
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT er.*, (er.id = i.primary_extraction_run_id) AS is_primary,
                   (SELECT COUNT(*) FROM view_market WHERE extraction_run_id = er.id) AS n_market_views,
                   (SELECT COUNT(*) FROM prediction WHERE extraction_run_id = er.id) AS n_predictions
            FROM extraction_run er JOIN item i ON i.id = er.item_id
            WHERE er.item_id = :i
            ORDER BY er.id DESC
        """), {"i": item_id}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/items")
def list_items(source: list[str] | None = Query(None),
               channel_id: list[int] | None = Query(None),
               date_from: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
               date_to: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
               has_predictions: str | None = Query(
                   None, description="true/false = with/without extracted "
                                      "predictions; bull/bear = at least one "
                                      "bullish/bearish call"),
               limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Latest items (no keyword search), with the same multi-select
    source/channel filters, date range, prediction-extraction filter, and
    pagination as /api/search."""
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    clauses, params, expanding = _list_filters(
        source, channel_id, date_from, date_to, has_predictions)
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    count_sql = text(f"SELECT COUNT(*) FROM item i JOIN source s ON s.id=i.source_id {where_sql}")
    list_sql = text(f"""
        SELECT i.id, i.title, i.url, i.published_at, i.summary,
               s.code AS source, c.handle AS channel, c.name AS channel_name,
               i.channel_id,
               EXISTS (SELECT 1 FROM prediction p
                       WHERE p.item_id=i.id AND p.extraction_run_id=i.primary_extraction_run_id
                      ) AS has_predictions
        FROM item i JOIN source s ON s.id=i.source_id
        LEFT JOIN channel c ON c.id=i.channel_id
        {where_sql}
        ORDER BY i.published_at DESC NULLS LAST, i.id DESC
        LIMIT :lim OFFSET :off
    """)
    for name in expanding:
        count_sql = count_sql.bindparams(bindparam(name, expanding=True))
        list_sql = list_sql.bindparams(bindparam(name, expanding=True))
    with engine().connect() as conn:
        total = conn.execute(count_sql, params).scalar() or 0
        rows = conn.execute(list_sql, {**params, "lim": limit, "off": offset}).mappings().all()
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/predictions")
def predictions(ticker: str | None = None,
                speaker: str | None = Query(
                    None, description="Substring match on speaker, case-insensitive"),
                channel_id: int | None = None,
                direction: str | None = Query(
                    None, description="up | down | flat | unspecified"),
                scored: bool | None = Query(
                    None, description="true = only rows evaluated against prices, "
                                      "false = only unscored"),
                date_from: str | None = Query(None, description="YYYY-MM-DD on made_at, inclusive"),
                date_to: str | None = Query(None, description="YYYY-MM-DD on made_at, inclusive"),
                order: str = Query("recent", description="recent | score_desc | score_asc"),
                all_runs: bool = Query(
                    False, description="Include non-primary extraction runs "
                                       "(provider-comparison rows) instead of just the "
                                       "canonical extraction"),
                limit: int = 50,
                offset: int = 0) -> dict[str, Any]:
    """Flat prediction rows with item/channel context. Defaults to the items'
    canonical (primary) extraction runs and newest-first ordering."""
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if not all_runs:
        clauses.append("p.extraction_run_id = i.primary_extraction_run_id")
    if ticker:
        clauses.append("p.ticker = :ticker")
        params["ticker"] = ticker.strip().upper()
    if speaker:
        clauses.append("p.speaker ILIKE :speaker_pat")
        params["speaker_pat"] = f"%{speaker.strip()}%"
    if channel_id is not None:
        clauses.append("i.channel_id = :cid")
        params["cid"] = channel_id
    if direction:
        clauses.append("p.direction = :dir")
        params["dir"] = direction
    if scored is not None:
        clauses.append("p.score IS NOT NULL" if scored else "p.score IS NULL")
    if date_from:
        clauses.append("p.made_at >= CAST(:date_from AS date)")
        params["date_from"] = date_from
    if date_to:
        clauses.append("p.made_at < (CAST(:date_to AS date) + INTERVAL '1 day')")
        params["date_to"] = date_to
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order_sql = {
        "recent": "p.made_at DESC NULLS LAST, p.id DESC",
        "score_desc": "p.score DESC NULLS LAST, p.made_at DESC NULLS LAST",
        "score_asc": "p.score ASC NULLS LAST, p.made_at DESC NULLS LAST",
    }.get(order, "p.made_at DESC NULLS LAST, p.id DESC")
    base = ("FROM prediction p JOIN item i ON i.id=p.item_id "
            "LEFT JOIN channel c ON c.id=i.channel_id ")
    with engine().connect() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) {base}{where_sql}"),
                             params).scalar() or 0
        rows = conn.execute(text(f"""
            SELECT p.id, p.item_id, p.extraction_run_id, p.speaker, p.ticker,
                   p.asset_name, p.action, p.direction,
                   p.target_price::float8 AS target_price,
                   p.stop_price::float8 AS stop_price,
                   p.timeframe, p.quote, p.made_at,
                   p.price_at_call::float8 AS price_at_call,
                   p.price_at_eval::float8 AS price_at_eval,
                   p.eval_at, p.score,
                   i.title AS item_title, i.url AS item_url,
                   c.handle AS channel, c.name AS channel_name
            {base}{where_sql}
            ORDER BY {order_sql}
            LIMIT :lim OFFSET :off
        """), {**params, "lim": limit, "off": offset}).mappings().all()
    return {"items": [dict(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


@app.get("/api/leaderboard")
def leaderboard(weeks: int = 12,
                date_from: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
                date_to: str | None = Query(None, description="YYYY-MM-DD, inclusive")) -> dict[str, Any]:
    """Weekly + overall channel scoring, plus the speaker (interviewee/
    author) leaderboard. By default `weeks` limits the weekly series to the
    last N weeks (the overall tables are all-time). Supplying `date_from`
    and/or `date_to` overrides the weeks window with an explicit inclusive
    range: applied to `leaderboard_weekly.week_start` for the weekly series
    and to `item.published_at` for the overall aggregate. All aggregates use
    the items' canonical (primary) extraction runs only."""
    has_range = bool(date_from or date_to)
    with engine().connect() as conn:
        if has_range:
            wclauses: list[str] = []
            wparams: dict[str, Any] = {}
            if date_from:
                wclauses.append("lw.week_start >= :date_from")
                wparams["date_from"] = date_from
            if date_to:
                wclauses.append("lw.week_start < (CAST(:date_to AS date) + INTERVAL '1 day')")
                wparams["date_to"] = date_to
            weekly = [dict(r) for r in conn.execute(text(
                "SELECT lw.channel_id, c.handle, c.name, s.code AS source, "
                "lw.week_start, lw.n_calls, lw.n_scored, lw.avg_score, lw.hit_rate "
                "FROM leaderboard_weekly lw "
                "JOIN channel c ON c.id=lw.channel_id "
                "JOIN source s ON s.id=c.source_id "
                "WHERE " + " AND ".join(wclauses) + " "
                "ORDER BY lw.week_start, lw.avg_score DESC"
            ), wparams).mappings()]
        else:
            weekly = [dict(r) for r in conn.execute(text("""
                SELECT lw.channel_id, c.handle, c.name, s.code AS source,
                       lw.week_start, lw.n_calls, lw.n_scored,
                       lw.avg_score, lw.hit_rate
                FROM leaderboard_weekly lw
                JOIN channel c ON c.id=lw.channel_id
                JOIN source s ON s.id=c.source_id
                WHERE lw.week_start >= (CURRENT_DATE - (:w * INTERVAL '7 day'))
                ORDER BY lw.week_start, lw.avg_score DESC
            """), {"w": weeks}).mappings()]

        overall_base = (
            "SELECT c.id AS channel_id, c.handle, c.name, s.code AS source, "
            "COUNT(p.id) AS n_calls, COUNT(p.score) AS n_scored, "
            "AVG(p.score) AS avg_score, "
            "AVG(CASE WHEN p.score>0 THEN 1.0 WHEN p.score<0 THEN 0.0 END) AS hit_rate "
            "FROM channel c JOIN source s ON s.id=c.source_id "
            "LEFT JOIN item i ON i.channel_id=c.id "
            "LEFT JOIN prediction p ON p.item_id=i.id "
            "AND p.extraction_run_id = i.primary_extraction_run_id "
        )
        overall_tail = (
            " GROUP BY c.id, s.code "
            "HAVING COUNT(p.id) > 0 "
            "ORDER BY avg_score DESC NULLS LAST"
        )
        if has_range:
            oclauses: list[str] = []
            oparams: dict[str, Any] = {}
            if date_from:
                oclauses.append("i.published_at >= :date_from")
                oparams["date_from"] = date_from
            if date_to:
                oclauses.append("i.published_at < (CAST(:date_to AS date) + INTERVAL '1 day')")
                oparams["date_to"] = date_to
            overall = [dict(r) for r in conn.execute(
                text(overall_base + "WHERE " + " AND ".join(oclauses) + overall_tail),
                oparams).mappings()]
        else:
            overall = [dict(r) for r in conn.execute(
                text(overall_base + overall_tail)).mappings()]

        # Speaker rollup (precomputed by `kb leaderboard rebuild`).
        speakers = [dict(r) for r in conn.execute(text("""
            SELECT ls.speaker, ls.n_calls, ls.n_scored, ls.avg_score,
                   ls.hit_rate, ls.last_call_at, ls.main_channel_id,
                   c.handle AS main_channel_handle,
                   c.name AS main_channel_name, s.code AS source
            FROM leaderboard_speaker ls
            LEFT JOIN channel c ON c.id = ls.main_channel_id
            LEFT JOIN source s ON s.id = c.source_id
            ORDER BY ls.avg_score DESC NULLS LAST
        """)).mappings()]
    return {"weekly": weekly, "overall": overall, "speakers": speakers}


@app.get("/api/models/leaderboard")
def models_leaderboard() -> dict[str, Any]:
    """Cross-model accuracy: same scoring as /api/leaderboard, but grouped by
    the LLM provider/model that produced each prediction instead of by
    channel. Lets you see whether e.g. openai/gpt-4o-mini or anthropic/claude
    extracts more accurate calls from the same underlying articles."""
    with engine().connect() as conn:
        overall = [dict(r) for r in conn.execute(text("""
            SELECT provider, model, n_calls, n_scored, avg_score, hit_rate, updated_at
            FROM provider_model_leaderboard
            WHERE channel_id IS NULL
            ORDER BY avg_score DESC NULLS LAST
        """)).mappings()]
        by_channel = [dict(r) for r in conn.execute(text("""
            SELECT pml.provider, pml.model, pml.channel_id, c.handle, c.name AS channel_name,
                   pml.n_calls, pml.n_scored, pml.avg_score, pml.hit_rate, pml.updated_at
            FROM provider_model_leaderboard pml
            JOIN channel c ON c.id = pml.channel_id
            ORDER BY c.name, pml.avg_score DESC NULLS LAST
        """)).mappings()]
    return {"overall": overall, "by_channel": by_channel}


@app.get("/api/market/tickers")
def market_tickers() -> list[dict[str, Any]]:
    """Ticker directory: every ticker referenced by an extracted prediction,
    with call counts, scores, and price-store coverage/sync status."""
    from .. import marketdata
    return marketdata.ticker_stats()


@app.get("/api/market/prices")
def market_prices(ticker: str = Query(..., description="e.g. GC=F, ^GSPC, AAPL"),
                  date_from: str | None = Query(None, description="YYYY-MM-DD"),
                  date_to: str | None = Query(None, description="YYYY-MM-DD"),
                  limit: int = Query(400, description="Max points (downsampled)")) -> dict[str, Any]:
    """Daily close series from the price store, for sparklines."""
    from .. import marketdata
    pts = marketdata.series(ticker.strip().upper(), date_from, date_to,
                            max_points=max(50, min(limit, 2000)))
    return {"ticker": ticker.strip().upper(), "points": pts}


@app.get("/api/items/{item_id}/raw")
def raw_md(item_id: int) -> FileResponse:
    with engine().connect() as conn:
        row = conn.execute(text("SELECT md_path FROM item WHERE id=:i"),
                           {"i": item_id}).first()
    if not row or not row[0]:
        raise HTTPException(404)
    return FileResponse(row[0], media_type="text/markdown")


# --- Insights (llm-wiki) ------------------------------------------------------
#
# The llm-wiki is generated markdown files in the repo (llm-wiki/), never
# stored in the DB (only its read-tracking is, in wiki_item_read). These
# endpoints list and serve those files straight from disk.

_WIKI_ROOT = ROOT / "llm-wiki"
# Fixed section order for the sidebar; page slugs are restricted to safe
# filename characters so a crafted path can't traverse outside llm-wiki/.
_WIKI_SECTIONS = ["Analysts", "People", "Syntheses", "Studies", "Themes", "Tickers", "Weekly"]
_SAFE_PAGE = re.compile(r"^[A-Za-z0-9^][A-Za-z0-9._^=-]*$")


def _wiki_title(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _wiki_files(section: str) -> list[str]:
    d = _WIKI_ROOT / section
    if not d.is_dir():
        return []
    return sorted(
        p.stem for p in d.glob("*.md")
        if p.name != "_Index.md" and _SAFE_PAGE.match(p.stem)
    )


@app.get("/api/insights")
def insights_index() -> dict[str, Any]:
    sections = [
        {"name": s, "pages": _wiki_files(s)}
        for s in _WIKI_SECTIONS
    ]
    return {"sections": sections, "has_home": (_WIKI_ROOT / "Home.md").is_file()}


@app.get("/api/insights/page")
def insights_page(section: str, page: str) -> dict[str, Any]:
    if section not in _WIKI_SECTIONS or not _SAFE_PAGE.match(page):
        raise HTTPException(404, "no such insights page")
    path = _WIKI_ROOT / section / f"{page}.md"
    if not path.is_file():
        raise HTTPException(404, "no such insights page")
    md_text = path.read_text(encoding="utf-8")
    return {"section": section, "page": page,
            "title": _wiki_title(md_text, page), "markdown": md_text}


@app.get("/api/insights/home")
def insights_home() -> dict[str, Any]:
    path = _WIKI_ROOT / "Home.md"
    if not path.is_file():
        raise HTTPException(404, "no home page")
    md_text = path.read_text(encoding="utf-8")
    return {"section": None, "page": "Home",
            "title": _wiki_title(md_text, "Insights"), "markdown": md_text}


# --- Static frontend (built SPA) -------------------------------------------
# Serve frontend/dist when present so `kb api` is a one-command local
# deployment: /api/* routes above win (registered first), /assets is a real
# static mount, and everything else falls back to index.html for SPA
# routing. Without a dist/ the app stays API-only (dev uses the Vite proxy).
_DIST = ROOT / "frontend" / "dist"
if (_DIST / "index.html").is_file():
    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        target = (_DIST / full_path).resolve() if full_path else None
        if target and target.is_file() and _DIST.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(_DIST / "index.html")


def main() -> None:
    import uvicorn
    s = settings()
    uvicorn.run("kb.api.main:app", host=s.api_host, port=s.api_port, reload=False)