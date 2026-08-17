#!/usr/bin/env python3
"""Deduplicate data/youtube/ markdown files (folder drift + undated copies).

Two duplication modes are cleaned up, both caused by the item's path being
computed differently between runs:

1. **Cross-folder** — YouTube channel folders are named from the slugified
   channel display name. When a channel's display name changed between scrape
   runs (e.g. ``MacroVoices`` → ``Macro Voices``), the next run could no
   longer see the old folder in its already-scraped check and re-downloaded
   the entire channel into a new folder. The duplicate pair is merged into
   the canonical folder (the slug of the channel's *current* DB display
   name) and the old folder is removed.
2. **Same-folder** — a video first scraped without a resolved upload date
   lands at ``<ch>/undated/undated-<title>.md``; a later run that resolved
   the date writes ``<ch>/<year>/<date>-<title>.md``. The better copy is
   kept (transcript first, then recency, then the dated path), the other
   deleted, and an undated winner is promoted to the dated path.

Finally, items whose ``md_path`` no longer exists on disk (e.g. legacy
``<date>__<id>/content.md`` layout rows) are re-pointed by re-ingesting the
surviving file for their ``external_id``.

Idempotent: re-running finds nothing to do. Inspect with ``--dry-run``.

Usage::

    uv run python scripts/fix_youtube_dup_folders.py --dry-run
    uv run python scripts/fix_youtube_dup_folders.py
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kb.config import DATA_DIR  # noqa: E402
from kb.db import engine  # noqa: E402
from kb.ingest import ingest_file  # noqa: E402
from kb.io_md import load_md, slugify  # noqa: E402
from sqlalchemy import text  # noqa: E402

_EXT_RE = re.compile(r"^external_id:\s*(\S+)", re.M)


def _channel_slugs() -> dict[str, str]:
    """handle → slug of the channel's current DB display name."""
    with engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT c.handle, c.name FROM channel c "
            "JOIN source s ON c.source_id = s.id WHERE s.code = 'youtube'"
        )).fetchall()
    return {h: slugify(n) for h, n in rows}


def _scan(yt_root: Path) -> dict[str, list[Path]]:
    """external_id → [md paths] for every video markdown file."""
    vids: dict[str, list[Path]] = defaultdict(list)
    for md in yt_root.rglob("*.md"):
        if md.name == "README.md":
            continue
        try:
            m = _EXT_RE.search(md.read_text(encoding="utf-8")[:2000])
        except OSError:
            continue
        if m:
            vids[m.group(1)].append(md)
    return vids


