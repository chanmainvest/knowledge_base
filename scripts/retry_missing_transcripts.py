#!/usr/bin/env python3
"""Re-fetch transcripts for YouTube items currently marked has_transcript=false.

Many "missing" videos DO have subtitles/transcripts — they failed during the
original scrape due to rate-limiting (HTTP 429), the narrow language filter,
or the missing deno JS runtime. This script re-tries each one with the
improved fetcher (deno-enabled yt-dlp + broader transcript-api language
fallback) and updates both the markdown file and the DB row when a transcript
is recovered.

It calls ``fetch()`` + ``write_md()`` directly (bypassing the ``run()`` loop's
``already_scraped`` skip), so existing markdown files are overwritten with the
recovered transcript.

Usage::

    uv run python scripts/retry_missing_transcripts.py             # re-try all
    uv run python scripts/retry_missing_transcripts.py --limit 50  # first 50
    uv run python scripts/retry_missing_transcripts.py --channel latp  # one channel
    uv run python scripts/retry_missing_transcripts.py --dry-run   # report only
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kb.db import engine  # noqa: E402
from kb.ingest import ingest_file  # noqa: E402
from kb.scrapers.youtube import YouTubeScraper  # noqa: E402
from sqlalchemy import text  # noqa: E402


def gather_targets(limit: int, channel: str | None) -> list[dict]:
    """Return YouTube item rows with has_transcript=false."""
    sql = """
        SELECT i.id, i.external_id, i.title, i.md_path,
               ch.handle AS channel_handle, ch.name AS channel_name
        FROM item i
        JOIN source s ON s.id = i.source_id
        LEFT JOIN channel ch ON ch.id = i.channel_id
        WHERE s.code = 'youtube' AND i.has_transcript = false
    """
    params: dict = {}
    if channel:
        sql += " AND (ch.handle ILIKE :ch OR ch.name ILIKE :ch)"
        params["ch"] = f"%{channel}%"
    sql += " ORDER BY i.id"
    if limit:
        sql += f" LIMIT {limit}"
    with engine().connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


async def retry_all(targets: list[dict], delay: float, dry_run: bool) -> tuple[int, int]:
    """Re-fetch each target. Returns (recovered, still_missing)."""
    scraper = YouTubeScraper()
    recovered = 0
    still_missing = 0
    total = len(targets)

    for i, row in enumerate(targets, 1):
        vid = row["external_id"]
        title = (row["title"] or "")[:50]
        print(f"  [{i}/{total}] {vid} | {title}...", end=" ", flush=True)

        d = {
            "external_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": row["title"],
            "channel_handle": row.get("channel_handle") or "@unknown",
            "channel_name": row.get("channel_name") or "Unknown",
            "duration": None,
            "upload_date": None,
        }
        try:
            item = await scraper.fetch(d)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ error: {exc}")
            still_missing += 1
            if i < total and delay:
                time.sleep(delay)
            continue

        if item is None or not item.has_transcript:
            print("✗ still missing")
            still_missing += 1
        else:
            print("✓ RECOVERED")
            recovered += 1
            if not dry_run:
                # Overwrite the markdown file with the recovered transcript.
                p = scraper.write_md(item)
                # Re-ingest to update has_transcript in the DB.
                ingest_file(p)

        if i < total and delay:
            time.sleep(delay)

    return recovered, still_missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="Max items to retry (0=all)")
    ap.add_argument("--channel", default=None, help="Only retry this channel handle/name")
    ap.add_argument("--dry-run", action="store_true", help="Don't modify files/DB")
    ap.add_argument("--delay", type=float, default=5.0,
                    help="Seconds between videos to avoid 429. Default 5 (the "
                         "scraper's own limiter also backs off reactively on 429).")
    args = ap.parse_args()

    targets = gather_targets(args.limit, args.channel)
    total = len(targets)
    print(f"Retrying {total} YouTube items with missing transcripts...")
    if args.dry_run:
        print("(DRY RUN — no files/DB will be modified)")
    print()

    recovered, still_missing = asyncio.run(
        retry_all(targets, args.delay, args.dry_run)
    )

    print()
    print(f"Done: {recovered} recovered, {still_missing} still missing "
          f"(of {total} retried).")
    if recovered and not args.dry_run:
        print("Tip: re-run scripts/backfill_has_transcript.py to reconcile DB counts.")


if __name__ == "__main__":
    main()
