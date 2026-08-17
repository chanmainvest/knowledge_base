#!/usr/bin/env python3
"""DB <-> data/ consistency audit.

Checks that the `item` table and the data/ markdown tree agree:

- every DB row's ``md_path`` exists on disk and contains the row's
  ``external_id`` in its front-matter;
- every data/ markdown file has a DB row (no orphans);
- no duplicate (source, external_id) rows; no duplicate files per item.

Usage::

    uv run python scripts/check_db_data_consistency.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kb.config import DATA_DIR  # noqa: E402
from kb.db import engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

# external_id, tolerating YAML quoting of numeric-looking ids ('14275')
EXT_RE = re.compile(r"^external_id:\s*('([^']*)'|\"([^\"]*)\"|(\S+))", re.M)


def read_eid(path: Path) -> str | None:
    try:
        m = EXT_RE.search(path.read_text(encoding="utf-8", errors="replace")[:2500])
    except OSError:
        return None
    if not m:
        return None
    return next(g for g in m.groups()[1:] if g is not None)


def main() -> None:
    conn = engine().connect()

    # --- DB side: one row per (source, external_id) with its md_path ------
    rows = conn.execute(text(
        "SELECT s.code, i.external_id, i.md_path FROM item i "
        "JOIN source s ON i.source_id = s.id")).fetchall()
    db = {(code, eid): mp for code, eid, mp in rows}
    print(f"DB items: {len(rows)}")
    dup_db = [k for k, n in Counter((c, e) for c, e, _ in rows).items() if n > 1]
    print(f"duplicate (source, external_id) rows in DB: {len(dup_db)}")
    conn.close()

    # --- disk side: every md file (skip data/raw/ and READMEs) ------------
    disk: dict = {}
    by_path_bad = []
    no_row = []
    dup_files = []
    for md in DATA_DIR.rglob("*.md"):
        rel = md.relative_to(DATA_DIR)
        if md.name == "README.md" or rel.parts[0] == "raw":
            continue
        eid = read_eid(md)
        if not eid:
            by_path_bad.append(md)
            continue
        key = (rel.parts[0], eid)
        if key in disk:
            dup_files.append((md, disk[key]))
            continue
        disk[key] = md
        if key not in db:
            no_row.append(md)
    print(f"disk md files: {len(disk)} "
          f"(unreadable front-matter: {len(by_path_bad)}, "
          f"duplicate items on disk: {len(dup_files)})")

    # --- cross checks ------------------------------------------------------
    missing_file = [(k, mp) for k, mp in db.items() if not mp or not Path(mp).exists()]
    print(f"\nDB rows whose md_path file is missing on disk: {len(missing_file)}")
    for k, mp in missing_file[:8]:
        print("   ", k, "->", mp)

    mismatch = []
    for k, mp in db.items():
        if not mp or not Path(mp).exists():
            continue
        e = read_eid(Path(mp))
        if not e or (k[0], e) != k:
            mismatch.append((k, mp))
    print(f"md_path files whose external_id disagrees with the DB row: {len(mismatch)}")
    for k, mp in mismatch[:8]:
        print("   ", k, "->", mp)

    print(f"disk files with no DB row: {len(no_row)}")
    for md in no_row[:8]:
        print("   ", md.relative_to(DATA_DIR))

    src_db = Counter(c for c, _ in db)
    src_disk = Counter(c for c, _ in disk)
    print(f"\n{'source':16s} {'db':>7s} {'disk':>7s} {'miss-file':>9s} {'no-row':>7s}")
    for c in sorted(set(src_db) | set(src_disk)):
        n_miss = sum(1 for (s, _), __ in missing_file if s == c)
        n_norow = sum(1 for md in no_row if md.relative_to(DATA_DIR).parts[0] == c)
        print(f"{c:16s} {src_db.get(c, 0):>7d} {src_disk.get(c, 0):>7d} "
              f"{n_miss:>9d} {n_norow:>7d}")


if __name__ == "__main__":
    main()