def _rank(path: Path, prefer: bool = False) -> tuple:
    """Rank a copy: transcript presence, then recency, then dated over
    undated (so the canonical dated path wins ties), then ``prefer``."""
    try:
        front = load_md(path).front
    except Exception:  # noqa: BLE001
        return (0, "", 0, 0)
    has_tx = 1 if front.get("has_transcript") else 0
    is_dated = 0 if "undated" in path.parts else 1
    return (has_tx, str(front.get("scraped_at") or ""), is_dated, int(prefer))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would happen without touching anything")
    args = ap.parse_args()

    yt_root = DATA_DIR / "youtube"
    canonical_slugs = set(_channel_slugs().values())
    vids = _scan(yt_root)
    reingest: set[Path] = set()

    # ---- Phase 1: cross-folder duplicates -------------------------------
    by_folders: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for vid, paths in vids.items():
        folders = tuple(sorted({p.relative_to(yt_root).parts[0] for p in paths}))
        if len(folders) > 1:
            by_folders[folders].append(vid)

    n_deleted = n_moved = 0
    for pair, vid_list in sorted(by_folders.items()):
        canonical = next((f for f in pair if f in canonical_slugs), None)
        if canonical is None:
            print(f"!! no canonical folder among {pair} — skipping group")
            continue
        old = [f for f in pair if f != canonical]
        print(f"\n{pair}: {len(vid_list)} duplicated video(s) "
              f"→ keep {canonical!r}, merge+remove {old}")

        for vid in vid_list:
            copies = vids[vid]
            winner = max(copies, key=lambda p: _rank(p, p.relative_to(yt_root).parts[0] == canonical))
            reingest.add(winner if winner.relative_to(yt_root).parts[0] == canonical else None)
            for path in copies:
                if path == winner:
                    continue
                if not args.dry_run:
                    path.unlink()
                n_deleted += 1
            if winner.relative_to(yt_root).parts[0] != canonical:
                rel = winner.relative_to(yt_root / winner.relative_to(yt_root).parts[0])
                target = yt_root / canonical / rel
                if not args.dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(winner), str(target))
                reingest.add(target)
                n_moved += 1
            vids[vid] = [yt_root / canonical / winner.relative_to(yt_root)[1:]] \
                if winner.relative_to(yt_root).parts[0] != canonical else [winner]

        # Move files that exist ONLY in an old folder into the canonical one,
        # then remove the emptied old folder (only READMEs / empty dirs left).
        for folder in old:
            src_root = yt_root / folder
            for md in sorted(src_root.rglob("*.md")):
                if md.name == "README.md":
                    continue
                m = _EXT_RE.search(md.read_text(encoding="utf-8")[:2000])
                vid = m.group(1) if m else None
                if vid and any(p.relative_to(yt_root).parts[0] == canonical
                               for p in vids.get(vid, [])):
                    continue  # canonical already has this video
                rel = md.relative_to(src_root)
                target = yt_root / canonical / rel
                if not args.dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(md), str(target))
                reingest.add(target)
                n_moved += 1
                if vid and vid in vids:
                    vids[vid].append(target)
            if not args.dry_run:
                for p in sorted(src_root.rglob("*"), reverse=True):
                    if p.is_file():
                        p.unlink()
                    elif p.is_dir():
                        p.rmdir()
                src_root.rmdir()
            print(f"  removed folder: {folder}")

    # ---- Phase 2: same-folder duplicates (undated vs dated) -------------
    same_dup = {v: ps for v, ps in vids.items()
                if len(ps) > 1 and len({p.relative_to(yt_root).parts[0] for p in ps}) == 1}
    n_promoted = 0
    if same_dup:
        print(f"\n{len(same_dup)} video(s) duplicated within one folder "
              f"(undated vs dated)")
    for vid, copies in sorted(same_dup.items()):
        winner = max(copies, key=_rank)
        dated_losers = [p for p in copies
                        if p != winner and "undated" not in p.parts]
        for path in copies:
            if path == winner:
                continue
            if not args.dry_run:
                path.unlink()
            n_deleted += 1
        # Promote an undated winner to the dated path so future path
        # computations (and humans) find it where expected.
        if "undated" in winner.parts and dated_losers:
            target = dated_losers[0]
            if not args.dry_run:
                shutil.move(str(winner), str(target))
            winner = target
            n_promoted += 1
        reingest.add(winner)
        vids[vid] = [winner]

    print(f"\nPhase 1+2 summary: {n_deleted} duplicate file(s) deleted, "
          f"{n_moved} moved across folders, {n_promoted} undated file(s) "
          f"promoted to dated paths")

    if args.dry_run:
        print("(dry run — nothing written, no re-ingest)")
        return

    # ---- Phase 3: re-ingest + reconcile stale md_path --------------------
    on_disk = {v: ps[0] for v, ps in vids.items() if ps}

    n_ing = 0
    for md in sorted(p for p in reingest if p and p.is_file()):
        try:
            if ingest_file(md):
                n_ing += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  !! ingest failed {md}: {exc}")
    print(f"Re-ingested {n_ing} markdown file(s)")

    n_rep = 0
    with engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT i.external_id, i.md_path FROM item i "
            "JOIN source s ON i.source_id = s.id WHERE s.code = 'youtube'"
        )).fetchall()
    for eid, mp in rows:
        if mp and Path(mp).exists():
            continue
        target = on_disk.get(eid)
        if target:
            try:
                ingest_file(target)
                n_rep += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  !! re-point failed {eid}: {exc}")
        else:
            print(f"  !! item {eid} has no file on disk (md_path was {mp})")
    print(f"Re-pointed {n_rep} item(s) whose md_path was stale")


if __name__ == "__main__":
    main()
