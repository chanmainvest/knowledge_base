"""FastAPI app: search, items, leaderboard."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from ..config import settings
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
    has_predictions: bool | None = None,
) -> tuple[list[str], dict[str, Any], list[str]]:
    """Build shared WHERE clauses/params for the item-list and search
    endpoints so both support multi-select sources/channels, a published_at
    date range, and a with/without-prediction-extraction filter identically.

    `has_predictions` filters on the item's canonical (primary) extraction
    run: True keeps items with at least one extracted prediction there,
    False keeps items with none (including items never extracted).

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
        exists_sql = (
            "EXISTS (SELECT 1 FROM prediction p "
            "WHERE p.item_id=i.id AND p.extraction_run_id=i.primary_extraction_run_id)"
        )
        clauses.append(exists_sql if has_predictions else f"NOT {exists_sql}")
    return clauses, params, expanding


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
           has_predictions: bool | None = Query(
               None, description="true = only items with extracted predictions, "
                                  "false = only items without"),
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
        item["predictions"] = [dict(r) for r in conn.execute(text(
            "SELECT * FROM prediction WHERE item_id=:i AND extraction_run_id=:r ORDER BY id"),
            {"i": item_id, "r": effective_run_id}).mappings()]
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
               has_predictions: bool | None = Query(
                   None, description="true = only items with extracted predictions, "
                                      "false = only items without"),
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
                channel_id: int | None = None,
                limit: int = 100) -> list[dict[str, Any]]:
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT p.*, i.title AS item_title, i.url AS item_url,
                   c.handle AS channel, c.name AS channel_name
            FROM prediction p JOIN item i ON i.id=p.item_id
            LEFT JOIN channel c ON c.id=i.channel_id
            WHERE (CAST(:t AS text) IS NULL OR p.ticker=:t)
              AND (CAST(:cid AS integer) IS NULL OR i.channel_id=:cid)
            ORDER BY p.made_at DESC NULLS LAST LIMIT :lim
        """), {"t": ticker, "cid": channel_id, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/leaderboard")
def leaderboard(weeks: int = 12) -> dict[str, Any]:
    with engine().connect() as conn:
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
        overall = [dict(r) for r in conn.execute(text("""
            SELECT c.id AS channel_id, c.handle, c.name, s.code AS source,
                   COUNT(p.id) AS n_calls,
                   COUNT(p.score) AS n_scored,
                   AVG(p.score) AS avg_score,
                   AVG(CASE WHEN p.score>0 THEN 1.0 WHEN p.score<0 THEN 0.0 END) AS hit_rate
            FROM channel c JOIN source s ON s.id=c.source_id
            LEFT JOIN item i ON i.channel_id=c.id
            LEFT JOIN prediction p ON p.item_id=i.id
            GROUP BY c.id, s.code
            HAVING COUNT(p.id) > 0
            ORDER BY avg_score DESC NULLS LAST
        """)).mappings()]
    return {"weekly": weekly, "overall": overall}


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


@app.get("/api/items/{item_id}/raw")
def raw_md(item_id: int) -> FileResponse:
    with engine().connect() as conn:
        row = conn.execute(text("SELECT md_path FROM item WHERE id=:i"),
                           {"i": item_id}).first()
    if not row or not row[0]:
        raise HTTPException(404)
    return FileResponse(row[0], media_type="text/markdown")


def main() -> None:
    import uvicorn
    s = settings()
    uvicorn.run("kb.api.main:app", host=s.api_host, port=s.api_port, reload=False)
