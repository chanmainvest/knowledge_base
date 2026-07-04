"""One-shot migration: consolidate macrovoices + madxcap under data/blog/.

Moves the on-disk data from the old separate-source layout::

    data/macrovoices/<YYYY>/<date>-<slug>.md
    data/madxcap/狂徒/<YYYY>/<date>-<slug>.md
    data/raw/macrovoices/<YYYY>/...
    data/raw/madxcap/狂徒/<YYYY>/...

to the consolidated blog layout::

    data/blog/macrovoices/<YYYY>/<date>-<slug>.md
    data/blog/狂徒/<YYYY>/<date>-<slug>.md
    data/raw/blog/macrovoices/<YYYY>/...
    data/raw/blog/狂徒/<YYYY>/...

and rewrites the ``source:`` front-matter field in every affected markdown
file from ``macrovoices``/``madxcap`` to ``blog``.

Usage::

    uv run python scripts/migrate_to_blog_source.py --dry-run
    uv run python scripts/migrate_to_blog_source.py
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import yaml

# Resolve DATA_DIR the same way the app does.
from kb.config import DATA_DIR

# Old source dir → new channel slug under data/blog/
SITE_MAP = {
    "macrovoices": "macrovoices",       # data/blog/macrovoices/...
    "madxcap":     "狂徒",               # data/blog/狂徒/...
}


def _rewrite_source_front_matter(md_path: Path, old: str, new: str) -> bool:
    """Rewrite the `source:` value in YAML front-matter. Returns True if changed."""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return False
    fm_text = m.group(1)
    fm = yaml.safe_load(fm_text) or {}
    if fm.get("source") != old:
        return False
    fm["source"] = new
    new_fm = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    new_text = f"---\n{new_fm}\n---\n{m.group(2)}"
    md_path.write_text(new_text, encoding="utf-8")
    return True


def _move_tree(src: Path, dst: Path, dry: bool) -> int:
    """Move src dir into dst (dst must not exist). Returns 1 if moved."""
    if not src.exists() or dst.exists():
        return 0
    print(f"  move: {src.relative_to(DATA_DIR)} -> {dst.relative_to(DATA_DIR)}")
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return 1


def migrate(dry: bool) -> dict[str, int]:
    """Move data folders + rewrite front-matter. Returns per-site counts."""
    moved_dirs = 0
    rewritten_md = 0
    errors = 0

    for old_source, channel_slug in SITE_MAP.items():
        print(f"\n=== Migrating data/{old_source}/ -> data/blog/{channel_slug}/ ===")

        # --- Content: data/<old_source>/ -> data/blog/<channel>/ ---
        old_content = DATA_DIR / old_source
        new_content = DATA_DIR / "blog" / channel_slug

        if old_content.exists() and not new_content.exists():
            moved_dirs += _move_tree(old_content, new_content, dry)
        elif old_content.exists() and new_content.exists():
            # Merge: move year subdirs one by one.
            print(f"  merge: {old_content.relative_to(DATA_DIR)} into {new_content.relative_to(DATA_DIR)}")
            for child in sorted(old_content.iterdir()):
                dst_child = new_content / child.name
                if child.is_dir() and not dst_child.exists():
                    moved_dirs += _move_tree(child, dst_child, dry)
                elif child.is_file():
                    # e.g. README.md — copy if missing.
                    if not dst_child.exists() and not dry:
                        dst_child.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(child), str(dst_child))
                    print(f"    copy: {child.name}")

        # --- Raw HTML: data/raw/<old_source>/ -> data/raw/blog/<channel>/ ---
        old_raw = DATA_DIR / "raw" / old_source
        new_raw = DATA_DIR / "raw" / "blog" / channel_slug
        if old_raw.exists() and not new_raw.exists():
            moved_dirs += _move_tree(old_raw, new_raw, dry)
        elif old_raw.exists() and new_raw.exists():
            print(f"  merge raw: {old_raw.relative_to(DATA_DIR)} into {new_raw.relative_to(DATA_DIR)}")
            for child in sorted(old_raw.iterdir()):
                dst_child = new_raw / child.name
                if child.is_dir() and not dst_child.exists():
                    moved_dirs += _move_tree(child, dst_child, dry)

    # --- Rewrite `source:` front-matter in all data/blog/**/*.md ---
    print("\n=== Rewriting front-matter source: -> blog ===")
    blog_dir = DATA_DIR / "blog"
    if blog_dir.exists():
        for md_path in blog_dir.rglob("*.md"):
            try:
                if _rewrite_source_front_matter(md_path, old="macrovoices", new="blog"):
                    rewritten_md += 1
                    print(f"  rewrote: {md_path.relative_to(DATA_DIR)}")
                    continue
                if _rewrite_source_front_matter(md_path, old="madxcap", new="blog"):
                    rewritten_md += 1
                    print(f"  rewrote: {md_path.relative_to(DATA_DIR)}")
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  ERROR: {md_path}: {exc}")
    else:
        print("  (data/blog/ not found — nothing to rewrite)")

    return {
        "moved_dirs": moved_dirs,
        "rewritten_md": rewritten_md,
        "errors": errors,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Show moves/rewrites without doing them")
    args = ap.parse_args()

    if args.dry_run:
        print("[DRY RUN — no files will be moved or changed]\n")

    stats = migrate(dry=args.dry_run)
    print(f"\n[bold]Done.[/bold] moved_dirs={stats['moved_dirs']} "
          f"rewritten_md={stats['rewritten_md']} errors={stats['errors']}")
    if not args.dry_run and stats["errors"] == 0:
        print("\nNext steps:")
        print("  1. uv run kb db migrate          # apply init.sql (creates blog source)")
        print("  2. uv run kb ingest              # re-ingest with new paths")
        print("  OR run migrations/006_consolidate_blog.sql against an existing DB")


if __name__ == "__main__":
    main()