"""Blogspot / Blogger platform scraper.

Generic scraper for Blogspot-hosted blogs (Google Blogger). Each Blogger
site (e.g. greenhorn) is one *channel* under the shared ``blog`` source —
same shape as ``madxcap`` / ``gorozen`` which share ``blog``.

Current registry
----------------
``BLOGSPOT_SITES`` holds known handles. Greenhorn
(https://greenhornfinancefootnote.blogspot.com/ — 綠角財經筆記) is the
first entry; add new Blogspot blogs by extending the dict (no code change
needed beyond the entry). A caller can also scrape an arbitrary Blogspot URL
via ``--source-type https://<name>.blogspot.com/`` — it is treated as a
custom one-off site and its handle is derived from the hostname.

Engine: static HTML — httpx + BeautifulSoup, no JS.
Pagination: Blogger ``/search?updated-max=<ISO>&max-results=N`` with
``a.blog-pager-older-link``. Feeds (``/feeds/posts/default``) currently
redirect via follow.it, so HTML is the stable path.
Post URL: ``/YYYY/MM/slug.html`` → external_id ``YYYY/MM/slug``.
Date: ``span.date-header`` ``YYYY年M月D日``.
Body: ``div.post-body`` → regex fallback (full-doc lxml sees empty div on
this Blogger template).
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from datetime import datetime
from typing import AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md_of

from ..config import DATA_DIR
from ..io_md import slugify
from .base import BaseScraper, ScrapedItem

# ---------------------------------------------------------------------------
# Site registry — add new Blogspot blogs here.
# ``handle`` is the channel slug stored on disk (data/blog/<handle>/).
# ``base`` must be the blog's root URL (no trailing slash needed).
# ---------------------------------------------------------------------------
BLOGSPOT_SITES: dict[str, dict] = {
    "greenhorn": {
        "base": "https://greenhornfinancefootnote.blogspot.com",
        "name": "綠角財經筆記",
        "lang": "zh-TW",
    },
}

_PAGE_SIZE = 25
_RE_POST_PATH = re.compile(r"/(\d{4})/(\d{2})/([^/]+)\.html", re.I)
_RE_ZH_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def _parse_blogspot_date(text: str) -> datetime | None:
    if not text:
        return None
    m = _RE_ZH_DATE.search(text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d")
        except ValueError:
            pass
    m = re.search(r"(\d{4})/(\d{2})/(\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y/%m/%d")
        except ValueError:
            pass
    return None


def _external_id_from_url(url: str) -> str | None:
    try:
        path = urlparse(url).path
    except ValueError:
        return None
    if path.endswith(".html"):
        path = path[: -len(".html")]
    path = path.lstrip("/")
    if not re.match(r"\d{4}/\d{2}/[^/]+$", path):
        return None
    return path


def _slug_from_external_id(external_id: str) -> str:
    return external_id.rsplit("/", 1)[-1] if "/" in external_id else external_id


def _handle_for_base(base: str) -> str | None:
    base_n = base.rstrip("/").lower()
    for handle, cfg in BLOGSPOT_SITES.items():
        if cfg["base"].rstrip("/").lower() == base_n:
            return handle
    return None


def _handle_for_url(url: str) -> str | None:
    """Reverse-lookup handle from a post URL's origin, or None for custom sites."""
    try:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    except ValueError:
        return None
    return _handle_for_base(origin)


