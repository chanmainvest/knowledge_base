"""Re-extract content from existing HKEJ raw HTML files.

Iterates all data/hkej/<author>/<date>__<id>/ folders that have raw.html but
empty (or header-only) content.md, and re-extracts using the fixed logic.

Usage:
    uv run python scripts/reextract_hkej.py [--dry-run]
"""
from __future__ import annotations
import re
import sys
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kb.config import DATA_DIR
from kb.io_md import load_md, MdDoc


def extract_body(html: str, author_name: str = "") -> tuple[str, datetime | None, str]:
    """Return (body_md, published_at, column_name) from raw HTML."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.find("h1") or soup.find("h2")

    # --- date ---
    published_at: datetime | None = None
    if title_el:
        container = title_el.parent
        for sib in container.children if container else []:
            if isinstance(sib, NavigableString) or sib is title_el:
                continue
            if sib.name == "p":
                txt = sib.get_text(strip=True)
                m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", txt)
                if m:
                    try:
                        published_at = datetime(int(m[1]), int(m[2]), int(m[3]))
                    except ValueError:
                        pass
                    break
    if published_at is None:
        m2 = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", html)
        if m2:
            try:
                published_at = datetime.fromisoformat(m2.group(1))
            except ValueError:
                pass
    if published_at is None:
        m3 = re.search(r"(20\d{2})/(\d{2})/(\d{2})", html)
        if m3:
            try:
                published_at = datetime(int(m3[1]), int(m3[2]), int(m3[3]))
            except ValueError:
                pass

    # --- body paragraphs ---
    body_paras: list[str] = []
    column_name = ""
    if title_el:
        container = title_el.parent
        # Extract column from p.info
        for sib in (container.children if container else []):
            if isinstance(sib, NavigableString):
                continue
            if sib.name == "p" and "info" in (sib.get("class") or []):
                info_txt = sib.get_text(strip=True)
                if author_name and info_txt.startswith(author_name):
                    column_name = info_txt[len(author_name):].strip()
                break

        # Collect paragraphs after h1
        past_h1 = False
        for child in list(container.children if container else []):
            if isinstance(child, NavigableString):
                continue
            if child is title_el:
                past_h1 = True
                continue
            if not past_h1:
                continue
            if child.name in ("script", "style"):
                continue
            child_classes = " ".join(child.get("class", []))
            if any(x in child_classes for x in ("thumb", "enlargeImg", "hkej_detail_thumb")):
                continue
            if child.name == "p":
                txt = child.get_text(strip=True)
                if txt and any(c > "\x7f" for c in txt) and txt not in ("（節錄）", "（完）"):
                    body_paras.append(txt)
            elif child.name in ("div", "section", "article"):
                for p in child.find_all("p"):
                    txt = p.get_text(strip=True)
                    if txt and any(c > "\x7f" for c in txt) and txt not in ("（節錄）", "（完）"):
                        body_paras.append(txt)

    return "\n\n".join(body_paras), published_at, column_name


def reextract_all(dry_run: bool = False) -> None:
    hkej_dir = DATA_DIR / "hkej"
    updated = skipped = no_raw = paywalled = 0

    for author_dir in sorted(hkej_dir.iterdir()):
        if not author_dir.is_dir() or author_dir.name.startswith("."):
            continue
        for article_dir in sorted(author_dir.iterdir()):
            if not article_dir.is_dir():
                continue
            raw_path = article_dir / "raw.html"
            md_path = article_dir / "content.md"
            if not raw_path.exists():
                no_raw += 1
                continue
            if not md_path.exists():
                skipped += 1
                continue

            doc = load_md(md_path)
            front = doc.front

            # Check if body is already non-trivial (more than just title + author line)
            body_lines = [l for l in doc.body.strip().splitlines() if l.strip()]
            # A proper article has more than 3 lines (title, blank, author, blank, content...)
            if len(body_lines) > 4:
                skipped += 1
                continue

            # Re-extract
            html = raw_path.read_text(encoding="utf-8", errors="replace")
            author_name = front.get("channel_name", "")
            body_md, published_at, column_name = extract_body(html, author_name)

            if not body_md.strip():
                paywalled += 1
                continue

            # Build new content
            title = front.get("title", "")
            header = f"# {title}\n\n*{author_name}*"
            if column_name:
                header += f" | {column_name}"
            new_body = f"{header}\n\n{body_md}".strip()

            # Update front matter
            if published_at and not front.get("published_at"):
                front["published_at"] = published_at.isoformat()
            if column_name and not front.get("extra", {}).get("column"):
                front.setdefault("extra", {})["column"] = column_name

            if dry_run:
                print(f"[DRY] {article_dir.relative_to(ROOT)}: {len(body_md)} chars, {column_name=}")
            else:
                MdDoc(front=front, body=new_body).write(md_path)
                print(f"[OK]  {article_dir.relative_to(ROOT)}: {len(body_md)} chars")
            updated += 1

    print(f"\nDone: {updated} updated, {skipped} skipped (already have content), "
          f"{paywalled} paywalled (no content), {no_raw} missing raw HTML")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    reextract_all(dry_run=dry_run)
