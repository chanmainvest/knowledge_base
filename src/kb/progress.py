"""Pipeline progress tracking.

Keeps the `source_progress` table (one row per source) and the per-item
`ingested_at` / `extracted_at` timestamps accurate as content moves through
the scrape → ingest → extract pipeline.

Each pipeline boundary calls one of the `mark_*` functions, which increment
the relevant counter and stamp `last_*_at` via a single UPSERT. Counts are
incremented at runtime rather than recomputed on every call (cheap, O(1)).
`recompute()` does a full `COUNT(*)` recount from the `item` table and is
used (a) by the init.sql backfill, (b) by `kb progress recompute` to recover
from drift, and (c) once at the end of an `extract run` batch as a safety net.

`n_downloaded` is best-effort: it counts scrape write events from when this
feature shipped forward (the filesystem-discovery sources have no historical
catalog to reconstruct it from). It is reconciled against the hkej/patreon
catalog tables where they exist.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import text

from .config import DATA_DIR
from .db import engine
from .logging_setup import get_logger

log = get_logger("progress")


def count_downloaded_on_disk() -> dict[str, int]:
    """Count actual downloaded markdown files per source on disk.

    Walks `DATA_DIR/<source>/**/*.md`, excluding `README.md` index files and
    anything under a `raw/` directory (raw HTML lives there, not content).
    This is the ground truth for "what's been downloaded" for the
    filesystem-discovery sources that have no catalog table; for hkej/patreon
    it should roughly match their catalog `downloaded` counts.
    """
    out: dict[str, int] = {}
    if not DATA_DIR.exists():
        return out
    for src_dir in sorted(p for p in DATA_DIR.iterdir() if p.is_dir()):
        if src_dir.name == "raw":
            continue
        n = sum(1 for f in src_dir.rglob("*.md")
                if f.name != "README.md" and "raw" not in f.parts)
        out[src_dir.name] = n
    return out


def _source_id_for(code: str) -> int | None:
    with engine().connect() as conn:
        return conn.execute(text("SELECT id FROM source WHERE code=:c"),
                            {"c": code}).scalar_one_or_none()


def mark_downloaded(source_code: str) -> None:
    """Record one downloaded/scraped file for the source. Safe to call before
    a source row exists in `source_progress` (the UPSERT creates it)."""
    sid = _source_id_for(source_code)
    if sid is None:
        return
    with engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO source_progress(source_id, n_downloaded, last_scrape_at)
            VALUES (:sid, 1, now())
            ON CONFLICT (source_id) DO UPDATE SET
                n_downloaded = source_progress.n_downloaded + 1,
                last_scrape_at = now(),
                updated_at = now()
        """), {"sid": sid})


def mark_ingested(source_id: int) -> None:
    """Record one ingested item (called after the item upsert succeeds)."""
    with engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO source_progress(source_id, n_ingested, last_ingest_at)
            VALUES (:sid, 1, now())
            ON CONFLICT (source_id) DO UPDATE SET
                n_ingested = source_progress.n_ingested + 1,
                last_ingest_at = now(),
                updated_at = now()
        """), {"sid": source_id})


def mark_extracted(source_id: int, status: str) -> None:
    """Record the outcome of extracting one item. `status` is the new
    `extraction_status` value: 'done' or 'error'. The counter for that status
    is incremented; `last_extract_at` and `updated_at` are stamped.

    Note: this counts extraction *attempts that settled*, not net transitions,
    so re-extracting an already-done item will over-count. The batch driver
    calls `recompute()` at the end of a run to reconcile, and `kb progress
    recompute` can fix drift on demand.
    """
    col = {"done": "n_extracted", "error": "n_extract_error"}.get(status)
    if col is None:
        return
    with engine().begin() as conn:
        conn.execute(text(f"""
            INSERT INTO source_progress(source_id, {col}, last_extract_at)
            VALUES (:sid, 1, now())
            ON CONFLICT (source_id) DO UPDATE SET
                {col} = source_progress.{col} + 1,
                last_extract_at = now(),
                updated_at = now()
        """), {"sid": source_id})


def recompute() -> None:
    """Full reconciliation of every source's counters from authoritative sources.

    `n_ingested` / `n_extracted` / `n_extract_pending` / `n_extract_error` and
    the ingest/extract timestamps are recomputed from the `item` table.
    `n_downloaded` is set from an actual filesystem scan of
    `DATA_DIR/<source>/**/*.md` (the ground truth for what has been downloaded,
    including the filesystem-discovery sources that have no catalog table).
    Use to seed correct values on first deploy or recover from drift.
    """
    disk = count_downloaded_on_disk()
    with engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO source_progress(source_id)
            SELECT id FROM source
            ON CONFLICT (source_id) DO NOTHING
        """))
        # Stamp n_downloaded per source from the disk scan.
        rows = conn.execute(text("SELECT id, code FROM source")).all()
        for sid, code in rows:
            n = disk.get(code, 0)
            conn.execute(text("""
                UPDATE source_progress SET n_downloaded = :n WHERE source_id = :sid
            """), {"n": n, "sid": sid})
        conn.execute(text("""
            UPDATE source_progress sp SET
                n_ingested        = COALESCE((
                    SELECT COUNT(*) FROM item i
                    WHERE i.source_id = sp.source_id AND i.ingested_at IS NOT NULL), 0),
                n_extracted       = COALESCE((
                    SELECT COUNT(*) FROM item i
                    WHERE i.source_id = sp.source_id AND i.extraction_status = 'done'), 0),
                n_extract_pending = COALESCE((
                    SELECT COUNT(*) FROM item i
                    WHERE i.source_id = sp.source_id AND i.extraction_status = 'pending'), 0),
                n_extract_error   = COALESCE((
                    SELECT COUNT(*) FROM item i
                    WHERE i.source_id = sp.source_id AND i.extraction_status = 'error'), 0),
                last_ingest_at    = (
                    SELECT MAX(i.ingested_at) FROM item i WHERE i.source_id = sp.source_id),
                last_extract_at   = (
                    SELECT MAX(i.extracted_at) FROM item i WHERE i.source_id = sp.source_id),
                updated_at        = now()
        """))
    log.info("recomputed source_progress (item table + disk scan)")


def snapshot() -> list[dict]:
    """Current per-source progress rows joined to source for display."""
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT s.code, s.name, s.kind,
                   sp.n_downloaded, sp.n_ingested, sp.n_extracted,
                   sp.n_extract_pending, sp.n_extract_error,
                   sp.last_scrape_at, sp.last_ingest_at, sp.last_extract_at,
                   sp.updated_at
            FROM source s
            LEFT JOIN source_progress sp ON sp.source_id = s.id
            ORDER BY s.name
        """)).mappings().all()
    return [dict(r) for r in rows]
