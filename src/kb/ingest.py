"""Ingest scraped markdown files from data/ into Postgres."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from .config import DATA_DIR
from .db import engine
from .io_md import load_md
from .logging_setup import get_logger

log = get_logger("ingest")


def _upsert_channel(conn, source_code: str, handle: str, name: str) -> int:
    sid = conn.execute(text("SELECT id FROM source WHERE code=:c"),
                       {"c": source_code}).scalar_one()
    row = conn.execute(text(
        "INSERT INTO channel(source_id, handle, name) VALUES (:s,:h,:n) "
        "ON CONFLICT (source_id, handle) DO UPDATE SET name=EXCLUDED.name "
        "RETURNING id"), {"s": sid, "h": handle, "n": name}).scalar_one()
    return row


def ingest_file(md_path: Path) -> int | None:
    doc = load_md(md_path)
    fm = doc.front
    if not fm.get("source") or not fm.get("external_id"):
        log.warning("skip (missing front-matter): %s", md_path)
        return None
    pub = fm.get("published_at")
    if isinstance(pub, str):
        try:
            pub_dt: datetime | None = datetime.fromisoformat(pub.replace("Z", ""))
        except Exception:
            pub_dt = None
    else:
        pub_dt = pub

    with engine().begin() as conn:
        sid = conn.execute(text("SELECT id FROM source WHERE code=:c"),
                           {"c": fm["source"]}).scalar_one()
        cid = _upsert_channel(conn, fm["source"],
                              fm.get("channel") or "unknown",
                              fm.get("channel_name") or fm.get("channel") or "unknown")
        item_id = conn.execute(text("""
            INSERT INTO item(source_id, channel_id, external_id, title, url,
                             published_at, language, duration_sec, md_path,
                             content, metadata, has_transcript, ingested_at)
            VALUES (:s,:ch,:eid,:t,:u,:p,:l,:d,:mp,:c,:m,:ht, now())
            ON CONFLICT (source_id, external_id) DO UPDATE SET
              title=EXCLUDED.title, url=EXCLUDED.url, published_at=EXCLUDED.published_at,
              language=EXCLUDED.language, duration_sec=EXCLUDED.duration_sec,
              md_path=EXCLUDED.md_path, content=EXCLUDED.content,
              metadata=EXCLUDED.metadata, has_transcript=EXCLUDED.has_transcript,
              ingested_at=now()
            RETURNING id
        """), {
            "s": sid, "ch": cid, "eid": fm["external_id"],
            "t": fm.get("title", ""), "u": fm.get("url"),
            "p": pub_dt, "l": fm.get("language"),
            "d": fm.get("duration_sec"),
            "mp": str(md_path),
            "c": doc.body,
            "m": _json(fm.get("extra") or {}),
            "ht": bool(fm.get("has_transcript", True)),
        }).scalar_one()
    # Bump the per-source progress counter (best-effort; never abort ingest).
    try:
        from . import progress
        progress.mark_ingested(sid)
    except Exception:  # noqa: BLE001
        log.debug("progress.mark_ingested failed", exc_info=True)
    return item_id


def _json(o):
    import json
    return json.dumps(o, ensure_ascii=False, default=str)


def ingest_all() -> int:
    n = 0
    for p in DATA_DIR.rglob("*.md"):
        parts = p.relative_to(DATA_DIR).parts
        if parts[0] == "raw":  # skip data/raw/ hierarchy
            continue
        try:
            iid = ingest_file(p)
            if iid:
                n += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("ingest failed for %s: %s", p, exc)
    log.info("ingested %d items", n)
    return n
