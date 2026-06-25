"""Base scraper class."""
from __future__ import annotations

import abc
import asyncio
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import httpx

from ..config import DATA_DIR, settings
from ..logging_setup import get_logger
from ..ratelimit import HostRateLimiter


@dataclass
class ScrapedItem:
    source: str
    channel: str           # handle / slug
    channel_name: str
    external_id: str
    title: str
    url: str
    published_at: datetime | None
    body_md: str
    extra: dict | None = None
    raw_html: str | None = None
    slides_path: str | None = None
    duration_sec: int | None = None
    language: str | None = None
    folder_name: str | None = None  # override leaf folder name (default: date__id)

    def folder(self) -> Path:
        date_part = (self.published_at.strftime("%Y-%m-%d")
                     if self.published_at else "undated")
        from ..io_md import slugify
        leaf = self.folder_name or f"{date_part}__{slugify(self.external_id, 60)}"
        return DATA_DIR / self.source / slugify(self.channel) / leaf


class BaseScraper(abc.ABC):
    code: str = ""           # e.g. 'macrovoices'
    name: str = ""

    def __init__(self) -> None:
        self.log = get_logger(f"scraper.{self.code}")
        s = settings()
        self.limiter = HostRateLimiter(s.scrape_rate_limit_sec, jitter=1.0)
        self.headers = {"User-Agent": s.scrape_user_agent,
                        "Accept-Language": "en-US,en;q=0.8,zh-HK;q=0.7"}

    async def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.headers, follow_redirects=True, timeout=60.0, http2=True,
        )

    async def polite_get(self, client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
        await self.limiter.wait(url)
        for attempt in range(settings().scrape_max_retries):
            r = await client.get(url, **kw)
            if r.status_code == 429:
                ra = float(r.headers.get("Retry-After", "30"))
                self.log.warning("429 from %s; sleeping %.1fs", url, ra)
                await asyncio.sleep(ra + random.uniform(0, 2))
                continue
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            return r
        r.raise_for_status()
        return r

    @abc.abstractmethod
    async def discover(self, limit: int | None = None) -> AsyncIterator[dict]:
        """Yield lightweight item descriptors (must include 'external_id', 'url')."""
        if False:
            yield {}

    @abc.abstractmethod
    async def fetch(self, descriptor: dict) -> ScrapedItem | None:
        ...

    def already_scraped(self, descriptor: dict) -> bool:
        # default: subclasses can override; based on disk
        return False

    async def run(self, limit: int | None = None) -> list[Path]:
        out: list[Path] = []
        async for d in self.discover(limit=limit):
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

    def write_md(self, item: ScrapedItem) -> Path:
        from ..io_md import MdDoc
        folder = item.folder()
        folder.mkdir(parents=True, exist_ok=True)
        front = {
            "source": item.source,
            "channel": item.channel,
            "channel_name": item.channel_name,
            "external_id": item.external_id,
            "url": item.url,
            "title": item.title,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "language": item.language,
            "duration_sec": item.duration_sec,
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "extra": item.extra or {},
        }
        doc = MdDoc(front=front, body=item.body_md)
        path = folder / "content.md"
        doc.write(path)
        if item.raw_html:
            (folder / "raw.html").write_text(item.raw_html, encoding="utf-8")
        self.log.info("wrote %s", path)
        return path
