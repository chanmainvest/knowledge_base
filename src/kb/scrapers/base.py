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


def _iso(v) -> str | None:
    """ISO-format a datetime-or-string (or None), tolerating scrapers that
    return ``published_at`` as a pre-formatted string rather than a datetime."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


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
    folder_name: str | None = None  # override leaf folder/stem name
    flat_layout: bool = False  # True → flat .md + data/raw/ hierarchy (hkej, macrovoices)
    channel_dir: str | None = None  # override data/<source>/<dir>/ segment (e.g. display name)

    def _storage_channel_slug(self) -> str:
        from ..io_md import slugify
        return slugify(self.channel_dir or self.channel)

    def folder(self) -> Path:
        """Legacy folder layout (youtube, patreon). Not used when flat_layout=True."""
        date_part = (self.published_at.strftime("%Y-%m-%d")
                     if self.published_at else "undated")
        from ..io_md import slugify
        leaf = self.folder_name or f"{date_part}__{slugify(self.external_id, 60)}"
        return DATA_DIR / self.source / self._storage_channel_slug() / leaf

    def content_path(self) -> Path:
        """Flat layout: data/<source>/[<channel>/]<year>/<stem>.md"""
        date_part = (self.published_at.strftime("%Y-%m-%d")
                     if self.published_at else "undated")
        year_part = (self.published_at.strftime("%Y")
                     if self.published_at else "undated")
        from ..io_md import slugify
        stem = self.folder_name or f"{date_part}-{slugify(self.external_id, 60)}"
        ch = self._storage_channel_slug()
        if ch == self.source:
            # channel == source (e.g. macrovoices): skip redundant channel dir
            return DATA_DIR / self.source / year_part / f"{stem}.md"
        return DATA_DIR / self.source / ch / year_part / f"{stem}.md"

    def raw_html_path(self) -> Path:
        """Flat layout: mirrors content_path() under data/raw/ with .html suffix."""
        p = self.content_path()
        rel = p.relative_to(DATA_DIR)
        return DATA_DIR / "raw" / rel.with_suffix(".html")


class BaseScraper(abc.ABC):
    code: str = ""           # registry key / site name, e.g. 'macrovoices'
    name: str = ""
    source_code: str = ""    # DB source code; empty → same as `code` (e.g. 'blog')

    @property
    def effective_source_code(self) -> str:
        """The source code written to markdown front-matter and resolved by
        ingest. Blog scrapers override ``source_code`` so multiple sites share
        one ``blog`` source row while keeping distinct scraper classes."""
        return self.source_code or self.code

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

    async def _recording_discover(self, limit: int | None = None, **kwargs):
        """Wrap :meth:`discover`, recording every yielded descriptor into the
        generic ``discovery_catalog`` so "discovered but not downloaded" is
        queryable and a half-dead scrape can be resumed. hkej/patreon keep
        their own richer catalogs and should NOT route through this wrapper."""
        async for d in self.discover(limit=limit, **kwargs):
            try:
                from .. import catalog
                catalog.record_discovery(self.effective_source_code, d)
            except Exception:  # noqa: BLE001
                self.log.debug("catalog.record_discovery failed", exc_info=True)
            yield d

    async def run(self, limit: int | None = None, **kwargs) -> list[Path]:
        out: list[Path] = []
        async for d in self._recording_discover(limit=limit, **kwargs):
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

    async def resume(self, limit: int | None = None) -> list[Path]:
        """Re-attempt items discovered but never downloaded.

        Reads pending rows (``downloaded=false``) from
        ``discovery_catalog`` for this source, reconstructs the original
        discovery descriptor, and re-runs ``fetch`` + ``write_md`` for each.
        Items that now pass :meth:`already_scraped` (downloaded out-of-band)
        are reconciled to ``downloaded=true``. Override in scrapers that keep
        a native catalog (hkej/patreon).
        """
        from .. import catalog
        out: list[Path] = []
        for row in catalog.pending(self.effective_source_code, limit=limit):
            d = row["descriptor"]
            if self.already_scraped(d):
                try:
                    catalog.mark_downloaded(self.effective_source_code,
                                            str(d.get("external_id", "")),
                                            str(self._md_path_for(d) or ""))
                except Exception:  # noqa: BLE001
                    pass
                continue
            try:
                item = await self.fetch(d)
            except Exception as exc:  # noqa: BLE001
                self.log.exception("resume fetch failed: %s :: %s", d, exc)
                continue
            if item is None:
                continue
            p = self.write_md(item)  # write_md marks the catalog row downloaded
            out.append(p)
            if limit and len(out) >= limit:
                break
        return out

    def _md_path_for(self, descriptor: dict) -> Path | None:
        """Best-effort on-disk md path for a descriptor, used only by resume
        to reconcile an already-downloaded row. Subclasses with a custom
        path scheme may override; default returns None (no reconciliation)."""
        return None

    def write_md(self, item: ScrapedItem) -> Path:
        from ..io_md import MdDoc
        front = {
            "source": item.source,
            "channel": item.channel,
            "channel_name": item.channel_name,
            "external_id": item.external_id,
            "url": item.url,
            "title": item.title,
            "published_at": _iso(item.published_at),
            "language": item.language,
            "duration_sec": item.duration_sec,
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "extra": item.extra or {},
        }
        doc = MdDoc(front=front, body=item.body_md)
        if item.flat_layout:
            path = item.content_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            doc.write(path)
            if item.raw_html:
                raw_path = item.raw_html_path()
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(item.raw_html, encoding="utf-8")
        else:
            folder = item.folder()
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "content.md"
            doc.write(path)
            if item.raw_html:
                (folder / "raw.html").write_text(item.raw_html, encoding="utf-8")
        self.log.info("wrote %s", path)
        # Record this download against the source's progress counter and the
        # discovery catalog. Wrapped so a tracking failure never aborts the
        # scrape itself.
        try:
            from .. import progress
            progress.mark_downloaded(self.effective_source_code)
        except Exception:  # noqa: BLE001
            self.log.debug("progress.mark_downloaded failed", exc_info=True)
        try:
            from .. import catalog
            catalog.mark_downloaded(self.effective_source_code,
                                    item.external_id, str(path))
        except Exception:  # noqa: BLE001
            self.log.debug("catalog.mark_downloaded failed", exc_info=True)
        return path
