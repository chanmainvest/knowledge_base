#!/usr/bin/env python3
"""Audit + backfill: how many YouTube items lack a transcript/subtitle?

The YouTube scraper writes ``_(no transcript available)_`` into the markdown
body when neither yt-dlp subtitles nor the youtube-transcript-api fallback
produced any text. This script:

1. Scans every YouTube markdown file on disk and reports the count with/without
   a transcript, broken down per channel.
2. Updates ``item.has_transcript`` in the DB for every ingested YouTube item
   based on the marker, so the column stays authoritative.
3. Also scans DB rows whose ``md_path`` file is missing or unreadable
   (defensive — those rows can't be checked against disk).

Usage::

    uv run python scripts/backfill_has_transcript.py            # report + update
    uv run python scripts/backfill_has_transcript.py --no-update # report only (dry run)
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Allow running as a standalone script (scripts/ is outside src/).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kb.config import DATA_DIR  # noqa: E402
from kb.db import engine  # noqa: E402
from kb.scrapers.youtube import NO_TRANSCRIPT_MARKER  # noqa: E402

from sqlalchemy import text  # noqa: E402

YOUTUBE_DIR = DATA_DIR / "youtube"


def _content_has_marker(content: str) -> bool:
    """True if the markdown body's Transcript section is the no-transcript
    placeholder. We check the marker appears in the body (the scraper only
    ever writes it as the sole content of that section, so a substring match
    is unambiguous)."""
    return NO_TRANSCRIPT_MARKER in content


# ---------------------------------------------------------------------------
# 1. Disk scan
# ---------------------------------------------------------------------------
def scan_disk() -> dict[str, dict]:
    """Walk data/youtube/**/*.md and return {channel: {total, missing, files}}."""
    per_channel: dict[str, dict] = defaultdict(lambda: {"total": 0, "missing": 0})
    files_missing: list[str] = []
    if not YOUTUBE_DIR.exists():
        print(f"WARNING: {YOUTUBE_DIR} does not exist")
        return per_channel
    for md in YOUTUBE_DIR.rglob("*.md"):
        if md.name == "README.md":
            continue
        channel = md.relative_to(YOUTUBE_DIR).parts[0]
        per_channel[channel]["total"] += 1
        try:
            body = md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if _content_has_marker(body):
            per_channel[channel]["missing"] += 1
            files_missing.append(str(md))
    return per_channel


# ---------------------------------------------------------------------------
# 2. DB backfill
# ---------------------------------------------------------------------------
def backfill_db(do_update: bool) -> tuple[int, int]:
    """Update item.has_transcript for YouTube rows. Returns (n_total, n_missing)."""
    with engine().begin() as conn:
        rows = conn.execute(text("""
            SELECT i.id, i.content, i.md_path
            FROM item i JOIN source s ON s.id = i.source_id
            WHERE s.code = 'youtube'
        """)).all()
        n_total = len(rows)
        to_false: list[int] = []
        to_true: list[int] = []
        for r in rows:
            has = not _content_has_marker(r.content or "")
            # Only collect rows that need changing (avoids pointless writes).
            (to_true if has else to_false).append(r.id)
        n_missing = len(to_false)
        if do_update:
            # Batch the updates in chunks to avoid huge parameter lists.
            if to_false:
                for i in range(0, len(to_false), 1000):
                    chunk = to_false[i:i + 1000]
                    conn.execute(text(
                        "UPDATE item SET has_transcript = false WHERE id = ANY(:ids)"
                    ), {"ids": chunk})
            if to_true:
                for i in range(0, len(to_true), 1000):
                    chunk = to_true[i:i + 1000]
                    conn.execute(text(
                        "UPDATE item SET has_transcript = true WHERE id = ANY(:ids)"
                    ), {"ids": chunk})
        return n_total, n_missing


# ---------------------------------------------------------------------------
# 3. Report
# ---------------------------------------------------------------------------
def print_report(per_channel: dict, n_db_total: int, n_db_missing: int,
                 do_update: bool) -> None:
    disk_total = sum(c["total"] for c in per_channel.values())
    disk_missing = sum(c["missing"] for c in per_channel.values())

    print("=" * 66)
    print("  YouTube transcript/subtitle audit")
    print("=" * 66)
    print()
    print("  DISK SCAN  (data/youtube/**/*.md)")
    print(f"    total files:          {disk_total:>7,}")
    print(f"    with transcript:      {disk_total - disk_missing:>7,}  ({_pct(disk_total - disk_missing, disk_total)})")
    print(f"    MISSING transcript:   {disk_missing:>7,}  ({_pct(disk_missing, disk_total)})")
    print()
    print("  DATABASE  (item rows where source = youtube)")
    print(f"    total rows:           {n_db_total:>7,}")
    print(f"    MISSING (has_transcript=false): {n_db_missing:>4,}  ({_pct(n_db_missing, n_db_total)})")
    print()
    print("  PER-CHANNEL BREAKDOWN  (disk, sorted by missing desc)")
    print(f"    {'channel':<32s} {'total':>7s} {'missing':>7s} {'%':>6s}")
    print(f"    {'-' * 32} {'-' * 7} {'-' * 7} {'-' * 6}")
    for ch in sorted(per_channel, key=lambda c: per_channel[c]["missing"], reverse=True):
        c = per_channel[ch]
        if c["total"] == 0:
            continue
        print(f"    {ch:<32s} {c['total']:>7d} {c['missing']:>7d} {_pct(c['missing'], c['total']):>6s}")
    print()
    if do_update:
        print(f"  DB updated: has_transcript set for {n_db_total:,} YouTube rows "
              f"({n_db_missing:,} marked missing).")
    else:
        print("  DRY RUN — DB not modified. Re-run without --no-update to apply.")
    print("=" * 66)


def _pct(num: int, denom: int) -> str:
    return f"{(100.0 * num / denom):.1f}%" if denom else "—"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-update", action="store_true",
                    help="Report only; don't modify the database (dry run).")
    args = ap.parse_args()

    per_channel = scan_disk()
    n_db_total, n_db_missing = backfill_db(do_update=not args.no_update)
    print_report(per_channel, n_db_total, n_db_missing, do_update=not args.no_update)


if __name__ == "__main__":
    main()
