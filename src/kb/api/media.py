"""Media-mention endpoints: the books / movies / papers extracted from item
content by extraction v2's `media_mentions` array (persisted into the
`media_work` / `media_mention` tables by `kb.extract._persist`).

Queries are built with SQLAlchemy Core expressions (no raw SQL strings) and
executed as single inline `select()` constructions — parameters (kind
filter, work id, limit/offset) are bound by construction.

Split out of main.py into its own APIRouter so the app module stays focused
on items/search/predictions; see spec/llm-extraction.md for the pipeline.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, column, desc, func, literal, or_, select, table

from ..db import engine

router = APIRouter()

MAX_PAGE_SIZE = 200

# Lightweight Core tables — just the columns these endpoints touch.
_media_work = table(
    "media_work",
    column("id"), column("kind"), column("title"), column("creators"),
    column("year"))
_media_mention = table(
    "media_mention",
    column("id"), column("media_work_id"), column("item_id"),
    column("extraction_run_id"), column("speaker"), column("quote"))
_item = table(
    "item",
    column("id"), column("title"), column("url"), column("published_at"),
    column("channel_id"), column("primary_extraction_run_id"))
_channel = table("channel", column("id"), column("handle"), column("name"))

# A mention counts towards the canonical view only when its run is the
# item's primary (canonical) extraction — same scoping as /api/predictions.
_CANONICAL = and_(_item.c.id == _media_mention.c.item_id,
                  _item.c.primary_extraction_run_id
                  == _media_mention.c.extraction_run_id)


@router.get("/api/media")
def media(kind: str | None = Query(None, description="book|movie|paper"),
          work_id: int | None = Query(
              None, description="Drill down: all mentions of one work"),
          limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Books / movies / papers extracted from item content (extraction v2's
    media_mentions). The list view ranks works by how often they are
    mentioned across items (canonical/primary extraction runs only), with
    the speakers who cited them; work_id= switches to the per-work
    drill-down: every mention with item, speaker and quote."""
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)

    if work_id is not None:
        with engine().connect() as conn:
            work = conn.execute(
                select(_media_work.c.id, _media_work.c.kind, _media_work.c.title,
                       _media_work.c.creators, _media_work.c.year)
                .where(_media_work.c.id == work_id)).mappings().first()
            if not work:
                raise HTTPException(404, "media work not found")
            mentions = [dict(r) for r in conn.execute(
                select(_media_mention.c.item_id, _item.c.title.label("item_title"),
                       _item.c.url, _item.c.published_at,
                       _channel.c.handle.label("channel"),
                       _channel.c.name.label("channel_name"),
                       _media_mention.c.speaker, _media_mention.c.quote)
                .select_from(_media_mention)
                .join(_item, and_(_item.c.id == _media_mention.c.item_id,
                                  _item.c.primary_extraction_run_id
                                  == _media_mention.c.extraction_run_id))
                .join(_channel, _channel.c.id == _item.c.channel_id,
                      isouter=True)
                .where(_media_mention.c.media_work_id == work_id)
                .order_by(desc(_item.c.published_at))).mappings()]
        return {"work": dict(work), "mentions": mentions}

    # Static kind condition: literal(true) when no valid kind was requested
    # (matches every row), else equality on the allowlisted value.
    kind_any = literal(kind not in ("book", "movie", "paper"))
    kind_cond = or_(kind_any, _media_work.c.kind == kind)
    counts = (
        select(_media_mention.c.media_work_id,
               func.count(_media_mention.c.id).label("n_mentions"),
               func.count(func.distinct(_media_mention.c.item_id)).label("n_items"),
               func.max(_item.c.published_at).label("last_mentioned_at"))
        .select_from(_media_mention)
        .join(_item, _CANONICAL)
        .group_by(_media_mention.c.media_work_id)
        .subquery())
    with engine().connect() as conn:
        total = conn.execute(
            select(func.count())
            .select_from(_media_work)
            .join(counts, counts.c.media_work_id == _media_work.c.id)
            .where(kind_cond)).scalar() or 0
        works = [dict(r) for r in conn.execute(
            select(_media_work.c.id, _media_work.c.kind, _media_work.c.title,
                   _media_work.c.creators, _media_work.c.year,
                   counts.c.n_mentions, counts.c.n_items,
                   counts.c.last_mentioned_at)
            .select_from(_media_work)
            .join(counts, counts.c.media_work_id == _media_work.c.id)
            .where(kind_cond)
            .order_by(desc(counts.c.n_mentions),
                      desc(counts.c.last_mentioned_at))
            .limit(limit).offset(offset)).mappings().all()]
        ids = [w["id"] for w in works]
        speakers_by_work: dict[int, list[str]] = defaultdict(list)
        if ids:
            for wid, speaker in conn.execute(
                    select(_media_mention.c.media_work_id,
                           _media_mention.c.speaker)
                    .select_from(_media_mention)
                    .join(_item, _CANONICAL)
                    .where(_media_mention.c.media_work_id.in_(ids))).all():
                if speaker and speaker not in speakers_by_work[wid]:
                    speakers_by_work[wid].append(speaker)
    for w in works:
        w["speakers"] = speakers_by_work.get(w["id"], [])
    return {"works": works, "total": total, "limit": limit, "offset": offset}
