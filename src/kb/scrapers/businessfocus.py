"""BusinessFocus (businessfocus.io) columnist scraper.

Authors are registered as channels in the DB (``kb businessfocus add-author
<slug>``); each author's article index is paginated through the site's own
Nuxt backend (node_api) — the same endpoint the 查看更多 button calls — and
article bodies are read from the SSR'd article page.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md_of

from ..config import DATA_DIR
from ..db import engine
from ..io_md import slugify
from .base import BaseScraper, ScrapedItem
from sqlalchemy import text


BASE = "https://businessfocus.io"
# node_api requires a site-wide pageId param (Facebook page id from the
# site's fb:pages meta); businessfocus.io's is stable site config.
PAGE_ID = "635680996587830"
PAGE_SIZE = 12  # matches the site's own load-more batch size


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


class BusinessFocusScraper(BaseScraper):
    code = "businessfocus"
    name = "BusinessFocus"

    def __init__(self) -> None:
        super().__init__()
        self.headers["Accept-Language"] = "zh-HK,zh;q=0.9,en;q=0.8"
        self.headers["Referer"] = f"{BASE}/"
        # author dir -> set of scraped external_ids (front-matter scan, once)
        self._scraped_cache: dict[str, set[str]] = {}

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.headers, follow_redirects=True, timeout=60.0, http2=True,
        )

    # ---- authors -------------------------------------------------------------

    def _load_authors(self) -> dict[str, str]:
        """Return {handle: display_name} for registered BusinessFocus authors."""
        with engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.handle, c.name FROM channel c "
                    "JOIN source s ON c.source_id = s.id "
                    "WHERE s.code = 'businessfocus' ORDER BY c.handle"
                )
            ).all()
        return {h: n for h, n in rows}

    async def resolve_blogger(self, client: httpx.AsyncClient, slug: str) -> dict | None:
        """Return the node_api author object for *slug* (numeric id lives here)."""
        url = f"{BASE}/node_api/v1/authors/blogger/{slug}"
        r = await self.polite_get(client, url, params={"pageId": PAGE_ID})
        if r.status_code >= 400:
            self.log.warning("blogger resolve %s -> %s", slug, r.status_code)
            return None
        data = (r.json().get("data") or {})
        if not data.get("id"):
            return None
        return data

    def _remember_blogger_id(self, slug: str, blogger: dict) -> None:
        """Pin the numeric blogger id in channel metadata so later runs can
        skip the resolve call (the slug stays the stable handle)."""
        try:
            with engine().begin() as conn:
                conn.execute(
                    text("""
                        UPDATE channel SET metadata =
                            jsonb_set(COALESCE(metadata, '{}'::jsonb),
                                      '{blogger_id}', to_jsonb(:bid))
                        WHERE source_id = (SELECT id FROM source WHERE code='businessfocus')
                          AND handle = :h
                    """),
                    {"bid": blogger["id"], "h": slug},
                )
        except Exception:  # noqa: BLE001
            self.log.debug("pin blogger_id failed for %s", slug, exc_info=True)

    # ---- discovery -----------------------------------------------------------

    def _scraped_ids(self, author_slug: str) -> set[str]:
        """external_ids already on disk for this author (cached per run)."""
        if author_slug in self._scraped_cache:
            return self._scraped_cache[author_slug]
        ids: set[str] = set()
        author_dir = DATA_DIR / self.code / slugify(author_slug, 80)
        for md_path in author_dir.glob("*/*.md"):
            try:
                head = md_path.read_text(encoding="utf-8", errors="replace")[:600]
            except OSError:
                continue
            m = re.search(r"^external_id: ['\"]?(\S+?)['\"]?\s*$", head, re.M)
            if m:
                ids.add(m.group(1))
        self._scraped_cache[author_slug] = ids
        return ids

    def already_scraped(self, d: dict) -> bool:
        ext_id = str(d.get("external_id", ""))
        author = d.get("author") or {}
        return ext_id in self._scraped_ids(author.get("slug") or "unknown")

    async def discover(
        self,
        limit: int | None = None,
        author_handle: str | None = None,
    ) -> AsyncIterator[dict]:
        authors = self._load_authors()
        if author_handle:
            if author_handle not in authors:
                authors = {author_handle: author_handle, **authors}
            targets = {author_handle: authors[author_handle]}
        else:
            targets = authors
        if not targets:
            self.log.warning(
                "no BusinessFocus authors registered — "
                "kb businessfocus add-author <slug>"
            )
            return

        n = 0
        async with await self._client() as client:
            for slug, display_name in sorted(targets.items()):
                blogger = await self.resolve_blogger(client, slug)
                if not blogger:
                    self.log.warning("could not resolve blogger %r — skipped", slug)
                    continue
                self._remember_blogger_id(slug, blogger)
                author = {
                    "slug": slug,
                    "name": blogger.get("display_name") or display_name or slug,
                }
                scraped = self._scraped_ids(slug)
                offset = 0
                while True:
                    url = f"{BASE}/node_api/v1/articles/blogger/{blogger['id']}"
                    r = await self.polite_get(
                        client, url,
                        params={"pageId": PAGE_ID, "offset": offset, "limit": PAGE_SIZE},
                    )
                    if r.status_code >= 400:
                        self.log.warning(
                            "index page failed for %s at offset %d -> %s",
                            slug, offset, r.status_code,
                        )
                        break
                    posts = r.json().get("data") or []
                    if not posts:
                        break
                    page_all_scraped = True
                    for post in posts:
                        ext_id = str(post.get("id") or "")
                        if not ext_id:
                            continue
                        path = post.get("url") or f"/article/{ext_id}/"
                        yield {
                            "external_id": ext_id,
                            "url": f"{BASE}{path}",
                            "title": post.get("title") or ext_id,
                            "published_at": _parse_iso_dt(post.get("post_date")),
                            "author": author,
                        }
                        n += 1
                        if ext_id not in scraped:
                            page_all_scraped = False
                        if limit and n >= limit:
                            return
                    # Index is newest-first: a fully-cached page means every
                    # older post was already downloaded on an earlier run.
                    if page_all_scraped:
                        self.log.info(
                            "index fully cached for %s at offset %d — stop paging",
                            slug, offset,
                        )
                        break
                    offset += len(posts)

    # ---- article fetch ---------------------------------------------------------

    async def fetch(self, d: dict) -> ScrapedItem | None:
        url = d["url"]
        async with await self._client() as client:
            r = await self.polite_get(client, url)
            if r.status_code >= 400:
                self.log.warning("article fetch %s -> %s", url, r.status_code)
                return None
            html = r.text

        soup = BeautifulSoup(html, "lxml")

        title = ""
        title_el = soup.select_one(".pl-main-article__title")
        if title_el:
            title = title_el.get_text(strip=True)

        published_at = d.get("published_at")
        ld_el = soup.find("script", type="application/ld+json")
        if ld_el and ld_el.string:
            try:
                ld = json.loads(ld_el.string)
            except ValueError:
                ld = {}
            if not title and ld.get("headline"):
                title = ld["headline"].strip()
            if published_at is None:
                published_at = _parse_iso_dt(ld.get("datePublished"))

        if not title:
            title = (d.get("title") or "").strip() or url

        body_el = soup.select_one(".pl-main-article__main") or soup.select_one("article")
        if body_el:
            for junk in body_el.select(
                "script, style, noscript, ins, .pl-text-ads, .ad-slot-script-wrap"
            ):
                junk.decompose()
            body_md = md_of(str(body_el), heading_style="ATX").strip()
            # markdownify keeps the source div indentation as whitespace-only
            # lines; collapse them so paragraphs stay canonical for FTS/chunks.
            body_md = re.sub(r"[ \t]+\n", "\n", body_md)
            body_md = re.sub(r"\n{3,}", "\n\n", body_md).strip()
        else:
            meta = soup.find("meta", attrs={"name": "description"})
            body_md = (meta.get("content", "").strip() if meta else "") or title

        author = d.get("author") or {}
        date_part = published_at.strftime("%Y-%m-%d") if published_at else "undated"

        return ScrapedItem(
            source=self.code,
            channel=author.get("slug") or "unknown",
            channel_name=author.get("name") or author.get("slug", ""),
            external_id=d["external_id"],
            title=title,
            url=url,
            published_at=published_at,
            body_md=body_md,
            raw_html=html,
            language="zh-Hant-HK",
            flat_layout=True,
            folder_name=f"{date_part}-{slugify(title, 80)}",
            extra={"author_slug": author.get("slug")},
        )
