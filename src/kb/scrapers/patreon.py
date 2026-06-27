"""Patreon scraper using Patreon's internal JSON API.

Requires a browser ``session_id`` cookie (``PATREON_SESSION_ID`` in .env, or
``PATREON_COOKIES_FROM_BROWSER``) to read patron-visible posts for DB-registered
creators.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from markdownify import markdownify
from sqlalchemy import text as sa_text

from ..config import DATA_DIR, settings
from ..io_md import slugify
from ..logging_setup import get_logger
from ..ratelimit import HostRateLimiter
from .base import BaseScraper, ScrapedItem

API_ROOT = "https://www.patreon.com/api"
PATREON_ROOT = "https://www.patreon.com"
SESSION_PATH = DATA_DIR / "patreon" / ".session.json"
# Extra pauses on top of HostRateLimiter (see patreon_rate_limit_sec in settings).
_BETWEEN_PAGES_SEC = 2.0
_BETWEEN_CREATORS_SEC = 3.0
_BETWEEN_POST_FETCH_SEC = 1.5
_PAGE_SETTLE_SEC = 2.0
_MIN_429_BACKOFF_SEC = 30.0

_POST_FIELDS = [
    "title",
    "content",
    "published_at",
    "url",
    "patreon_url",
    "current_user_can_view",
    "post_type",
    "is_paid",
    "edited_at",
    "teaser_text",
]
_POST_INCLUDES = ["user", "campaign"]
_CAMPAIGN_FIELDS = ["name", "url", "vanity"]

_BROWSER_LOADERS: dict[str, str] = {
    "chrome": "chrome",
    "chromium": "chromium",
    "edge": "edge",
    "firefox": "firefox",
    "brave": "brave",
    "opera": "opera",
    "vivaldi": "vivaldi",
}


def normalize_vanity(handle: str) -> str:
    """Extract Patreon vanity from slug, URL, or c/<vanity>/posts link."""
    raw = handle.strip().lstrip("@")
    m = re.search(r"[?&]vanity=([^&]+)", raw, flags=re.I)
    if m:
        return m.group(1).strip()

    if raw.startswith("http"):
        parsed = urlparse(raw)
        raw = parsed.path.lstrip("/")
        if not m:
            q = parse_qs(parsed.query)
            if q.get("vanity"):
                return q["vanity"][0].strip()

    raw = re.sub(r"^https?://(?:www\.)?patreon\.com/", "", handle.strip(), flags=re.I)
    raw = raw.split("?")[0].strip("/")
    parts = [p for p in raw.split("/") if p]
    if parts and parts[0] in ("c", "cw"):
        return parts[1] if len(parts) > 1 else parts[0]
    if parts and parts[0] == "user":
        return parts[1] if len(parts) > 1 else ""
    return parts[0] if parts else raw


def _posts_list_url(campaign_id: str) -> str:
    params: list[tuple[str, str]] = [
        ("include", ",".join(_POST_INCLUDES)),
        ("fields[post]", ",".join(_POST_FIELDS)),
        ("fields[user]", "full_name,url"),
        ("fields[campaign]", "name,url"),
        ("sort", "-published_at"),
        ("filter[is_draft]", "false"),
        ("filter[contains_exclusive_posts]", "true"),
        ("filter[campaign_id]", campaign_id),
        ("json-api-use-default-includes", "false"),
        ("json-api-version", "1.0"),
    ]
    return f"{API_ROOT}/posts?{urlencode(params)}"


def _post_detail_url(post_id: str) -> str:
    params: list[tuple[str, str]] = [
        ("include", ",".join(_POST_INCLUDES)),
        ("fields[post]", ",".join(_POST_FIELDS)),
        ("fields[user]", "full_name,url"),
        ("fields[campaign]", "name,url"),
        ("json-api-use-default-includes", "false"),
        ("json-api-version", "1.0"),
    ]
    return f"{API_ROOT}/posts/{post_id}?{urlencode(params)}"


def _campaign_lookup_url(vanity: str) -> str:
    params = [
        ("filter[vanity]", vanity),
        ("fields[campaign]", ",".join(_CAMPAIGN_FIELDS)),
        ("json-api-version", "1.0"),
    ]
    return f"{API_ROOT}/campaigns?{urlencode(params)}"


def _current_user_url() -> str:
    params = [
        ("include", "campaign.null"),
        ("fields[user]", "full_name,image_url,url"),
        ("json-api-version", "1.0"),
    ]
    return f"{API_ROOT}/current_user?{urlencode(params)}"


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


def _load_session_id_from_file() -> str | None:
    if not SESSION_PATH.exists():
        return None
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        sid = (data.get("session_id") or "").strip()
        return sid or None
    except Exception:
        return None


def save_session_id(session_id: str) -> Path:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(
        json.dumps({"session_id": session_id}, indent=2),
        encoding="utf-8",
    )
    return SESSION_PATH


def _load_session_id_from_browser(spec: str) -> str | None:
    """Read session_id from a local browser profile (e.g. chrome, edge)."""
    import browser_cookie3

    browser_name, _, profile = spec.partition(":")
    loader_name = _BROWSER_LOADERS.get(browser_name.lower())
    if not loader_name:
        raise ValueError(
            f"unsupported browser {browser_name!r}; "
            f"use one of: {', '.join(sorted(_BROWSER_LOADERS))}"
        )
    loader = getattr(browser_cookie3, loader_name)
    kwargs: dict[str, Any] = {"domain_name": ".patreon.com"}
    if profile:
        kwargs["profile"] = profile
    for cookie in loader(**kwargs):
        if cookie.name == "session_id" and cookie.value:
            return cookie.value
    return None


def _load_channels(handle: str | None = None) -> list[dict[str, Any]]:
    try:
        from ..db import engine as db_engine

        sql = (
            "SELECT c.handle, c.name, c.metadata FROM channel c "
            "JOIN source s ON c.source_id = s.id WHERE s.code = 'patreon' "
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


def _save_campaign_id(handle: str, campaign_id: str, name: str | None = None) -> None:
    try:
        from ..db import engine as db_engine

        with db_engine().begin() as conn:
            meta_patch = json.dumps({"campaign_id": campaign_id})
            if name:
                conn.execute(sa_text(
                    "UPDATE channel SET metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:m AS jsonb), "
                    "name = :n FROM source "
                    "WHERE channel.source_id = source.id AND source.code = 'patreon' "
                    "AND channel.handle = :h"
                ), {"m": meta_patch, "h": handle, "n": name})
            else:
                conn.execute(sa_text(
                    "UPDATE channel SET metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:m AS jsonb) "
                    "FROM source "
                    "WHERE channel.source_id = source.id AND source.code = 'patreon' "
                    "AND channel.handle = :h"
                ), {"m": meta_patch, "h": handle})
    except Exception as exc:
        get_logger("scraper.patreon").warning(
            "failed to cache campaign_id for %s: %s", handle, exc,
        )


class PatreonScraper(BaseScraper):
    code = "patreon"
    name = "Patreon"

    def __init__(
        self,
        *,
        filter_year: int | None = None,
        filter_handle: str | None = None,
        filter_display_name: str | None = None,
        cookies_from_browser: str | None = None,
        use_browser: bool = False,
    ) -> None:
        super().__init__()
        s = settings()
        interval = max(s.patreon_rate_limit_sec, s.scrape_rate_limit_sec)
        self.limiter = HostRateLimiter(interval, jitter=1.5)
        self.headers = {
            **self.headers,
            "Accept": "application/vnd.api+json, application/json",
            "Referer": f"{PATREON_ROOT}/",
        }
        self.filter_year = filter_year
        self.filter_handle = normalize_vanity(filter_handle) if filter_handle else None
        self.filter_display_name = filter_display_name
        self._cookies_from_browser = (
            cookies_from_browser if cookies_from_browser is not None
            else s.patreon_cookies_from_browser
        )
        self.use_browser = use_browser
        self._session_id_cache: str | None = None

    async def polite_get(self, client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
        """Patreon-specific GET with per-host spacing and conservative 429 backoff."""
        await self.limiter.wait(url)
        max_attempts = settings().scrape_max_retries + 3
        for attempt in range(max_attempts):
            r = await client.get(url, **kw)
            if r.status_code == 429:
                ra = max(
                    float(r.headers.get("Retry-After", str(_MIN_429_BACKOFF_SEC))),
                    _MIN_429_BACKOFF_SEC,
                )
                self.log.warning(
                    "429 from Patreon; sleeping %.1fs (attempt %d/%d)",
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
        if self._session_id_cache:
            return {"session_id": self._session_id_cache}

        sid = settings().patreon_session_id.strip()
        if not sid:
            sid = _load_session_id_from_file() or ""
        if not sid and self._cookies_from_browser.strip():
            try:
                sid = _load_session_id_from_browser(self._cookies_from_browser.strip()) or ""
                if sid:
                    self.log.info(
                        "loaded Patreon session_id from browser %r",
                        self._cookies_from_browser.strip(),
                    )
            except Exception as exc:
                self.log.warning("browser cookie load failed: %s", exc)
        if sid:
            self._session_id_cache = sid
            return {"session_id": sid}
        return {}

    def _ensure_session(self) -> None:
        if not self._cookies():
            raise RuntimeError(
                "No Patreon session. Set PATREON_SESSION_ID in .env, run "
                "`kb patreon prime-session`, or set PATREON_COOKIES_FROM_BROWSER=chrome "
                "(while logged into patreon.com), then: kb patreon check-session"
            )

    async def _api_get(self, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        r = await self.polite_get(client, url, cookies=self._cookies())
        if r.status_code == 401:
            raise RuntimeError(
                "Patreon session expired or invalid (401). "
                "Refresh PATREON_SESSION_ID or re-login in your browser."
            )
        r.raise_for_status()
        return r.json()

    async def resolve_campaign_id(
        self, client: httpx.AsyncClient, handle: str, metadata: dict[str, Any] | None = None,
    ) -> str:
        meta = metadata or {}
        cached = meta.get("campaign_id")
        if cached:
            return str(cached)
        if re.fullmatch(r"\d+", handle.strip()):
            return handle.strip()

        vanity = normalize_vanity(handle)
        if not vanity:
            raise ValueError(f"invalid Patreon handle: {handle!r}")

        data = await self._api_get(client, _campaign_lookup_url(vanity))
        campaigns = data.get("data") or []
        if not campaigns:
            raise ValueError(f"no Patreon campaign found for vanity {vanity!r}")
        campaign_id = str(campaigns[0]["id"])
        name = (campaigns[0].get("attributes") or {}).get("name")
        _save_campaign_id(handle, campaign_id, name)
        return campaign_id

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post_id: str,
    ) -> dict[str, Any]:
        """Open individual post (API equivalent of clicking into a post)."""
        self.log.info("fetching post detail %s", post_id)
        data = await self._api_get(client, _post_detail_url(post_id))
        post = data.get("data") or {}
        return post.get("attributes") or {}

    def _post_matches_year(self, published_at: datetime | None) -> bool:
        if self.filter_year is None:
            return True
        if published_at is None:
            return False
        return published_at.year == self.filter_year

    def _past_filter_year(self, published_at: datetime | None) -> bool:
        """True when sorted newest-first and we've scrolled past the target year."""
        if self.filter_year is None or published_at is None:
            return False
        return published_at.year < self.filter_year

    async def list_years(self, handle: str) -> dict[int, int]:
        """Return {year: post_count} for a creator (paginates all posts)."""
        counts: dict[int, int] = {}
        vanity = normalize_vanity(handle)
        async with await self.http() as client:
            campaign_id = await self.resolve_campaign_id(client, vanity)
            url: str | None = _posts_list_url(campaign_id)
            while url:
                page = await self._api_get(client, url)
                for post in page.get("data") or []:
                    attrs = post.get("attributes") or {}
                    if not attrs.get("current_user_can_view", True):
                        continue
                    published_at = _parse_dt(attrs.get("published_at"))
                    if published_at:
                        counts[published_at.year] = counts.get(published_at.year, 0) + 1
                links = page.get("links") or {}
                url = links.get("next") or None
                if url:
                    await asyncio.sleep(_BETWEEN_PAGES_SEC)
        return dict(sorted(counts.items()))

    async def check_session(self) -> dict[str, Any]:
        self._ensure_session()
        async with await self.http() as client:
            data = await self._api_get(client, _current_user_url())
        user = data.get("data") or {}
        attrs = user.get("attributes") or {}
        return {
            "id": user.get("id"),
            "full_name": attrs.get("full_name"),
            "url": attrs.get("url"),
        }

    def already_scraped(self, d: dict) -> bool:
        published = d.get("published_at")
        if isinstance(published, datetime):
            date_fmt = published.strftime("%Y-%m-%d")
        else:
            date_fmt = "undated"
        folder = (
            DATA_DIR / "patreon" / slugify(d["channel_handle"])
            / f"{date_fmt}__{slugify(d['external_id'], 60)}"
        )
        path = folder / "content.md"
        return path.exists() and path.stat().st_size > 50

    @staticmethod
    def _post_id_from_url(url: str) -> str:
        m = re.search(r"/posts/(?:[^/?#]+-)?(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/posts/([^/?#]+)", url)
        return m.group(1) if m else slugify(url, 60)

    async def _sync_session_from_browser(self, ctx) -> None:
        cookies = await ctx.cookies(PATREON_ROOT)
        sid = next((c["value"] for c in cookies if c["name"] == "session_id"), "")
        if sid:
            self._session_id_cache = sid
            save_session_id(sid)

    async def _apply_year_filter(self, page, year: int) -> None:
        """Click Patreon posts-page year filter (UI equivalent)."""
        self.log.info("filtering posts to year %s", year)
        for label in (str(year), f"{year}"):
            try:
                btn = page.get_by_role("button", name=re.compile(r"year|date|filter", re.I))
                if await btn.count():
                    await btn.first.click(timeout=5000)
                    await asyncio.sleep(1)
                opt = page.get_by_role("menuitem", name=label)
                if await opt.count():
                    await opt.first.click(timeout=5000)
                    await page.wait_for_load_state("networkidle", timeout=30_000)
                    return
                opt = page.get_by_text(label, exact=True)
                if await opt.count():
                    await opt.first.click(timeout=5000)
                    await page.wait_for_load_state("networkidle", timeout=30_000)
                    return
            except Exception as exc:
                self.log.debug("year filter attempt failed: %s", exc)
        self.log.warning(
            "could not click year filter for %s; will filter by published date after scrape",
            year,
        )

    async def _collect_post_urls(self, page, max_posts: int) -> list[str]:
        """Scroll posts feed and collect post URLs (infinite scroll)."""
        seen: list[str] = []
        seen_set: set[str] = set()
        stagnant = 0
        while len(seen) < max_posts and stagnant < 4:
            batch = await page.eval_on_selector_all(
                'a[href*="/posts/"]',
                """els => {
                    const out = [];
                    const seen = new Set();
                    for (const e of els) {
                        const h = e.href.split('?')[0];
                        if (!h.includes('/posts/') || h.endsWith('/posts')) continue;
                        if (seen.has(h)) continue;
                        seen.add(h);
                        out.push(h);
                    }
                    return out;
                }""",
            )
            before = len(seen)
            for href in batch or []:
                if href not in seen_set:
                    seen_set.add(href)
                    seen.append(href)
                    if len(seen) >= max_posts:
                        break
            if len(seen) == before:
                stagnant += 1
            else:
                stagnant = 0
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(_BETWEEN_PAGES_SEC)

        self.log.info("collected %d post URLs from feed", len(seen))
        return seen[:max_posts]

    async def _extract_post_page(self, page, post_url: str) -> dict[str, Any]:
        """Navigate into a post and extract title/content (click-through)."""
        await self.limiter.wait(post_url)
        await page.goto(post_url, wait_until="domcontentloaded", timeout=120_000)
        await asyncio.sleep(_PAGE_SETTLE_SEC)

        meta = await page.evaluate("""() => {
            const titleEl = document.querySelector('h1, [data-tag="post-title"]');
            const timeEl = document.querySelector('time[datetime]');
            const contentEl = document.querySelector(
                '[data-tag="post-content"], article [class*="post-content"], .post-content'
            );
            return {
                title: titleEl ? titleEl.innerText.trim() : document.title,
                published: timeEl ? timeEl.getAttribute('datetime') : null,
                html: contentEl ? contentEl.innerHTML : null,
            };
        }""")
        title = (meta.get("title") or "").strip() or self._post_id_from_url(post_url)
        published_at = _parse_dt(meta.get("published"))
        content_html = meta.get("html") or ""
        post_id = self._post_id_from_url(post_url)
        return {
            "external_id": post_id,
            "url": post_url,
            "title": title,
            "published_at": published_at,
            "content_html": content_html,
            "teaser_text": "",
            "post_type": None,
            "is_paid": None,
            "patreon_url": post_url,
        }

    async def _discover_via_browser(self, limit: int | None) -> AsyncIterator[dict]:
        from playwright.async_api import async_playwright

        vanity = self.filter_handle
        if not vanity:
            channels = _load_channels()
            if not channels:
                self.log.warning("no creator specified for browser scrape")
                return
            vanity = normalize_vanity(channels[0]["handle"])
        display = self.filter_display_name or vanity
        posts_url = f"{PATREON_ROOT}/c/{vanity}/posts"
        max_posts = limit or 50

        profile = DATA_DIR / "patreon" / ".browser_profile"
        profile.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(profile),
                headless=False,
                user_agent=settings().scrape_user_agent,
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await self._sync_session_from_browser(ctx)

            self.log.info("browser: open %s", posts_url)
            await page.goto(posts_url, wait_until="domcontentloaded", timeout=120_000)
            await asyncio.sleep(_PAGE_SETTLE_SEC)

            if self.filter_year:
                await self._apply_year_filter(page, self.filter_year)

            post_urls = await self._collect_post_urls(page, max_posts)
            if not post_urls:
                self.log.warning("no post links found on %s", posts_url)
                await ctx.close()
                return

            for post_url in post_urls:
                if self.filter_year:
                    # quick year check from URL listing date if visible — refined on post page
                    pass
                try:
                    extracted = await self._extract_post_page(page, post_url)
                except Exception as exc:
                    self.log.warning("post extract failed %s: %s", post_url, exc)
                    continue

                published_at = extracted.get("published_at")
                if not self._post_matches_year(published_at):
                    continue

                yield {
                    **extracted,
                    "channel_handle": vanity,
                    "channel_name": display,
                    "campaign_id": None,
                }
                await asyncio.sleep(_BETWEEN_POST_FETCH_SEC)

            await ctx.close()

    async def discover(self, limit: int | None = None) -> AsyncIterator[dict]:
        if self.use_browser:
            async for item in self._discover_via_browser(limit):
                yield item
            return

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
                "No Patreon creators in DB. Add with: kb scrape add-channel patreon <vanity> \"Name\""
            )
            return

        matched = 0
        async with await self.http() as client:
            for i, ch in enumerate(channels):
                if i > 0:
                    await asyncio.sleep(_BETWEEN_CREATORS_SEC)

                handle = ch["handle"]
                display = ch["name"]
                try:
                    campaign_id = await self.resolve_campaign_id(
                        client, handle, ch.get("metadata"),
                    )
                except Exception as exc:
                    self.log.error("campaign resolve failed for %s: %s", handle, exc)
                    continue

                url = _posts_list_url(campaign_id)
                skipped_locked = 0
                skipped_year = 0
                page_num = 0
                stop_pagination = False
                while url and not stop_pagination:
                    page_num += 1
                    try:
                        page = await self._api_get(client, url)
                    except Exception as exc:
                        self.log.error("posts fetch failed for %s: %s", handle, exc)
                        break

                    page_posts = page.get("data") or []
                    viewable_on_page = sum(
                        1 for p in page_posts
                        if (p.get("attributes") or {}).get("current_user_can_view", True)
                    )
                    if page_num == 1 and page_posts and viewable_on_page == 0:
                        self.log.error(
                            "no patron-visible posts for %s — you are not logged in or not "
                            "subscribed. Run: kb patreon prime-session --creator %s",
                            handle, normalize_vanity(handle),
                        )
                        break

                    for post in page_posts:
                        attrs = post.get("attributes") or {}
                        if not attrs.get("current_user_can_view", True):
                            skipped_locked += 1
                            continue

                        post_id = str(post.get("id") or "")
                        if not post_id:
                            continue

                        published_at = _parse_dt(attrs.get("published_at"))
                        if self._past_filter_year(published_at):
                            stop_pagination = True
                            break
                        if not self._post_matches_year(published_at):
                            skipped_year += 1
                            continue

                        content_html = attrs.get("content") or ""
                        if not content_html.strip():
                            await asyncio.sleep(_BETWEEN_POST_FETCH_SEC)
                            detail_attrs = await self._fetch_post_detail(client, post_id)
                            content_html = detail_attrs.get("content") or content_html
                            if not attrs.get("title"):
                                attrs["title"] = detail_attrs.get("title")
                            if not attrs.get("teaser_text"):
                                attrs["teaser_text"] = detail_attrs.get("teaser_text")

                        post_url = attrs.get("url") or attrs.get("patreon_url") or ""
                        matched += 1
                        yield {
                            "external_id": post_id,
                            "url": post_url,
                            "title": (attrs.get("title") or "").strip() or post_id,
                            "published_at": published_at,
                            "channel_handle": handle,
                            "channel_name": display,
                            "campaign_id": campaign_id,
                            "content_html": content_html,
                            "post_type": attrs.get("post_type"),
                            "is_paid": attrs.get("is_paid"),
                            "patreon_url": attrs.get("patreon_url"),
                            "teaser_text": attrs.get("teaser_text"),
                        }

                        if limit and matched >= limit:
                            stop_pagination = True
                            break

                    if stop_pagination:
                        break

                    links = page.get("links") or {}
                    url = links.get("next") or ""
                    if url:
                        self.log.debug(
                            "next page %d for %s; pausing %.1fs",
                            page_num + 1, handle, _BETWEEN_PAGES_SEC,
                        )
                        await asyncio.sleep(_BETWEEN_PAGES_SEC)

                if skipped_locked:
                    self.log.info(
                        "skipped %d tier-locked posts for %s", skipped_locked, handle,
                    )
                if skipped_year:
                    self.log.info(
                        "skipped %d posts outside year %s for %s",
                        skipped_year, self.filter_year, handle,
                    )

    async def fetch(self, d: dict) -> ScrapedItem | None:
        title = d["title"]
        published_at = d.get("published_at")
        content_html = d.get("content_html") or ""
        teaser = (d.get("teaser_text") or "").strip()

        if not content_html and teaser:
            body_content = teaser
            raw_html = None
        else:
            body_content = _html_to_md(content_html)
            raw_html = content_html if content_html else None

        pub_line = (
            published_at.date().isoformat()
            if isinstance(published_at, datetime)
            else "unknown"
        )
        body = (
            f"# {title}\n\n"
            f"- Creator: {d['channel_name']} ({d['channel_handle']})\n"
            f"- URL: {d['url']}\n"
            f"- Published: {pub_line}\n\n"
            f"{body_content or '_(no text content)_'}\n"
        )

        return ScrapedItem(
            source="patreon",
            channel=d["channel_handle"],
            channel_name=d["channel_name"],
            external_id=d["external_id"],
            title=title,
            url=d["url"],
            published_at=published_at,
            language="en",
            body_md=body,
            raw_html=raw_html,
            extra={
                "post_type": d.get("post_type"),
                "is_paid": d.get("is_paid"),
                "campaign_id": d.get("campaign_id"),
                "patreon_url": d.get("patreon_url"),
                "year": published_at.year if isinstance(published_at, datetime) else None,
            },
        )

    async def prime_session(
        self,
        creator: str = "aminvest",
        wait_sec: float = 600.0,
    ) -> bool:
        """Open Patreon in a browser; save session_id after manual login."""
        from playwright.async_api import async_playwright

        profile = DATA_DIR / "patreon" / ".browser_profile"
        profile.mkdir(parents=True, exist_ok=True)
        vanity = normalize_vanity(creator)
        posts_url = f"{PATREON_ROOT}/c/{vanity}/posts"

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(profile),
                headless=False,
                user_agent=settings().scrape_user_agent,
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            self.log.info("open %s — log in if prompted", posts_url)
            await page.goto(posts_url, wait_until="domcontentloaded", timeout=120_000)

            deadline = asyncio.get_running_loop().time() + wait_sec
            while asyncio.get_running_loop().time() < deadline:
                cookies = await ctx.cookies(PATREON_ROOT)
                sid = next((c["value"] for c in cookies if c["name"] == "session_id"), "")
                if sid:
                    try:
                        async with await self.http() as client:
                            self._session_id_cache = sid
                            await self._api_get(client, _current_user_url())
                        save_session_id(sid)
                        self.log.info("session saved to %s", SESSION_PATH)
                        await ctx.close()
                        return True
                    except Exception as exc:
                        self.log.debug("session not ready yet: %s", exc)
                await asyncio.sleep(2)

            await ctx.close()
        return False
