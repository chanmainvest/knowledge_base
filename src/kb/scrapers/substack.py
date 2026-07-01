"""Substack scraper using Substack's public JSON API + a browser fallback for
gated (paid-only) content.

Discovery and article bodies come from a publication's own API — no login
required for free posts:

    https://<subdomain>.substack.com/api/v1/archive?sort=new&offset=&limit=
    https://<subdomain>.substack.com/api/v1/posts/<slug>

A publication's ``subdomain`` (and, for pubs on a *forced* custom domain,
``custom_domain``) is resolved once from a writer's public profile handle
(``https://substack.com/api/v1/user/<handle>/public_profile``) and cached in
``channel.metadata`` — mirrors how ``patreon.py`` caches ``campaign_id``.

Paid (``audience != "everyone"``) posts sometimes come back from the API with
the full body already inlined (many creators enable "free preview" for every
post), but not always. When the API body looks truncated relative to the
post's own ``wordcount``, we fall back to rendering the post with a headless,
cookie-authenticated browser instead of trusting the anonymous API response.
This is also the only way to read a publication that forces a *custom
domain* redirect (``custom_domain_optional: false``): Substack's auth cookie
is scoped to ``.substack.com`` and does not transfer across domains for a
plain HTTP client, but a real browser page load on the custom domain
performs its own credentialed sync back to substack.com, exactly as it does
for a human reader.

Requires a ``substack.sid`` cookie (``SUBSTACK_SESSION_COOKIE`` in .env, or
``SUBSTACK_COOKIES_FROM_BROWSER``) to read posts on a paid subscription. Get
one interactively with ``kb substack prime-session``.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
from sqlalchemy import text as sa_text

from ..config import DATA_DIR, settings
from ..io_md import slugify
from ..logging_setup import get_logger
from ..ratelimit import HostRateLimiter
from .base import BaseScraper, ScrapedItem

SUBSTACK_ROOT = "https://substack.com"
SESSION_PATH = DATA_DIR / "substack" / ".session.json"
ARCHIVE_PAGE_SIZE = 20
_BETWEEN_CHANNELS_SEC = 2.0
_BETWEEN_PAGES_SEC = 1.5
_PAGE_SETTLE_SEC = 2.0
_MIN_429_BACKOFF_SEC = 15.0
# Completeness threshold: treat the anonymous API body as "the full post"
# when its rendered word count is at least this fraction of the post's own
# reported wordcount (HTML markup + stripped embeds mean it rarely hits 100%).
_COMPLETE_WORDCOUNT_RATIO = 0.85

_POST_PAGE_CONTENT_SELECTORS = [
    ".available-content",
    ".body.markup",
    "article .body",
    "[class*='available-content']",
    "[class*='body-markup']",
    ".post-content",
    "article",
]

_BROWSER_LOADERS: dict[str, str] = {
    "chrome": "chrome",
    "chromium": "chromium",
    "edge": "edge",
    "firefox": "firefox",
    "brave": "brave",
    "opera": "opera",
    "vivaldi": "vivaldi",
}


def normalize_handle(handle: str) -> str:
    """Extract a bare Substack handle from '@handle', a profile URL, or a URL."""
    raw = handle.strip()
    raw = re.sub(r"^https?://(?:www\.)?substack\.com/", "", raw, flags=re.I)
    raw = raw.split("?")[0].strip("/")
    return raw.lstrip("@")


def _public_profile_url(handle: str) -> str:
    return f"{SUBSTACK_ROOT}/api/v1/user/{quote(handle, safe='')}/public_profile"


def _subscriptions_url() -> str:
    return f"{SUBSTACK_ROOT}/api/v1/subscriptions?tvOnly=false"


def _archive_url(subdomain: str, offset: int, limit: int = ARCHIVE_PAGE_SIZE) -> str:
    return (
        f"https://{subdomain}.substack.com/api/v1/archive"
        f"?sort=new&search=&offset={offset}&limit={limit}"
    )


def _post_detail_url(subdomain: str, slug: str) -> str:
    return f"https://{subdomain}.substack.com/api/v1/posts/{quote(slug, safe='')}"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _html_to_md(html: str) -> str:
    if not html:
        return ""
    return markdownify(html, heading_style="ATX").strip()


def _html_wordcount(html: str) -> int:
    if not html:
        return 0
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    return len(text.split())


def _load_sid_from_file() -> str | None:
    if not SESSION_PATH.exists():
        return None
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        sid = (data.get("sid") or "").strip()
        return sid or None
    except Exception:
        return None


def save_sid(sid: str) -> Path:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps({"sid": sid}, indent=2), encoding="utf-8")
    return SESSION_PATH


def _load_sid_from_browser(spec: str) -> str | None:
    """Read the substack.sid cookie from a local browser profile (e.g. chrome)."""
    import browser_cookie3

    browser_name, _, profile = spec.partition(":")
    loader_name = _BROWSER_LOADERS.get(browser_name.lower())
    if not loader_name:
        raise ValueError(
            f"unsupported browser {browser_name!r}; "
            f"use one of: {', '.join(sorted(_BROWSER_LOADERS))}"
        )
    loader = getattr(browser_cookie3, loader_name)
    kwargs: dict[str, Any] = {"domain_name": ".substack.com"}
    if profile:
        kwargs["profile"] = profile
    for cookie in loader(**kwargs):
        if cookie.name == "substack.sid" and cookie.value:
            return cookie.value
    return None


def _load_channels(handle: str | None = None) -> list[dict[str, Any]]:
    try:
        from ..db import engine as db_engine

        sql = (
            "SELECT c.handle, c.name, c.metadata FROM channel c "
            "JOIN source s ON c.source_id = s.id WHERE s.code = 'substack' "
        )
        params: dict[str, Any] = {}
        if handle:
            sql += "AND c.handle = :h "
            params["h"] = handle
        sql += "ORDER BY c.name"
        with db_engine().connect() as conn:
            rows = conn.execute(sa_text(sql), params).fetchall()
        return [
            {
                "handle": r[0],
                "name": r[1],
                "metadata": r[2] if isinstance(r[2], dict) else (json.loads(r[2]) if r[2] else {}),
            }
            for r in rows
        ]
    except Exception:
        return []


def _save_publication_meta(handle: str, info: dict[str, Any]) -> None:
    try:
        from ..db import engine as db_engine

        with db_engine().begin() as conn:
            meta_patch = json.dumps({k: v for k, v in info.items() if v is not None})
            name = info.get("publication_name")
            if name:
                conn.execute(sa_text(
                    "UPDATE channel SET metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:m AS jsonb), "
                    "name = :n FROM source "
                    "WHERE channel.source_id = source.id AND source.code = 'substack' "
                    "AND channel.handle = :h"
                ), {"m": meta_patch, "h": handle, "n": name})
            else:
                conn.execute(sa_text(
                    "UPDATE channel SET metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:m AS jsonb) "
                    "FROM source "
                    "WHERE channel.source_id = source.id AND source.code = 'substack' "
                    "AND channel.handle = :h"
                ), {"m": meta_patch, "h": handle})
    except Exception as exc:
        get_logger("scraper.substack").warning(
            "failed to cache publication metadata for %s: %s", handle, exc,
        )


class SubstackScraper(BaseScraper):
    code = "substack"
    name = "Substack"

    def __init__(
        self,
        *,
        filter_year: int | None = None,
        filter_handle: str | None = None,
        filter_display_name: str | None = None,
        cookies_from_browser: str | None = None,
    ) -> None:
        super().__init__()
        s = settings()
        interval = max(s.substack_rate_limit_sec, s.scrape_rate_limit_sec)
        self.limiter = HostRateLimiter(interval, jitter=1.0)
        self.headers = {
            **self.headers,
            "Accept": "application/json, text/plain, */*",
        }
        self.filter_year = filter_year
        self.filter_handle = normalize_handle(filter_handle) if filter_handle else None
        self.filter_display_name = filter_display_name
        self._cookies_from_browser = (
            cookies_from_browser if cookies_from_browser is not None
            else s.substack_cookies_from_browser
        )
        self._sid_cache: str | None = None

    async def polite_get(self, client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
        """Substack-specific GET with per-host spacing and 429 backoff."""
        await self.limiter.wait(url)
        max_attempts = settings().scrape_max_retries + 2
        for attempt in range(max_attempts):
            r = await client.get(url, **kw)
            if r.status_code == 429:
                ra = max(
                    float(r.headers.get("Retry-After", str(_MIN_429_BACKOFF_SEC))),
                    _MIN_429_BACKOFF_SEC,
                )
                self.log.warning(
                    "429 from Substack; sleeping %.1fs (attempt %d/%d)",
                    ra, attempt + 1, max_attempts,
                )
                await asyncio.sleep(ra + random.uniform(0, 2))
                await self.limiter.wait(url)
                continue
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                await self.limiter.wait(url)
                continue
            return r
        r.raise_for_status()
        return r

    def _cookies(self) -> dict[str, str]:
        if self._sid_cache:
            return {"substack.sid": self._sid_cache}

        sid = settings().substack_session_cookie.strip()
        if not sid:
            sid = _load_sid_from_file() or ""
        if not sid and self._cookies_from_browser.strip():
            try:
                sid = _load_sid_from_browser(self._cookies_from_browser.strip()) or ""
                if sid:
                    self.log.info(
                        "loaded Substack substack.sid from browser %r",
                        self._cookies_from_browser.strip(),
                    )
            except Exception as exc:
                self.log.warning("browser cookie load failed: %s", exc)
        if sid:
            self._sid_cache = sid
            return {"substack.sid": sid}
        return {}

    def _ensure_session(self) -> None:
        if not self._cookies():
            raise RuntimeError(
                "No Substack session. Run `kb substack prime-session` to log in "
                "interactively, set SUBSTACK_SESSION_COOKIE in .env, or set "
                "SUBSTACK_COOKIES_FROM_BROWSER=chrome (while logged into substack.com)."
            )

    async def _api_get(
        self, client: httpx.AsyncClient, url: str, *, cookies: dict[str, str] | None = None,
    ) -> Any:
        r = await self.polite_get(client, url, cookies=cookies or {})
        if r.status_code == 401:
            raise RuntimeError(
                "Substack session expired or invalid (401). "
                "Refresh it with `kb substack prime-session`."
            )
        r.raise_for_status()
        return r.json()

    async def resolve_publication(
        self, client: httpx.AsyncClient, handle: str, metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        meta = metadata or {}
        if meta.get("subdomain"):
            return {
                "subdomain": meta["subdomain"],
                "custom_domain": meta.get("custom_domain"),
                "custom_domain_optional": meta.get("custom_domain_optional", True),
                "publication_id": meta.get("publication_id"),
                "publication_name": meta.get("publication_name"),
            }

        try:
            data = await self._api_get(client, _public_profile_url(handle))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Not a writer's profile handle — assume it *is* the subdomain.
                self.log.info(
                    "no public profile for %r — treating it as a publication subdomain",
                    handle,
                )
                return {
                    "subdomain": handle,
                    "custom_domain": None,
                    "custom_domain_optional": True,
                    "publication_id": None,
                    "publication_name": None,
                }
            raise

        pub_users = data.get("publicationUsers") or []
        primary = (
            next((pu for pu in pub_users if pu.get("is_primary")), None)
            or (pub_users[0] if pub_users else None)
        )
        if not primary or not primary.get("publication"):
            raise ValueError(
                f"no publication found for Substack handle {handle!r} "
                "(is this a writer's profile?)"
            )
        pub = primary["publication"]
        subdomain = pub.get("subdomain")
        if not subdomain:
            raise ValueError(f"publication for {handle!r} has no subdomain")

        info = {
            "subdomain": subdomain,
            "custom_domain": pub.get("custom_domain"),
            "custom_domain_optional": pub.get("custom_domain_optional", True),
            "publication_id": pub.get("id"),
            "publication_name": pub.get("name"),
        }
        _save_publication_meta(handle, info)
        return info

    async def _fetch_post_page_content(self, post_url: str) -> str:
        """Render the post with a logged-in browser (handles custom-domain auth sync)."""
        self._ensure_session()

        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        sid = self._cookies().get("substack.sid")
        self.log.info("fetching rendered post content %s", post_url)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(user_agent=settings().scrape_user_agent)
                try:
                    if sid:
                        await context.add_cookies([{
                            "name": "substack.sid",
                            "value": sid,
                            "domain": ".substack.com",
                            "path": "/",
                            "secure": True,
                            "httpOnly": True,
                            "sameSite": "Lax",
                        }])
                    page = await context.new_page()
                    await self.limiter.wait(post_url)
                    await page.goto(post_url, wait_until="domcontentloaded", timeout=120_000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=30_000)
                    except PlaywrightTimeoutError:
                        pass
                    await asyncio.sleep(_PAGE_SETTLE_SEC)
                    html = await page.evaluate("""selectors => {
                        const norm = (s) => (s || '')
                            .replace(/\u00a0/g, ' ')
                            .replace(/[ \t]+/g, ' ')
                            .trim();
                        const visible = (el) => {
                            const style = getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return style.visibility !== 'hidden'
                                && style.display !== 'none'
                                && rect.width > 0
                                && rect.height > 0;
                        };
                        const candidates = [];
                        for (const selector of selectors) {
                            for (const el of document.querySelectorAll(selector)) {
                                if (!visible(el)) continue;
                                const text = norm(el.innerText || el.textContent);
                                if (!text) continue;
                                candidates.push({html: el.innerHTML, textLength: text.length});
                            }
                        }
                        candidates.sort((a, b) => b.textLength - a.textLength);
                        return candidates[0]?.html || '';
                    }""", _POST_PAGE_CONTENT_SELECTORS)
                    return html.strip() if isinstance(html, str) else ""
                finally:
                    await context.close()
            finally:
                await browser.close()

    def _post_matches_year(self, published_at: datetime | None) -> bool:
        if self.filter_year is None:
            return True
        if published_at is None:
            return False
        return published_at.year == self.filter_year

    def _past_filter_year(self, published_at: datetime | None) -> bool:
        if self.filter_year is None or published_at is None:
            return False
        return published_at.year < self.filter_year

    async def check_session(self) -> dict[str, Any]:
        self._ensure_session()
        async with await self.http() as client:
            data = await self._api_get(client, _subscriptions_url(), cookies=self._cookies())
        if not isinstance(data, dict):
            data = {}
        # "subscriptions" holds {publication: {...}} wrapper entries (paid plans);
        # "publications" holds plain publication objects (paid + free follows).
        subs = data.get("subscriptions") or []
        pubs = data.get("publications") or []
        names = [(s.get("publication") or {}).get("name") for s in subs if isinstance(s, dict)]
        names += [p.get("name") for p in pubs if isinstance(p, dict)]
        names = [n for n in names if n]
        return {"count": len(names), "publications": names}

    async def prime_session(self, wait_sec: float = 600.0) -> bool:
        """Open substack.com in a browser; save substack.sid after manual login."""
        from playwright.async_api import async_playwright

        profile = DATA_DIR / "substack" / ".browser_profile"
        profile.mkdir(parents=True, exist_ok=True)
        login_url = f"{SUBSTACK_ROOT}/sign-in"

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(profile),
                headless=False,
                user_agent=settings().scrape_user_agent,
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            self.log.info("open %s — log in if prompted", login_url)
            await page.goto(login_url, wait_until="domcontentloaded", timeout=120_000)

            deadline = asyncio.get_running_loop().time() + wait_sec
            while asyncio.get_running_loop().time() < deadline:
                cookies = await ctx.cookies(SUBSTACK_ROOT)
                sid = next((c["value"] for c in cookies if c["name"] == "substack.sid"), "")
                if sid:
                    try:
                        async with await self.http() as client:
                            self._sid_cache = sid
                            await self._api_get(client, _subscriptions_url(), cookies=self._cookies())
                        save_sid(sid)
                        self.log.info("session saved to %s", SESSION_PATH)
                        await ctx.close()
                        return True
                    except Exception as exc:
                        self.log.debug("session not ready yet: %s", exc)
                await asyncio.sleep(2)

            await ctx.close()
        return False

    @staticmethod
    def _content_path_for(d: dict) -> Path:
        published = d.get("published_at")
        if isinstance(published, datetime):
            year = published.strftime("%Y")
            date_fmt = published.strftime("%Y-%m-%d")
        else:
            year = date_fmt = "undated"
        title = d.get("title") or d["external_id"]
        stem = f"{date_fmt}-{slugify(title, 80)}"
        return DATA_DIR / "substack" / slugify(d["channel_handle"]) / year / f"{stem}.md"

    def already_scraped(self, d: dict) -> bool:
        path = self._content_path_for(d)
        return path.exists() and path.stat().st_size > 50

    async def discover(self, limit: int | None = None) -> AsyncIterator[dict]:
        if self.filter_handle:
            channels = [{
                "handle": self.filter_handle,
                "name": self.filter_display_name or self.filter_handle,
                "metadata": {},
            }]
            db_channels = _load_channels(self.filter_handle)
            if db_channels:
                channels = db_channels
        else:
            channels = _load_channels()

        if not channels:
            self.log.warning(
                "No Substack publications in DB. Add with: "
                "kb scrape add-channel substack <handle> \"Name\""
            )
            return

        matched = 0
        async with await self.http() as client:
            for i, ch in enumerate(channels):
                if i > 0:
                    await asyncio.sleep(_BETWEEN_CHANNELS_SEC)

                handle = ch["handle"]
                display = ch["name"]
                try:
                    pub = await self.resolve_publication(client, handle, ch.get("metadata"))
                except Exception as exc:
                    self.log.error("publication resolve failed for %s: %s", handle, exc)
                    continue

                subdomain = pub["subdomain"]
                offset = 0
                stop = False
                while not stop:
                    url = _archive_url(subdomain, offset)
                    try:
                        page = await self._api_get(client, url)
                    except Exception as exc:
                        self.log.error("archive fetch failed for %s at offset %d: %s", handle, offset, exc)
                        break
                    if not isinstance(page, list) or not page:
                        break

                    for post in page:
                        published_at = _parse_dt(post.get("post_date"))
                        if self._past_filter_year(published_at):
                            stop = True
                            break
                        if not self._post_matches_year(published_at):
                            continue

                        slug = post.get("slug") or ""
                        canonical_url = post.get("canonical_url") or (
                            f"https://{subdomain}.substack.com/p/{slug}" if slug else None
                        )
                        if not slug or not canonical_url:
                            continue

                        matched += 1
                        yield {
                            "external_id": str(post.get("id") or slug),
                            "slug": slug,
                            "url": canonical_url,
                            "title": post.get("title") or slug,
                            "subtitle": post.get("subtitle"),
                            "published_at": published_at,
                            "audience": post.get("audience") or "everyone",
                            "wordcount": post.get("wordcount"),
                            "channel_handle": handle,
                            "channel_name": display,
                            "subdomain": subdomain,
                        }
                        if limit and matched >= limit:
                            stop = True
                            break

                    offset += len(page)
                    if len(page) < ARCHIVE_PAGE_SIZE:
                        break
                    if not stop:
                        await asyncio.sleep(_BETWEEN_PAGES_SEC)

    async def fetch(self, d: dict) -> ScrapedItem | None:
        subdomain = d["subdomain"]
        slug = d.get("slug") or ""
        url = d["url"]
        audience = d.get("audience", "everyone")
        title = (d.get("title") or slug or d["external_id"]).strip()
        published_at = d.get("published_at")

        detail: dict[str, Any] = {}
        async with await self.http() as client:
            try:
                detail = await self._api_get(
                    client, _post_detail_url(subdomain, slug), cookies=self._cookies(),
                )
            except Exception as exc:  # noqa: BLE001
                self.log.warning("post detail fetch failed for %s: %s", url, exc)

        body_html = (detail.get("body_html") or "").strip()
        wordcount = int(detail.get("wordcount") or d.get("wordcount") or 0)
        looks_complete = bool(body_html) and (
            wordcount == 0 or _html_wordcount(body_html) >= wordcount * _COMPLETE_WORDCOUNT_RATIO
        )

        rendered_fallback_used = False
        if audience != "everyone" and not looks_complete:
            if self._cookies():
                try:
                    rendered = await self._fetch_post_page_content(url)
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("rendered post content fetch failed %s: %s", url, exc)
                    rendered = ""
                if rendered and _html_wordcount(rendered) > _html_wordcount(body_html):
                    body_html = rendered
                    rendered_fallback_used = True
            else:
                self.log.info(
                    "no Substack session configured — %s may only include a free preview", url,
                )

        if published_at is None:
            published_at = _parse_dt(detail.get("post_date"))
        title = (detail.get("title") or title or "").strip() or d["external_id"]
        subtitle = (detail.get("subtitle") or d.get("subtitle") or "").strip()

        body_content = _html_to_md(body_html) if body_html else ""
        pub_line = (
            published_at.date().isoformat()
            if isinstance(published_at, datetime)
            else "unknown"
        )
        subtitle_line = f"{subtitle}\n\n" if subtitle else ""
        body = (
            f"# {title}\n\n"
            f"- Publication: {d['channel_name']} ({d['channel_handle']})\n"
            f"- URL: {url}\n"
            f"- Published: {pub_line}\n"
            f"- Audience: {audience}\n\n"
            f"{subtitle_line}"
            f"{body_content or '_(no text content — paywalled and no Substack session configured)_'}\n"
        )

        date_part = (
            published_at.strftime("%Y-%m-%d")
            if isinstance(published_at, datetime) else "undated"
        )
        folder_name = f"{date_part}-{slugify(title, 80)}"

        return ScrapedItem(
            source=self.code,
            channel=d["channel_handle"],
            channel_name=d["channel_name"],
            external_id=d["external_id"],
            title=title,
            url=url,
            published_at=published_at,
            language="en",
            body_md=body,
            raw_html=body_html or None,
            extra={
                "audience": audience,
                "slug": slug,
                "subdomain": subdomain,
                "wordcount": wordcount or None,
                "rendered_fallback_used": rendered_fallback_used,
                "year": published_at.year if isinstance(published_at, datetime) else None,
            },
            folder_name=folder_name,
            flat_layout=True,
        )
