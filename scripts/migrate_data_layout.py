"""Migrate data/ from per-article-folder layout to flat-file layout.

Old layout
----------
  data/hkej/<author>/<date>-<title>/
      content.md  ,  raw.html

  data/macrovoices/macrovoices/<date>__<ep_id>/
      content.md  ,  raw.html  [, slides.pdf, transcript.pdf, transcript.txt]

  data/youtube/<channel>/<date>__<video_id>/
      content.md

New layout
----------
  data/hkej/<author>/<year>/<date>-<title>.md
  data/raw/hkej/<author>/<year>/<date>-<title>.html

  data/macrovoices/<year>/<date>-<ep_id>.md
  data/raw/macrovoices/<year>/<date>-<ep_id>.html  [, .slides.pdf, .transcript.*]

  data/youtube/<channel>/<year>/<date>-<title>.md

Run
---
  uv run python scripts/migrate_data_layout.py [--dry-run] [hkej] [macrovoices] [youtube]

After migration, re-index the DB:
  uv run kb ingest
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

# Force UTF-8 output on Windows (Chinese filenames in data paths)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(__file__).parent.parent / "data"

_CONTENT = "content.md"
_RAW_HTML = "raw.html"
_EXTRA_SUFFIXES = {
    "slides.pdf": ".slides.pdf",
    "transcript.pdf": ".transcript.pdf",
    "transcript.txt": ".transcript.txt",
}


# helpers

def _slugify(s: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:maxlen].strip("-") or "x"


def _year_from_folder(name: str) -> str:
    if name.startswith("undated"):
        return "undated"
    return name[:4]


def _read_front_matter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        return yaml.safe_load(m.group(1)) if m else {}
    except Exception:
        return {}


def _mv(src: Path, dst: Path, dry: bool) -> None:
    if src == dst:
        return
    if dry:
        print(f"  MOVE  {src.relative_to(DATA_DIR)}  ->  {dst.relative_to(DATA_DIR)}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _cleanup_dir(d: Path, dry: bool) -> None:
    if not d.exists():
        return
    remaining = list(d.iterdir())
    if not remaining:
        if not dry:
            d.rmdir()
            print(f"  RMDIR {d.relative_to(DATA_DIR)}")
    else:
        print(f"  WARN  leftover in {d.relative_to(DATA_DIR)}: {[f.name for f in remaining]}")


# per-source migrations

def migrate_hkej(dry: bool) -> int:
    hkej_dir = DATA_DIR / "hkej"
    if not hkej_dir.exists():
        print("data/hkej/ not found -- skipping")
        return 0

    moved = 0
    for author_dir in sorted(hkej_dir.iterdir()):
        if not author_dir.is_dir() or author_dir.name.startswith("."):
            continue
        for article_dir in sorted(author_dir.iterdir()):
            if not article_dir.is_dir():
                continue
            folder = article_dir.name
            year = _year_from_folder(folder)

            src_md = article_dir / _CONTENT
            if src_md.exists():
                _mv(src_md, hkej_dir / author_dir.name / year / f"{folder}.md", dry)
                moved += 1

            src_html = article_dir / _RAW_HTML
            if src_html.exists():
                _mv(src_html,
                    DATA_DIR / "raw" / "hkej" / author_dir.name / year / f"{folder}.html",
                    dry)
                moved += 1

            _cleanup_dir(article_dir, dry)

    return moved


def migrate_macrovoices(dry: bool) -> int:
    mv_src = DATA_DIR / "macrovoices" / "macrovoices"
    mv_dst = DATA_DIR / "macrovoices"
    if not mv_src.exists():
        print("data/macrovoices/macrovoices/ not found -- skipping")
        return 0

    moved = 0
    for article_dir in sorted(mv_src.iterdir()):
        if not article_dir.is_dir():
            continue
        folder = article_dir.name
        year = _year_from_folder(folder)
        stem = folder.replace("__", "-", 1)

        src_md = article_dir / _CONTENT
        if src_md.exists():
            _mv(src_md, mv_dst / year / f"{stem}.md", dry)
            moved += 1

        src_html = article_dir / _RAW_HTML
        if src_html.exists():
            _mv(src_html, DATA_DIR / "raw" / "macrovoices" / year / f"{stem}.html", dry)
            moved += 1

        for old_name, new_suffix in _EXTRA_SUFFIXES.items():
            src_extra = article_dir / old_name
            if src_extra.exists():
                _mv(src_extra,
                    DATA_DIR / "raw" / "macrovoices" / year / f"{stem}{new_suffix}",
                    dry)
                moved += 1

        _cleanup_dir(article_dir, dry)

    if mv_src.exists() and not any(mv_src.iterdir()):
        if not dry:
            mv_src.rmdir()
            print(f"  RMDIR {mv_src.relative_to(DATA_DIR)}")

    return moved


def migrate_youtube(dry: bool) -> int:
    """Filename uses the article title from YAML front matter (matches scraper output)."""
    yt_dir = DATA_DIR / "youtube"
    if not yt_dir.exists():
        print("data/youtube/ not found -- skipping")
        return 0

    moved = 0
    for ch_dir in sorted(yt_dir.iterdir()):
        if not ch_dir.is_dir() or ch_dir.name.startswith("."):
            continue
        for article_dir in sorted(ch_dir.iterdir()):
            if not article_dir.is_dir():
                continue
            src_md = article_dir / _CONTENT
            if not src_md.exists():
                continue

            fm = _read_front_matter(src_md)

            pub = fm.get("published_at")
            if pub:
                pub_s = pub if isinstance(pub, str) else pub.isoformat()
                date_part, year = pub_s[:10], pub_s[:4]
            else:
                n = article_dir.name
                date_part = n[:10] if len(n) >= 10 else "undated"
                year = n[:4] if (len(n) >= 4 and n[:4].isdigit()) else "undated"

            title = fm.get("title") or article_dir.name
            stem = f"{date_part}-{_slugify(title, 80)}"

            _mv(src_md, yt_dir / ch_dir.name / year / f"{stem}.md", dry)
            moved += 1

            _cleanup_dir(article_dir, dry)

    return moved


# main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without moving any files")
    parser.add_argument("sources", nargs="*",
                        choices=["hkej", "macrovoices", "youtube"],
                        help="Sources to migrate (default: all)")
    args = parser.parse_args()

    dry = args.dry_run
    sources = set(args.sources) if args.sources else {"hkej", "macrovoices", "youtube"}

    if dry:
        print("DRY RUN -- no files will be moved\n")

    totals: dict[str, int] = {}

    if "hkej" in sources:
        print("=== Migrating data/hkej/ ===")
        totals["hkej"] = migrate_hkej(dry)

    if "macrovoices" in sources:
        print("\n=== Migrating data/macrovoices/ ===")
        totals["macrovoices"] = migrate_macrovoices(dry)

    if "youtube" in sources:
        print("\n=== Migrating data/youtube/ ===")
        totals["youtube"] = migrate_youtube(dry)

    total = sum(totals.values())
    detail = ", ".join(f"{n} {src}" for src, n in totals.items())
    print(f"\n{'Would move' if dry else 'Moved'} {total} files ({detail})")

    if not dry and total > 0:
        print("\nRun 'uv run kb ingest' to re-index the database with the new paths.")


if __name__ == "__main__":
    main()
