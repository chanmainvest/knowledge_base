"""Fix Yahoo HK columnist articles saved with the generic 雅虎香港財經 title.

Renames markdown + raw HTML to the real headline, trims boilerplate from the
body, and re-ingests each item into Postgres. Safe to re-run; skips files that
no longer match the old filename pattern.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from kb.config import DATA_DIR
from kb.ingest import ingest_file
from kb.io_md import load_md, slugify
from kb.logging_setup import get_logger
from kb.scrapers.yahoohk import _trim_columnist_boilerplate

log = get_logger("fix_yahoohk_titles")
OLD_STEM_SUFFIX = "雅虎香港財經"


def _raw_html_for(md_path: Path) -> Path:
    rel = md_path.relative_to(DATA_DIR)
    return DATA_DIR / "raw" / rel.with_suffix(".html")


def _date_part(front: dict, md_path: Path) -> str:
    pub = front.get("published_at")
    if pub:
        return str(pub)[:10]
    stem = md_path.stem
    if len(stem) >= 10 and stem[4] == "-" and stem[7] == "-":
        return stem[:10]
    return "undated"


def _target_md_path(md_path: Path, title: str, date_part: str, external_id: str) -> Path:
    stem = f"{date_part}-{slugify(title, 80)}"
    target = md_path.with_name(f"{stem}.md")
    if target == md_path or not target.exists():
        return target
    return md_path.with_name(f"{stem}-{slugify(external_id, 12)}.md")


def fix_file(md_path: Path, *, dry_run: bool = False) -> Path | None:
    doc = load_md(md_path)
    body, title = _trim_columnist_boilerplate(doc.body)
    if not title:
        log.warning("skip (no headline): %s", md_path)
        return None

    external_id = str(doc.front.get("external_id") or "")
    date_part = _date_part(doc.front, md_path)
    new_md = _target_md_path(md_path, title, date_part, external_id)
    old_raw = _raw_html_for(md_path)
    new_raw = _raw_html_for(new_md)

    doc.front["title"] = title
    doc.body = body

    if dry_run:
        log.info("would fix %s -> %s (%s)", md_path.name, new_md.name, title)
        return new_md

    doc.write(new_md)
    if new_md != md_path and md_path.exists():
        md_path.unlink()

    if old_raw.exists() and old_raw != new_raw:
        new_raw.parent.mkdir(parents=True, exist_ok=True)
        if new_raw.exists():
            old_raw.unlink()
        else:
            old_raw.rename(new_raw)

    item_id = ingest_file(new_md)
    log.info("fixed %s -> %s (item_id=%s)", md_path.name, new_md.name, item_id)
    return new_md


def iter_old_files() -> list[Path]:
    yahoohk = DATA_DIR / "yahoohk"
    if not yahoohk.exists():
        return []
    return sorted(
        p for p in yahoohk.rglob("*.md")
        if p.stem.endswith(OLD_STEM_SUFFIX)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    paths = iter_old_files()
    if not paths:
        log.info("no files to fix")
        return

    fixed = 0
    for md_path in paths:
        try:
            if fix_file(md_path, dry_run=args.dry_run):
                fixed += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("failed %s: %s", md_path, exc)

    log.info("%s %d file(s)", "would fix" if args.dry_run else "fixed", fixed)


if __name__ == "__main__":
    main()
