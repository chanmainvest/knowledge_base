"""Test scrape for a single HKEJ author (default: 李聲揚)."""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from kb.db import engine
from kb.scrapers.hkej import HKEJScraper


async def main(handle: str = "李聲揚", limit: int = 3) -> None:
    with engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.handle, c.name, c.url, c.metadata FROM channel c "
                "JOIN source s ON c.source_id=s.id "
                "WHERE s.code='hkej' AND c.handle=:h"
            ),
            {"h": handle},
        ).fetchone()
    if not row:
        print(f"Author not found: {handle!r}")
        sys.exit(1)

    h, name, url, metadata = row
    author = {"slug": h, "name": name, "url": url, "metadata": dict(metadata or {})}
    print(f"Author: {name} | discovery: {url}")

    sc = HKEJScraper()
    async with sc._browser_session() as page:
        arts = await sc._list_articles_from_search(page, author, limit)
        print(f"Discovered {len(arts)} articles")
        for i, a in enumerate(arts, 1):
            print(f"  [{i}] {a['external_id']} | {a.get('published_at')} | {a['title'][:70]}")

        for i, a in enumerate(arts[:limit], 1):
            item = await sc._fetch_with_page(page, a)
            if item is None:
                print(f"[{i}] FETCH FAILED")
                continue
            path = sc.write_md(item)
            lines = item.body_md.split("\n")
            content = "\n".join(lines[3:]).strip() if len(lines) > 3 else item.body_md
            print("---")
            print(f"[{i}] author_name: {item.channel_name}")
            print(f"    title:       {item.title}")
            print(f"    date:        {item.published_at}")
            print(f"    url:         {item.url}")
            print(f"    saved:       {path}")
            print(f"    content_len: {len(content)} chars")
            preview = content[:300].replace("\n", " ")
            print(f"    preview:     {preview}...")
            if len(content) < 200:
                print("    [WARN] content looks short — may still be excerpt")


if __name__ == "__main__":
    handle = sys.argv[1] if len(sys.argv) > 1 else "李聲揚"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    asyncio.run(main(handle, n))
