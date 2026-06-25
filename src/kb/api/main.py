"""FastAPI app: search, items, leaderboard."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text

from ..config import settings
from ..db import engine

app = FastAPI(title="KB API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)


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
def channels(source: str | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT c.id, c.handle, c.name, s.code AS source, COUNT(i.id) AS n_items "
           "FROM channel c JOIN source s ON s.id=c.source_id "
           "LEFT JOIN item i ON i.channel_id=c.id "
           "WHERE (CAST(:s AS text) IS NULL OR s.code=:s) "
           "GROUP BY c.id, s.code ORDER BY n_items DESC, c.name")
    with engine().connect() as conn:
        rows = conn.execute(text(sql), {"s": source}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/search")
def search(q: str = Query(..., min_length=1),
           source: str | None = None,
           channel_id: int | None = None,
           limit: int = 30) -> list[dict[str, Any]]:
    sql = """
        SELECT i.id, i.title, i.url, i.published_at, i.summary,
               s.code AS source, c.handle AS channel, c.name AS channel_name,
               ts_headline('simple', i.content,
                 plainto_tsquery('simple', :q),
                 'MaxFragments=2,MinWords=8,MaxWords=30,ShortWord=2') AS snippet,
               ts_rank(i.tsv, plainto_tsquery('simple', :q)) AS rank
        FROM item i
        JOIN source s ON s.id=i.source_id
        LEFT JOIN channel c ON c.id=i.channel_id
        WHERE i.tsv @@ plainto_tsquery('simple', :q)
          AND (CAST(:src AS text) IS NULL OR s.code=:src)
          AND (CAST(:cid AS integer) IS NULL OR i.channel_id=:cid)
        ORDER BY rank DESC, i.published_at DESC NULLS LAST
        LIMIT :lim
    """
    with engine().connect() as conn:
        rows = conn.execute(text(sql),
                            {"q": q, "src": source, "cid": channel_id,
                             "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/items/{item_id}")
def get_item(item_id: int) -> dict[str, Any]:
    with engine().connect() as conn:
        row = conn.execute(text("""
            SELECT i.*, s.code AS source, c.handle AS channel, c.name AS channel_name
            FROM item i JOIN source s ON s.id=i.source_id
            LEFT JOIN channel c ON c.id=i.channel_id WHERE i.id=:i
        """), {"i": item_id}).mappings().first()
        if not row:
            raise HTTPException(404)
        item = dict(row)
        item["market_views"] = [dict(r) for r in conn.execute(text(
            "SELECT * FROM view_market WHERE item_id=:i"), {"i": item_id}).mappings()]
        item["predictions"] = [dict(r) for r in conn.execute(text(
            "SELECT * FROM prediction WHERE item_id=:i ORDER BY id"),
            {"i": item_id}).mappings()]
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


@app.get("/api/items")
def list_items(source: str | None = None, channel_id: int | None = None,
               limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT i.id, i.title, i.url, i.published_at, i.summary,
                   s.code AS source, c.handle AS channel, c.name AS channel_name
            FROM item i JOIN source s ON s.id=i.source_id
            LEFT JOIN channel c ON c.id=i.channel_id
            WHERE (CAST(:s AS text) IS NULL OR s.code=:s)
              AND (CAST(:cid AS integer) IS NULL OR i.channel_id=:cid)
            ORDER BY i.published_at DESC NULLS LAST, i.id DESC
            LIMIT :lim OFFSET :off
        """), {"s": source, "cid": channel_id, "lim": limit, "off": offset}).mappings().all()
    return [dict(r) for r in rows]


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
