"""Generic discovery catalog for the filesystem-discovery scrapers.

Records every item a scraper sees during discovery (before fetch) into the
`discovery_catalog` table, so "discovered but not downloaded" is queryable
and a half-dead scrape can be resumed via :func:`pending`.

Used by youtube, blog (macrovoices/madxcap), substack, yahoohk, and
master-insight — the sources whose only pre-existing "have I seen this?"
check was a filesystem probe. hkej and patreon keep their richer native
catalogs (``hkej_article_catalog`` / ``patreon_post_catalog``), which carry
run/page fingerprinting and resume cursors this table does not; their
pending counts are unioned in at read time by :func:`pending_counts`.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from .db import engine
from .logging_setup import get_logger

log = get_logger("catalog")


def _normalize(descriptor: dict) -> dict[str, Any]:
    """Pull a uniform shape out of a scraper-specific discovery descriptor.

    Descriptors are ad-hoc dicts whose only required keys are ``external_id``
    and ``url``. Channel identity is expressed inconsistently
    (``channel_handle`` vs a nested ``author`` dict), so resolve it here.
    """
    eid = str(descriptor.get("external_id") or "").strip()
    if not eid:
        return {}
    # Channel reference: prefer an explicit handle, else the author slug, else
    # leave blank (macrovoices/madxcap have no channel at discovery time).
    ch_ref = (descriptor.get("channel_handle") or "").strip()
    if not ch_ref:
        author = descriptor.get("author")
        if isinstance(author, dict):
            ch_ref = (author.get("slug") or author.get("handle") or "").strip()
    pub = descriptor.get("published_at")
    if isinstance(pub, str):
        try:
            pub = datetime.fromisoformat(pub.replace("Z", ""))
        except Exception:
            pub = None
    elif isinstance(pub, datetime):
        pass
    else:
        pub = None
    return {
        "external_id": eid,
        "url": descriptor.get("url"),
        "title": descriptor.get("title"),
        "published_at": pub,
        "channel_ref": ch_ref or None,
    }


def record_discovery(source_code: str, descriptor: dict) -> None:
    """Upsert one discovered item into ``discovery_catalog``.

    On conflict (same source + external_id) only refreshes ``last_seen_at``
    and the lightweight metadata (title/url/published_at/descriptor); it
    **never touches** ``downloaded`` so a re-discovery cannot un-mark a
    completed download.
    """
    n = _normalize(descriptor)
    if not n:
        return
    # Stash the original descriptor for resume(). Drop unserializable values
    # defensively (datetimes serialize via default=str below).
    try:
        desc_json = json.dumps(descriptor, ensure_ascii=False, default=str)
    except Exception:
        desc_json = None
    with engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO discovery_catalog(
                source_id, channel_id, channel_ref, external_id, title, url,
                published_at, discovered_at, last_seen_at, descriptor)
            SELECT s.id,
                   (SELECT c.id FROM channel c
                    WHERE c.source_id = s.id AND c.handle = :chref),
                   :chref, :eid, :title, :url, :pub, now(), now(),
                   CAST(:desc AS jsonb)
            FROM source s WHERE s.code = :code
            ON CONFLICT (source_id, external_id) DO UPDATE SET
                last_seen_at = now(),
                title       = COALESCE(EXCLUDED.title, discovery_catalog.title),
                url         = COALESCE(EXCLUDED.url, discovery_catalog.url),
                published_at = COALESCE(EXCLUDED.published_at, discovery_catalog.published_at),
                descriptor  = COALESCE(EXCLUDED.descriptor, discovery_catalog.descriptor)
        """), {"code": source_code, "chref": n["channel_ref"], "eid": n["external_id"],
               "title": n["title"], "url": n["url"], "pub": n["published_at"],
               "desc": desc_json})


def mark_downloaded(source_code: str, external_id: str, md_path: str) -> None:
    """Flip a catalog row to ``downloaded=true`` after the file is written."""
    with engine().begin() as conn:
        conn.execute(text("""
            UPDATE discovery_catalog SET
                downloaded = true, downloaded_at = now(), md_path = :md
            WHERE source_id = (SELECT id FROM source WHERE code = :code)
              AND external_id = :eid
        """), {"code": source_code, "eid": str(external_id), "md": md_path})