def _resolve_targets(source_type: str | None) -> list[tuple[str, dict]]:
    """Resolve ``source_type`` (handle or URL) to a list of (handle, cfg).

    - None → all known sites
    - handle name (e.g. ``greenhorn``) → that site
    - URL (``https://…blogspot.com``) → ephemeral custom site (handle derived
      from hostname, e.g. ``myblog`` from ``myblog.blogspot.com``)
    """
    if not source_type:
        return list(BLOGSPOT_SITES.items())
    st = source_type.strip()
    # URL-like?
    if st.startswith("http://") or st.startswith("https://"):
        base = st.rstrip("/")
        # strip path — keep origin only
        try:
            parsed = urlparse(base)
            origin = f"{parsed.scheme}://{parsed.netloc}"
        except ValueError:
            origin = base
        # try to find existing handle for this origin
        h = _handle_for_base(origin)
        if h:
            return [(h, BLOGSPOT_SITES[h])]
        # ephemeral custom site
        netloc = urlparse(origin).netloc or origin
        # handle = first label before .blogspot.com, else slugified netloc
        if "blogspot" in netloc:
            handle = netloc.split(".")[0]
        else:
            handle = slugify(netloc, 40) or "custom"
        # ensure handle uniqueness
        handle = handle.lower()
        cfg = {"base": origin, "name": handle, "lang": "zh-TW"}
        return [(handle, cfg)]
    # handle name
    if st in BLOGSPOT_SITES:
        return [(st, BLOGSPOT_SITES[st])]
    # allow short alias without exact match? try case-insensitive
    for h, cfg in BLOGSPOT_SITES.items():
        if h.lower() == st.lower():
            return [(h, cfg)]
    raise ValueError(
        f"Unknown Blogspot site {source_type!r}. Known: {', '.join(BLOGSPOT_SITES)} "
        f"or pass a full https://…blogspot.com URL."
    )


