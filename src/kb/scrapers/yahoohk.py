"""Yahoo Finance Hong Kong columnist scraper.

Discovers authors from https://hk.finance.yahoo.com/topic/contributors/ and
paginates each author's feed via the Nexus GraphQL API (GetAuthorFeed).
Article bodies are fetched from the public news HTML (.caas-body).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote, unquote

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md_of

from ..config import DATA_DIR
from ..io_md import slugify
from .base import BaseScraper, ScrapedItem


BASE = "https://hk.finance.yahoo.com"
CONTRIBUTORS_URL = f"{BASE}/topic/contributors/"
GATEWAY = "https://nexus-gateway-prod.media.yahoo.com/"
PAGE_SIZE = 25
_FEED_TEMPLATE_PATH = Path(__file__).with_name("yahoohk_author_feed.json")
_GENERIC_TITLES = frozenset({"雅虎香港財經", "Yahoo Finance Hong Kong"})


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _is_generic_title(title: str) -> bool:
    return title.strip() in _GENERIC_TITLES


def _h1_text(line: str) -> str:
    return re.sub(r"^#+\s*", "", line).strip()


def _h1_line_indices(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if re.match(r"^# [^#]", line)]


def _trim_columnist_boilerplate(body_md: str) -> tuple[str, str | None]:
    """Drop Yahoo columnist chrome before the article headline.

    When the feed or page chrome adds a generic first ``#`` heading, keep the
    second one as the article title. Otherwise start at the first ``#`` heading
    and drop any banner markup above it.
    """
    lines = body_md.splitlines()
    h1_idxs = _h1_line_indices(lines)
    if not h1_idxs:
        return body_md.strip(), None

    first_title = _h1_text(lines[h1_idxs[0]])
    use_idx = 1 if len(h1_idxs) >= 2 and _is_generic_title(first_title) else 0
    start = h1_idxs[use_idx]
    return "\n".join(lines[start:]).strip(), _h1_text(lines[start])


class YahooHKScraper(BaseScraper):
    code = "yahoohk"
    name = "Yahoo Finance Hong Kong"

    def __init__(self) -> None:
        super().__init__()
        self.headers["Accept-Language"] = "zh-HK,zh;q=0.9,en;q=0.8"
        self._session_meta: dict[str, tuple[str, str]] = {}
        self._query_template: dict | None = None
        self._authors_cache: dict[str, str] | None = None

    def _query_payload(self) -> dict:
        if self._query_template is None:
            self._query_template = json.loads(
                _FEED_TEMPLATE_PATH.read_text(encoding="utf-8")
            )
        return json.loads(json.dumps(self._query_template))

    def _author_page_url(self, slug: str) -> str:
        return f"{BASE}/author/{quote(slug, safe='')}/"

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            timeout=60.0,
            http2=True,
        )

    async def _get_page_meta(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str]:
        if slug in self._session_meta:
            return self._session_meta[slug]
        url = self._author_page_url(slug)
        await self.limiter.wait(url)
        r = await client.get(url)
        r.raise_for_status()
        html = r.text
        yrid_m = re.search(r'"yrid"\s*:\s*"([^"]+)"', html)
        ver_m = re.search(r'"clientVersion"\s*:\s*"([^"]+)"', html)
        if not yrid_m or not ver_m:
            raise RuntimeError(f"could not parse Yahoo session meta for author {slug!r}")
        meta = (yrid_m.group(1), ver_m.group(1))
        self._session_meta[slug] = meta
        return meta

    def _gateway_headers(self, slug: str, yrid: str, version: str) -> dict[str, str]:
        return {
            **self.headers,
            "Content-Type": "application/json",
            "Referer": self._author_page_url(slug),
            "x-yahoo-cg-client-name": "finance",
            "x-yahoo-cg-client-version": version,
            "y-rid": yrid,
        }

    async def discover_authors(self, client: httpx.AsyncClient) -> dict[str, str]:
        """Return {url_slug: display_name} from the contributors index."""
        await self.limiter.wait(CONTRIBUTORS_URL)
        r = await client.get(CONTRIBUTORS_URL)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        authors: dict[str, str] = {}
        for a in soup.find_all("a", href=re.compile(r"/author/")):
            m = re.search(r"/author/([^/?#]+)", a.get("href", ""))
            if not m:
                continue
            slug = unquote(m.group(1))
            name = a.get_text(strip=True)
            # Contributor index links articles with long titles; keep short names only.
            if name and ("|" in name or "│" in name or len(name) > 40):
                name = slug
            if slug in authors:
                continue
            authors[slug] = name or slug
        if not authors:
            for script in soup.find_all("script", type="application/json"):
                if not script.string:
                    continue
                for m in re.finditer(r"/author/([^\"/?#]+)", script.string):
                    slug = unquote(m.group(1))
                    authors.setdefault(slug, slug)
        if not authors:
            self.log.warning("no authors found at %s", CONTRIBUTORS_URL)
        return authors

    def ensure_channels(self, authors: dict[str, str]) -> None:
        from sqlalchemy import text

        from ..db import engine

        with engine().begin() as conn:
            sid = conn.execute(
                text("SELECT id FROM source WHERE code=:c"), {"c": self.code}
            ).scalar_one_or_none()
            if sid is None:
                self.log.warning("source %r not in database — run kb db migrate", self.code)
                return
            for handle, name in sorted(authors.items()):
                conn.execute(
                    text(
                        "INSERT INTO channel(source_id, handle, name) VALUES (:s,:h,:n) "
                        "ON CONFLICT (source_id, handle) DO UPDATE SET name=EXCLUDED.name"
                    ),
                    {"s": sid, "h": handle, "n": name},
                )

    async def _fetch_author_stream(
        self,
        client: httpx.AsyncClient,
        slug: str,
        *,
        start: int,
        count: int = PAGE_SIZE,
    ) -> dict:
        yrid, version = await self._get_page_meta(client, slug)
        payload = self._query_payload()
        payload["variables"]["start"] = start
        payload["variables"]["count"] = count
        payload["variables"]["contentSearchInput"]["alias_slug"] = f"author={slug}"
        await self.limiter.wait(GATEWAY)
        r = await client.post(
            GATEWAY,
            headers=self._gateway_headers(slug, yrid, version),
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(f"GraphQL errors for {slug}: {data['errors']}")
        return data["data"]["authorStream"]

    def _author_dict(self, slug: str, display_name: str | None = None) -> dict:
        return {"slug": slug, "name": display_name or slug}

    def _descriptor_from_asset(self, asset: dict, author: dict) -> dict | None:
        kind = asset.get("__typename")
        attrs = asset.get("contentAttributes") or {}
        url = attrs.get("canonicalUrl") or attrs.get("clickthroughUrl")
        if kind == "Story":
            ext_id = asset.get("id") or ""
            title = asset.get("title") or ext_id
            if not url or not ext_id:
                return None
            return {
                "external_id": ext_id,
                "url": url,
                "title": title,
                "published_at": _parse_iso_dt(attrs.get("pubDate") or attrs.get("displayTime")),
                "author": author,
                "asset_type": "story",
            }
        if kind in ("Video", "Outlink"):
            ext_id = asset.get("id") or asset.get("uuid") or ""
            title = asset.get("title") or asset.get("headline") or ext_id
            if not url:
                url = asset.get("url") or ""
            if not ext_id or not url:
                return None
            return {
                "external_id": ext_id,
                "url": url,
                "title": title,
                "published_at": _parse_iso_dt(
                    attrs.get("pubDate") or attrs.get("displayTime") or asset.get("displayTime")
                ),
                "author": author,
                "asset_type": kind.lower(),
            }
        return None

    def already_scraped(self, d: dict) -> bool:
        ext_id = str(d.get("external_id", ""))
        author = d.get("author") or {}
        author_dir = DATA_DIR / self.code / slugify(author.get("slug") or "unknown", 80)
        if not author_dir.exists():
            return False
        for md_path in author_dir.glob("*/*.md"):
            if md_path.stat().st_size < 200:
                continue
            text = md_path.read_text(encoding="utf-8", errors="replace")
            if f"external_id: '{ext_id}'" in text or f'external_id: "{ext_id}"' in text:
                return True
        return False

    async def discover(
        self,
        limit: int | None = None,
        author_handle: str | None = None,
    ) -> AsyncIterator[dict]:
        async with await self._client() as client:
            if self._authors_cache is None:
                self._authors_cache = await self.discover_authors(client)
                self.ensure_channels(self._authors_cache)
            authors = self._authors_cache

            if author_handle:
                if author_handle not in authors:
                    authors = {author_handle: author_handle, **authors}
                targets = {author_handle: authors.get(author_handle, author_handle)}
            else:
                targets = authors

            n = 0
            for slug, display_name in sorted(targets.items()):
                author = self._author_dict(slug, display_name)
                start = 0
                while True:
                    try:
                        stream = await self._fetch_author_stream(client, slug, start=start)
                    except Exception as exc:  # noqa: BLE001
                        self.log.exception("author feed failed for %s at %d: %s", slug, start, exc)
                        break
                    author_info = stream.get("author") or {}
                    if author_info.get("displayName"):
                        author["name"] = author_info["displayName"]
                    items = stream.get("stream") or []
                    if not items:
                        break
                    for item in items:
                        asset = (item or {}).get("asset") or {}
                        desc = self._descriptor_from_asset(asset, author)
                        if desc is None:
                            continue
                        yield desc
                        n += 1
                        if limit and n >= limit:
                            return
                    pagination = stream.get("pagination") or {}
                    total = int(pagination.get("total") or 0)
                    if total > 0:
                        try:
                            from sqlalchemy import text as _t
                            from ..db import engine as _eng
                            with _eng().begin() as _c:
                                _c.execute(_t("""
                                    UPDATE channel SET metadata =
                                        jsonb_set(COALESCE(metadata, '{}'::jsonb),
                                                  '{total_seen}', to_jsonb(:n))
                                    WHERE source_id = (SELECT id FROM source WHERE code='yahoohk')
                                      AND handle = :h
                                """), {"n": total, "h": slug})
                        except Exception:  # noqa: BLE001
                            self.log.debug("store total_seen failed", exc_info=True)
                    start += len(items)
                    if not pagination.get("nextPage") or start >= total:
                        break

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
        page_title = soup.title.string.strip() if soup.title and soup.title.string else ""
        title = (d.get("title") or "").strip()
        if _is_generic_title(title) and page_title:
            title = page_title
        published_at = d.get("published_at")
        if published_at is None:
            time_el = (
                soup.select_one(".caas-body time")
                or soup.select_one("article time")
                or soup.find("time")
            )
            if time_el and time_el.get("datetime"):
                published_at = _parse_iso_dt(time_el["datetime"])

        body_el = soup.select_one(".caas-body") or soup.select_one("article")
        if body_el:
            # Drop provider chrome above the article headline.
            for junk in body_el.select(
                "header, nav, [data-test-locator='molecule-banner']"
            ):
                junk.decompose()
            body_md = md_of(str(body_el), heading_style="ATX").strip()
            body_md, headline = _trim_columnist_boilerplate(body_md)
            if headline:
                title = headline
        else:
            summary = ""
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                summary = meta["content"].strip()
            body_md = summary or title or d.get("url", "")

        if not title:
            title_el = (
                soup.select_one(".caas-body h1")
                or soup.select_one("article h1")
                or soup.find("h1")
            )
            title = title_el.get_text(strip=True) if title_el else d.get("url", "")

        author = d.get("author") or {}
        author_slug = author.get("slug") or "unknown"
        channel_dir = slugify(author_slug, 80)
        date_part = published_at.strftime("%Y-%m-%d") if published_at else "undated"
        folder_name = f"{date_part}-{slugify(title, 80)}"
        if d.get("asset_type") and d["asset_type"] != "story":
            body_md = f"> ({d['asset_type']})\n\n{body_md}".strip()

        return ScrapedItem(
            source=self.code,
            channel=channel_dir,
            channel_name=author.get("name") or author.get("slug", ""),
            external_id=d["external_id"],
            title=title,
            url=url,
            published_at=published_at,
            body_md=body_md.strip(),
            raw_html=html,
            language="zh-Hant-HK",
            flat_layout=True,
            folder_name=folder_name,
            extra={
                "author_slug": author.get("slug"),
                "asset_type": d.get("asset_type", "story"),
            },
        )

    async def run(
        self,
        limit: int | None = None,
        author_handle: str | None = None,
    ) -> list:
        if author_handle is None:
            async with await self._client() as client:
                self._authors_cache = await self.discover_authors(client)
            self.ensure_channels(self._authors_cache)
            out: list = []
            for slug in sorted(self._authors_cache):
                paths = await self.run(limit=limit, author_handle=slug)
                out.extend(paths)
            return out

        out: list = []
        async for d in self._recording_discover(limit=limit, author_handle=author_handle):
            if self.already_scraped(d):
                self.log.info("skip (cached) %s", d.get("url") or d.get("external_id"))
                continue
            try:
                item = await self.fetch(d)
            except Exception as exc:  # noqa: BLE001
                self.log.exception("fetch failed: %s :: %s", d, exc)
                continue
            if item is None:
                continue
            p = self.write_md(item)
            out.append(p)
            if limit and len(out) >= limit:
                break
        return out