def pending(source_code: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Return pending (not-downloaded) catalog rows for a source, oldest-
    discovered first, for the resume path. Each row carries the round-tripped
    ``descriptor`` dict plus its ``id``."""
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT id, descriptor, title, url, external_id, channel_ref, published_at
            FROM discovery_catalog
            WHERE source_id = (SELECT id FROM source WHERE code = :code)
              AND downloaded = false
            ORDER BY discovered_at
            LIMIT :lim
        """), {"code": source_code, "lim": limit}).mappings().all()
    out = []
    for r in rows:
        d = dict(r.get("descriptor") or {})
        # Ensure the fetchable keys are present even if the stored descriptor
        # was sparse (older rows); fall back to the row's own columns.
        d.setdefault("external_id", r["external_id"])
        d.setdefault("url", r["url"])
        d.setdefault("title", r["title"])
        if r.get("channel_ref"):
            d.setdefault("channel_handle", r["channel_ref"])
        if r.get("published_at"):
            # Restore a datetime from the row so scrapers that compute paths
            # / parse dates from the descriptor (and call .isoformat()/.strftime)
            # see the same type discover() originally yielded. The stored JSONB
            # descriptor may carry the value as a string (serialized via
            # default=str on insert), so overwrite — don't just setdefault.
            pub = r["published_at"]
            if not isinstance(pub, datetime):
                try:
                    pub = datetime.fromisoformat(str(pub).replace("Z", ""))
                except Exception:
                    pub = None
            if pub is not None:
                d["published_at"] = pub
        out.append({"id": r["id"], "descriptor": d})
    return out


def pending_counts() -> dict[str, int]:
    """Per-source count of discovered-but-not-downloaded items, combining the
    generic ``discovery_catalog`` (5 filesystem sources) with the hkej/patreon
    native catalogs where they exist. Keys are source codes."""
    counts: dict[str, int] = {}
    with engine().connect() as conn:
        # Generic catalog.
        rows = conn.execute(text("""
            SELECT s.code, COUNT(*) AS n
            FROM discovery_catalog dc JOIN source s ON s.id = dc.source_id
            WHERE dc.downloaded = false
            GROUP BY s.code
        """)).mappings().all()
        for r in rows:
            counts[r["code"]] = int(r["n"])
        # hkej native catalog.
        rows = conn.execute(text("""
            SELECT s.code, COUNT(*) AS n
            FROM hkej_article_catalog hac
            JOIN channel ch ON ch.id = hac.channel_id
            JOIN source s ON s.id = ch.source_id
            WHERE hac.downloaded = false
            GROUP BY s.code
        """)).mappings().all()
        for r in rows:
            counts[r["code"]] = counts.get(r["code"], 0) + int(r["n"])
        # patreon native catalog.
        rows = conn.execute(text("""
            SELECT s.code, COUNT(*) AS n
            FROM patreon_post_catalog ppc
            JOIN channel ch ON ch.id = ppc.channel_id
            JOIN source s ON s.id = ch.source_id
            WHERE ppc.downloaded = false
            GROUP BY s.code
        """)).mappings().all()
        for r in rows:
            counts[r["code"]] = counts.get(r["code"], 0) + int(r["n"])
    return counts


def known_totals() -> dict[str, int | None]:
    """Per-source "total items known to exist upstream", where the source's
    API exposes it. Sources without an upstream total are absent from the
    result (callers treat missing keys as "unknown").

    - yahoohk: ``channel.metadata->>'total_seen'`` summed across channels
      (the GraphQL ``pagination.total`` per author).
    - hkej: ``SUM(hkej_author_state.search_total)``.
    - patreon: ``SUM(patreon_creator_state.total_posts)``.
    """
    out: dict[str, int | None] = {}
    with engine().connect() as conn:
        # yahoohk: total_seen stored on each channel's metadata.
        r = conn.execute(text("""
            SELECT COALESCE(SUM((c.metadata->>'total_seen')::int), 0) AS n
            FROM channel c JOIN source s ON s.id = c.source_id
            WHERE s.code = 'yahoohk' AND c.metadata ? 'total_seen'
        """)).scalar_one_or_none()
        if r:
            out["yahoohk"] = int(r)
        # hkej search totals.
        r = conn.execute(text("""
            SELECT COALESCE(SUM(has.search_total), 0) AS n
            FROM hkej_author_state has
        """)).scalar_one_or_none()
        if r:
            out["hkej"] = int(r)
        # patreon post totals.
        r = conn.execute(text("""
            SELECT COALESCE(SUM(pcs.total_posts), 0) AS n
            FROM patreon_creator_state pcs
        """)).scalar_one_or_none()
        if r:
            out["patreon"] = int(r)
    return out
