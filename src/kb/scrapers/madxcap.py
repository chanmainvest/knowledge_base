"""MadX (狂徒投資) personal blog scraper.

Scrapes articles from https://madxcap.com/ by 狂徒.
The site has Dcard articles and Facebook posts organized by year.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md_of

from ..config import DATA_DIR
from ..io_md import slugify
from .base import BaseScraper, ScrapedItem


BASE = "https://madxcap.com"


def _parse_date(text: str) -> datetime | None:
    """Parse date from text like '2026-03-31' or '2026-03-31 20:00'."""
    if not text:
        return None
    # Try YYYY-MM-DD first
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    # Try YYYY/MM/DD
    m = re.search(r"(\d{4}/\d{2}/\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y/%m/%d")
        except ValueError:
            pass
    return None


class MadxcapScraper(BaseScraper):
    code = "madxcap"
    name = "MadX 狂徒投資"
    source_code = "blog"

    def __init__(self) -> None:
        super().__init__()
        self.headers["Accept-Language"] = "zh-TW,zh;q=0.9,en;q=0.8"
        self._discovered: dict[str, dict] = {}

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            timeout=60.0,
            http2=True,
        )

    async def discover(
        self,
        limit: int | None = None,
        *,
        source_type: str | None = None,
    ) -> AsyncIterator[dict]:
        """Discover articles from the homepage and yearly archives."""
        async with await self._client() as client:
            # Get homepage to find all article links
            await self.limiter.wait(BASE)
            r = await client.get(BASE)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            # Find all article links - Dcard and Facebook posts
            # Dcard: 261214748/  Facebook: fb_20260618_5893/
            article_links = soup.find_all("a", href=re.compile(r"^(?:/)?(?:\d+|fb_\d{8}_\d+)/"))

            seen: set[str] = set()
            n = 0

            for link in article_links:
                href = str(link.get("href", ""))
                if not href or href in seen:
                    continue
                seen.add(href)

                # Extract external_id from URL
                # Dcard: 261214748/ -> 261214748
                # Facebook: fb_20260618_5893/ -> fb_20260618_5893
                m = re.match(r"(?:/)?([^/]+)/", href)
                if not m:
                    continue
                ext_id = m.group(1)

                # Get title from link text
                title = link.get_text(strip=True)
                if not title:
                    # Try parent elements
                    parent = link.find_parent(["h3", "h4", "div", "li"])
                    if parent:
                        title = parent.get_text(strip=True)
                        # Clean up - remove date prefix if present
                        title = re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", title)
                        title = re.sub(r"^D\s*|^F\s*", "", title).strip()

                # Try to extract date from link text or nearby
                published_at = None
                # Link text might contain date like "2026-03-31"
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", title)
                if date_match:
                    published_at = _parse_date(date_match.group(1))
                    title = title.replace(date_match.group(1), "").strip()

                # Also check sibling elements for date
                if not published_at:
                    next_sib = link.find_next_sibling(string=True)
                    if next_sib:
                        published_at = _parse_date(str(next_sib))

                url = urljoin(BASE, href)

                # Filter by source type if specified
                if source_type == "dcard" and not ext_id.isdigit():
                    continue
                if source_type == "facebook" and not ext_id.startswith("fb_"):
                    continue

                yield {
                    "external_id": ext_id,
                    "url": url,
                    "title": title or ext_id,
                    "published_at": published_at,
                }

                n += 1
                if limit and n >= limit:
                    return

    def already_scraped(self, d: dict) -> bool:
        ext_id = str(d.get("external_id", ""))
        author_dir = DATA_DIR / self.effective_source_code / slugify("狂徒", 80)
        if not author_dir.exists():
            return False
        for md_path in author_dir.glob("*/*.md"):
            if md_path.stat().st_size < 200:
                continue
            text = md_path.read_text(encoding="utf-8", errors="replace")
            if f"external_id: '{ext_id}'" in text or f'external_id: "{ext_id}"' in text:
                return True
        return False

    async def fetch(self, d: dict) -> ScrapedItem | None:
        url = d["url"]
        async with await self._client() as client:
            await self.limiter.wait(url)
            r = await client.get(url)
            if r.status_code >= 400:
                self.log.warning("article fetch %s -> %s", url, r.status_code)
                return None
            html = r.text

        soup = BeautifulSoup(html, "lxml")

        # Extract title
        title = (d.get("title") or "").strip()
        if not title:
            h1_el = soup.select_one("h1")
            title = h1_el.get_text(strip=True) if h1_el else d.get("external_id", "Untitled")

        # Extract published date from page
        published_at = d.get("published_at")
        if not published_at:
            # Look for date in meta tags or page content
            meta_date = soup.find("meta", attrs={"property": "article:published_time"})
            if meta_date:
                content = meta_date.get("content")
                if isinstance(content, str):
                    published_at = _parse_date(content)
            if not published_at:
                time_el = soup.find("time")
                if time_el:
                    datetime_attr = time_el.get("datetime")
                    if isinstance(datetime_attr, str):
                        published_at = _parse_date(datetime_attr)
            if not published_at:
                # Try to find date in page text
                page_text = soup.get_text(" ", strip=True)
                published_at = _parse_date(page_text)

        # Extract article body
        body_el = soup.select_one("article") or soup.select_one(".post-content") or soup.select_one("main") or soup.select_one(".post-body") or soup.select_one(".content") or soup.select_one("#content")
        if not body_el:
            # Fallback: try to find content area
            body_el = soup.select_one(".content") or soup.select_one("#content")

        if body_el:
            # Remove navigation, footer, related articles
            for junk in body_el.select("nav, footer, aside, .related, .navigation, script, style"):
                junk.decompose()
            # Remove "back to home" link at top
            for a in body_el.select('a[href="/"]'):
                if a.get_text(strip=True) in ("← 狂徒投資", "狂徒投資", "首頁"):
                    a.decompose()
            body_md = md_of(str(body_el), heading_style="ATX").strip()
        else:
            body_md = title

        # Clean up body - remove duplicate title if present at start
        lines = body_md.splitlines()
        if lines and lines[0].startswith("#"):
            first_heading = lines[0].lstrip("# ").strip()
            if first_heading == title:
                body_md = "\n".join(lines[1:]).strip()

        # Extract tags from page
        tags = []
        for tag_link in soup.select('a[href^="/tag/"], a[href^="/series/"]'):
            tag_text = tag_link.get_text(strip=True)
            if tag_text:
                tags.append(tag_text)

        # Determine content type (Dcard vs Facebook)
        ext_id = d["external_id"]
        content_type = "dcard" if ext_id.isdigit() else "facebook"

        date_part = published_at.strftime("%Y-%m-%d") if published_at else "undated"
        folder_name = f"{date_part}-{slugify(title, 80)}"

        return ScrapedItem(
            source=self.effective_source_code,
            channel="狂徒",
            channel_name="狂徒投資",
            external_id=ext_id,
            title=title,
            url=url,
            published_at=published_at,
            body_md=body_md,
            raw_html=html,
            language="zh-TW",
            flat_layout=True,
            folder_name=folder_name,
            extra={
                "content_type": content_type,
                "tags": tags,
            },
        )

    async def run(
        self,
        limit: int | None = None,
        *,
        source_type: str | None = None,
    ) -> list[Path]:
        out: list[Path] = []
        async for d in self.discover(limit=limit, source_type=source_type):
            if self.already_scraped(d):
                self.log.info("skip (cached) %s", d.get("url") or d.get("external_id"))
                continue
            try:
                item = await self.fetch(d)
            except Exception as exc:
                self.log.exception("fetch failed: %s :: %s", d, exc)
                continue
            if item is None:
                continue
            p = self.write_md(item)
            out.append(p)
            if limit and len(out) >= limit:
                break
        return out