class BlogspotScraper(BaseScraper):
    code = "blogspot"
    name = "Blogspot"
    source_code = "blog"

    def __init__(self) -> None:
        super().__init__()
        self.headers["Accept-Language"] = "zh-TW,zh;q=0.9,en;q=0.8"

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            timeout=60.0,
            http2=True,
        )

    # ------------------------------------------------------------------
    # discovery — walk Blogger pagination per site
    # ------------------------------------------------------------------
    async def discover(
        self,
        limit: int | None = None,
        *,
        source_type: str | None = None,
        # alias so callers can use site=…
        site: str | None = None,
    ) -> AsyncIterator[dict]:
        """Walk ``/`` → ``/search?updated-max=…`` for each configured site.

        ``source_type`` (or ``site``) filters to one handle or a custom URL:

        * ``None`` — all sites in ``BLOGSPOT_SITES``
        * ``"greenhorn"`` — that handle only
        * ``"https://myblog.blogspot.com"`` — arbitrary Blogspot URL
        """
        # ``site`` is an alias for ``source_type`` (mirrors madxcap/gorozen)
        st = source_type if source_type is not None else site
        try:
            targets = _resolve_targets(st)
        except ValueError as exc:
            self.log.error(str(exc))
            return
        # Share a single client across sites
        async with await self._client() as client:
            total = 0
            for handle, cfg in targets:
                base: str = cfg["base"]
                lang: str = cfg.get("lang", "zh-TW")  # noqa: F841 (kept for future per-site use)
                blog_name: str = cfg.get("name", handle)
                seen: set[str] = set()
                next_url: str | None = base
                self.log.info("blogspot discover handle=%s base=%s", handle, base)
                while next_url:
                    await self.limiter.wait(next_url)
                    self.log.info("blogspot listing %s", next_url)
                    try:
                        r = await client.get(next_url)
                    except Exception as exc:  # noqa: BLE001
                        self.log.warning("listing fetch failed %s: %s", next_url, exc)
                        break
                    if r.status_code >= 400:
                        self.log.warning("listing %s -> %s", next_url, r.status_code)
                        break
                    soup = BeautifulSoup(r.text, "lxml")
                    fresh = 0
                    for a in soup.select("h3.post-title a[href]"):
                        href = str(a.get("href", "")).strip()
                        if not href:
                            continue
                        href = urljoin(base + "/", href)
                        href = html_lib.unescape(href).strip()
                        ext_id = _external_id_from_url(href)
                        if not ext_id or ext_id in seen:
                            continue
                        seen.add(ext_id)
                        title = html_lib.unescape(a.get_text(strip=True))
                        if not title:
                            title = ext_id
                        published_at: datetime | None = None
                        post_div = a.find_parent("div", class_="post")
                        if post_div is not None:
                            date_el = post_div.select_one("span.date-header")
                            if date_el:
                                published_at = _parse_blogspot_date(
                                    date_el.get_text(" ", strip=True)
                                )
                            if not published_at:
                                published_at = _parse_blogspot_date(
                                    post_div.get_text(" ", strip=True)
                                )
                        if not published_at:
                            m = _RE_POST_PATH.search(href)
                            if m:
                                try:
                                    published_at = datetime(int(m.group(1)), int(m.group(2)), 1)
                                except ValueError:
                                    pass
                        fresh += 1
                        yield {
                            "external_id": ext_id,
                            "url": href,
                            "title": title,
                            "published_at": published_at,
                            "handle": handle,
                            "blog_base": base,
                            "blog_name": blog_name,
                        }
                        total += 1
                        if limit and total >= limit:
                            return
                    if fresh == 0:
                        self.log.info("blogspot listing no fresh on %s", next_url)
                        break
                    older = soup.select_one("a.blog-pager-older-link")
                    if older is None:
                        older = soup.find("a", id="Blog1_blog-pager-older-link")
                    if older is None:
                        older = soup.find("a", href=re.compile(r"updated-max"))
                    if older is None or not older.get("href"):
                        break
                    raw_href = html_lib.unescape(str(older.get("href")))
                    nxt = urljoin(base + "/", raw_href)
                    if "max-results=" in nxt:
                        nxt = re.sub(r"max-results=\d+", f"max-results={_PAGE_SIZE}", nxt)
                    elif "?" not in nxt:
                        nxt = f"{nxt}?max-results={_PAGE_SIZE}"
                    if nxt == next_url or nxt in seen:
                        break
                    next_url = nxt
                    await asyncio.sleep(0.5)
                if limit and total >= limit:
                    return

    def already_scraped(self, d: dict) -> bool:
        ext_id = str(d.get("external_id", ""))
        handle = str(d.get("handle") or _handle_for_url(str(d.get("url", ""))) or "greenhorn")
        slug = _slug_from_external_id(ext_id)
        slug_norm = slugify(slug, 80)
        # Channel dir is per-blog handle under data/blog/
        channel_dir = DATA_DIR / self.effective_source_code / handle
        # Fallback to legacy greenhorn dir if custom handle has no dir yet but
        # legacy data exists (migration case)
        if not channel_dir.exists() and handle != "greenhorn":
            # still check greenhorn for old greenhorn posts when handle was implicit
            pass
        if not channel_dir.exists():
            return False
        for md_path in channel_dir.glob("*/*.md"):
            if md_path.stat().st_size < 200:
                continue
            stem = md_path.stem
            if slug_norm and slug_norm in stem:
                return True
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
                if f"external_id: '{ext_id}'" in text or f'external_id: "{ext_id}"' in text:
                    return True
            except Exception:
                continue
        return False

    async def fetch(self, d: dict) -> ScrapedItem | None:
        url = d["url"]
        handle = str(d.get("handle") or _handle_for_url(url) or "greenhorn")
        cfg = BLOGSPOT_SITES.get(handle, {})
        blog_name = str(d.get("blog_name") or cfg.get("name") or handle)
        lang = str(cfg.get("lang") or "zh-TW")
        async with await self._client() as client:
            await self.limiter.wait(url)
            r = await client.get(url)
            if r.status_code >= 400:
                self.log.warning("article fetch %s -> %s", url, r.status_code)
                return None
            html = r.text
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.select_one("h3.post-title")
        if title_el is None:
            title_el = soup.find("h3", class_=re.compile(r"post-title", re.I))
        if title_el is not None:
            a = title_el.find("a")
            title = html_lib.unescape((a.get_text(strip=True) if a else title_el.get_text(strip=True))).strip()
        else:
            title = html_lib.unescape(str(d.get("title") or d["external_id"])).strip()
        published_at = d.get("published_at")
        if not published_at:
            date_el = soup.select_one("span.date-header")
            if date_el:
                published_at = _parse_blogspot_date(date_el.get_text(" ", strip=True))
        if not published_at:
            published_at = _parse_blogspot_date(soup.get_text(" ", strip=True))
        if not published_at:
            m = _RE_POST_PATH.search(url)
            if m:
                try:
                    published_at = datetime(int(m.group(1)), int(m.group(2)), 1)
                except ValueError:
                    pass
        # Body — Blogger template quirk: ``div.post-body`` is empty and
        # the article lives in its next siblings until ``div.post-footer``.
        # Full-doc lxml sees the empty div, so we must collect siblings.
        # Regex fallback kept only for edge cases.
        body_md = ""
        post_body_el = soup.select_one("div.post-body")
        if post_body_el is not None:
            if post_body_el.get_text(strip=True):
                for junk in post_body_el.select("script, style"):
                    junk.decompose()
                body_md = md_of(str(post_body_el), heading_style="ATX").strip()
            else:
                parts: list[str] = []
                cur = post_body_el.next_sibling
                while cur is not None:
                    cname = getattr(cur, "name", None)
                    if cname == "div" and cur.get("class") and any(
                        "post-footer" in c for c in (cur.get("class") or [])
                    ):
                        break
                    if cname:
                        parts.append(str(cur))
                    cur = cur.next_sibling
                    if len(parts) > 600:
                        break
                if parts:
                    body_html = "".join(parts)
                    frag_soup = BeautifulSoup(body_html, "lxml")
                    for junk in frag_soup.select("script, style"):
                        junk.decompose()
                    body_html = (
                        "".join(str(c) for c in frag_soup.body.contents)
                        if frag_soup.body
                        else body_html
                    )
                    if body_html.strip():
                        body_md = md_of(body_html, heading_style="ATX").strip()
        if not body_md:
            m_body = re.search(
                r'<div[^>]*class=["\'][^"\']*post-body[^"\']*["\'][^>]*>(.*?)</div>\s*<div[^>]*class=["\']post-footer',
                html,
                re.S | re.I,
            )
            if m_body:
                body_html = m_body.group(1)
                frag_soup = BeautifulSoup(body_html, "lxml")
                for junk in frag_soup.select("script, style"):
                    junk.decompose()
                body_html = str(frag_soup) if frag_soup.body else body_html
                if frag_soup.body:
                    body_html = "".join(str(c) for c in frag_soup.body.contents)
                body_md = md_of(body_html, heading_style="ATX").strip()
            else:
                body_el = (
                    soup.select_one("div.post-body")
                    or soup.select_one("div.entry-content")
                    or soup.select_one("article")
                    or soup.select_one("#main .post")
                )
                if body_el is not None:
                    for junk in body_el.select("script, style, nav, footer, aside"):
                        junk.decompose()
                    body_md = md_of(str(body_el), heading_style="ATX").strip()
                else:
                    body_md = title
        if not body_md:
            post_wrap = soup.select_one("div.post")
            if post_wrap is not None:
                body_md = md_of(str(post_wrap), heading_style="ATX").strip()
            if not body_md:
                body_md = title
        lines = body_md.splitlines()
        if lines and lines[0].startswith("#"):
            first = lines[0].lstrip("# ").strip()
            if first == title or title.startswith(first) or first.startswith(title[:30]):
                body_md = "\n".join(lines[1:]).strip()
        tags: list[str] = []
        for a in soup.select("span.post-labels a"):
            t = html_lib.unescape(a.get_text(strip=True))
            if t:
                tags.append(t)
        ext_id = str(d["external_id"])
        slug = _slug_from_external_id(ext_id)
        date_part = published_at.strftime("%Y-%m-%d") if isinstance(published_at, datetime) else "undated"
        folder_name = f"{date_part}-{slugify(slug, 80)}"
        # Channel is the blogspot handle (e.g. greenhorn) — stable across
        # custom URLs too (hostname-derived). Stored under data/blog/<handle>/.
        return ScrapedItem(
            source=self.effective_source_code,
            channel=handle,
            channel_name=blog_name,
            external_id=ext_id,
            title=title,
            url=url,
            published_at=published_at if isinstance(published_at, datetime) else None,
            body_md=body_md,
            raw_html=html,
            language=lang,
            flat_layout=True,
            folder_name=folder_name,
            extra={"tags": tags, "slug": slug, "handle": handle},
        )


# Back-compat alias — ``greenhorn`` was the original module/code. Keep it
# so ``kb blog scrape greenhorn`` and ``SCRAPERS["greenhorn"]`` still work
# after the rename to the generic ``blogspot`` platform.
class GreenhornScraper(BlogspotScraper):  # type: ignore
    code = "greenhorn"
    name = "綠角財經筆記"

    async def discover(self, limit: int | None = None, **kwargs):  # type: ignore[override]
        # Force to greenhorn handle unless caller explicitly passes another
        kwargs.setdefault("source_type", "greenhorn")
        async for d in super().discover(limit=limit, **kwargs):
            yield d
