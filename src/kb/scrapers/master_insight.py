"""Master Insight scraper.

Discovers and fetches articles from master-insight.com for registered authors.
"""
from __future__ import annotations

import re
import json
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md_of

from ..config import DATA_DIR
from ..io_md import slugify
from .base import BaseScraper, ScrapedItem
from ..db import engine
from sqlalchemy import text


class MasterInsightScraper(BaseScraper):
    code = "master-insight"
    name = "Master Insight"

    def __init__(self) -> None:
        super().__init__()
        self.headers["Accept-Language"] = "zh-HK,zh;q=0.9,en;q=0.8"

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            timeout=60.0,
            http2=True,
            verify=False,
        )

    def _author_page_url(self, slug: str, page: int = 1) -> str:
        return f"https://www.master-insight.com/author/{slug}?page={page}"

    def already_scraped(self, d: dict) -> bool:
        ext_id = str(d.get("external_id", ""))
        author = d.get("author") or {}
        author_slug = slugify(author.get("slug") or "unknown", 80)
        author_dir = DATA_DIR / self.code / author_slug
        if not author_dir.exists():
            return False
        for md_path in author_dir.glob("**/*.md"):
            if md_path.stat().st_size < 200:
                continue
            try:
                content = md_path.read_text(encoding="utf-8", errors="replace")
                if f"external_id: '{ext_id}'" in content or f'external_id: "{ext_id}"' in content:
                    return True
            except Exception:
                continue
        return False

    async def discover(
        self,
        limit: int | None = None,
        author_handle: str | None = None,
    ) -> AsyncIterator[dict]:
        async with await self._client() as client:
            if author_handle:
                with engine().connect() as conn:
                    name = conn.execute(
                        text("SELECT c.name FROM channel c JOIN source s ON c.source_id = s.id "
                             "WHERE s.code = 'master-insight' AND c.handle = :h"),
                        {"h": author_handle}
                    ).scalar_one_or_none()
                targets = {author_handle: name or author_handle}
            else:
                with engine().connect() as conn:
                    rows = conn.execute(
                        text("SELECT c.handle, c.name FROM channel c JOIN source s ON c.source_id = s.id "
                             "WHERE s.code = 'master-insight'")
                    ).fetchall()
                targets = {row[0]: row[1] for row in rows}

            n = 0
            for slug, author_name in sorted(targets.items()):
                page = 1
                while True:
                    url = self._author_page_url(slug, page)
                    self.log.info("Crawling author page: %s", url)
                    try:
                        r = await self.polite_get(client, url)
                    except Exception as exc:
                        self.log.exception("Failed to get author page %s: %s", url, exc)
                        break
                    
                    if r.status_code != 200:
                        self.log.warning("Author page %s returned status %d", url, r.status_code)
                        break

                    soup = BeautifulSoup(r.text, "lxml")
                    
                    top_h1 = soup.select_one(".author-top-box h1")
                    if top_h1:
                        author_name = top_h1.text.strip()
                    
                    articles = soup.select(".r2-box")
                    if not articles:
                        self.log.info("No articles found on page %d for %s", page, slug)
                        break

                    for article in articles:
                        title_a = article.select_one(".title a")
                        if not title_a:
                            continue
                        article_url = title_a.get("href")
                        if not article_url:
                            continue
                        
                        m = re.search(r"/article/(\d+)", article_url)
                        ext_id = m.group(1) if m else slugify(article_url)
                        
                        title = title_a.text.strip()
                        
                        published_at = None
                        author_div = article.select_one(".author")
                        if author_div:
                            date_match = re.search(r"\d{4}-\d{2}-\d{2}", author_div.text)
                            if date_match:
                                try:
                                    published_at = datetime.strptime(date_match.group(0), "%Y-%m-%d")
                                except ValueError:
                                    pass

                        yield {
                            "external_id": ext_id,
                            "url": article_url,
                            "title": title,
                            "published_at": published_at,
                            "author": {
                                "slug": slug,
                                "name": author_name,
                            }
                        }
                        n += 1
                        if limit and n >= limit:
                            return
                    
                    next_page = soup.find("a", string="下一頁")
                    if not next_page:
                        next_page = soup.find(lambda tag: tag.name == "a" and "下一頁" in tag.text)
                    
                    if not next_page:
                        self.log.info("No next page link found on page %d for %s", page, slug)
                        break
                    
                    page += 1

    async def fetch(self, d: dict) -> ScrapedItem | None:
        url = d["url"]
        async with await self._client() as client:
            try:
                r = await self.polite_get(client, url)
            except Exception as exc:
                self.log.exception("Failed to get article %s: %s", url, exc)
                return None
            
            if r.status_code >= 400:
                self.log.warning("Article fetch %s -> %s", url, r.status_code)
                return None
            html = r.text

        soup = BeautifulSoup(html, "lxml")
        title = (d.get("title") or "").strip()
        if not title:
            h1_el = soup.find("h1")
            title = h1_el.text.strip() if h1_el else "Untitled"

        published_at = d.get("published_at")
        
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if data.get("@type") == "NewsArticle" and data.get("datePublished"):
                    dt_str = data["datePublished"]
                    if dt_str.endswith("Z"):
                        dt_str = dt_str[:-1] + "+00:00"
                    published_at = datetime.fromisoformat(dt_str).replace(tzinfo=None)
                    break
            except Exception:
                continue

        body_el = soup.select_one(".post-body")
        if body_el:
            for junk in body_el.select("script, style, iframe"):
                junk.decompose()
            body_md = md_of(str(body_el), heading_style="ATX").strip()
        else:
            body_md = title

        author = d.get("author") or {}
        author_slug = slugify(author.get("slug") or "unknown", 80)
        date_part = published_at.strftime("%Y-%m-%d") if published_at else "undated"
        folder_name = f"{date_part}-{slugify(title, 80)}"

        return ScrapedItem(
            source=self.code,
            channel=author_slug,
            channel_name=author.get("name") or author.get("slug", ""),
            external_id=d["external_id"],
            title=title,
            url=url,
            published_at=published_at,
            body_md=body_md,
            raw_html=html,
            language="zh-Hant-HK",
            flat_layout=True,
            folder_name=folder_name,
            extra={
                "author_slug": author.get("slug"),
            },
        )

    async def run(
        self,
        limit: int | None = None,
        author_handle: str | None = None,
    ) -> list[Path]:
        if author_handle is None:
            with engine().connect() as conn:
                rows = conn.execute(
                    text("SELECT c.handle FROM channel c JOIN source s ON c.source_id = s.id "
                         "WHERE s.code = 'master-insight'")
                ).fetchall()
            out: list[Path] = []
            for row in sorted(rows):
                slug = row[0]
                paths = await self.run(limit=limit, author_handle=slug)
                out.extend(paths)
            return out

        out: list[Path] = []
        async for d in self.discover(limit=limit, author_handle=author_handle):
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
