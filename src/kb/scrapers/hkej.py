"""HKEJ Wealth Management author scraper.

Improvements over the previous version:
- Logs in once via Playwright, persists ``storage_state`` to a JSON file, and
  reuses it across ``discover`` and every ``fetch`` call so we don't re-login
  per article.
- Tries multiple known HKEJ login endpoints since the site has redirected
  several times in recent years.
- Confirms login by reloading the homepage and looking for the logout link
  (or the absence of the login button).
- Folder layout on disk uses the *author name* (slugified) instead of the
  numeric author id, e.g. ``data/hkej/<author_name_slug>/<YYYY-MM-DD>__<id>/``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote, urlparse

from ..config import DATA_DIR, settings
from ..io_md import slugify
from ..ratelimit import HostRateLimiter
from .base import BaseScraper, ScrapedItem


BASE = "https://www.hkej.com"
SEARCH_BASE = "https://search.hkej.com"
ARTICLE_BASE = "https://www1.hkej.com"
LOGIN_URL = (
    "https://subscribe.hkej.com/member/login"
    "?forwardURL=%2F%2Fwww.hkej.com%2Flanding%2Findex"
)
MEMBER_COOKIE_NAMES = frozenset({
    "memberid", "hkej_login", "hkej_session", "hkej_member", "hkej_uid",
})
# Logged-in header: 歡迎（我的賬戶｜登出） inside #hkej_logon_menu_container_2014
# Logged-out header: top .hkej_upper_registration_btn_2014 link text is exactly「登入」
# Use a real Chrome user-agent for HKEJ — the subscribe.hkej.com redirect
# breaks ("chrome-error://chromewebdata/") with non-browser UAs.
HKEJ_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
STATE_PATH = DATA_DIR / "hkej" / ".auth_state.json"
BROWSER_PROFILE_DIR = DATA_DIR / "hkej" / ".browser_profile"
SESSION_STATE_PATH = DATA_DIR / "hkej" / ".browser_state.json"
# Extra pauses on top of HostRateLimiter (see hkej_rate_limit_sec in settings).
_PAGE_SETTLE_SEC = 2.0
_BETWEEN_PAGES_SEC = 1.5
_BETWEEN_AUTHORS_SEC = 2.0


def _author_dir_slug(author: dict) -> str:
    name = (author.get("name") or "").strip()
    if name and not name.startswith("author_"):
        return slugify(name, 80)
    return slugify(f"author_{author.get('slug', 'unknown')}")


class HKEJScraper(BaseScraper):
    code = "hkej"
    name = "Hong Kong Economic Journal"

    def __init__(self) -> None:
        super().__init__()
        s = settings()
        # Browser automation is heavier than httpx; use a slower per-host floor.
        interval = max(s.hkej_rate_limit_sec, s.scrape_rate_limit_sec)
        self.limiter = HostRateLimiter(interval, jitter=1.5)
        self._keep_browser_open = False
        self.last_stats: dict = {}
        self._daemon_mode = False

    async def _page_settle(self) -> None:
        await asyncio.sleep(_PAGE_SETTLE_SEC)

    @staticmethod
    def _is_cloudflare(html: str, title: str = "") -> bool:
        return (
            title == "Just a moment..."
            or "Performing security verification" in html
            or "challenges.cloudflare.com" in html
        )

    async def _page_html_title(self, page) -> tuple[str, str]:
        try:
            html = await page.content()
            title = (await page.title() or "").strip()
            return html, title
        except Exception:
            return "", ""

    async def _wait_cloudflare_clear(
        self, page, site: str, timeout_sec: float = 300.0,
    ) -> bool:
        """Stay on the current page until Cloudflare finishes — do not navigate away."""
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            html, title = await self._page_html_title(page)
            if self._is_cloudflare(html, title):
                self.log.info(
                    "Cloudflare on %s — stay on this page until verification completes",
                    site,
                )
                await asyncio.sleep(3)
                continue
            return True
        self.log.warning("Cloudflare on %s did not clear within %.0fs", site, timeout_sec)
        return False

    async def _author_search_url(self, author_handle: str) -> tuple[str, str] | None:
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        with db_engine().connect() as conn:
            row = conn.execute(
                sql_text(
                    "SELECT c.handle, c.name, c.url FROM channel c "
                    "JOIN source s ON c.source_id=s.id "
                    "WHERE s.code='hkej' AND c.handle=:h"
                ),
                {"h": author_handle},
            ).fetchone()
        if not row:
            return None
        handle, name, url = row
        search_url = url or (
            f"{SEARCH_BASE}/template/fulltextsearch/php/search.php"
            f"?author={handle}"
        )
        return name, search_url

    @staticmethod
    def _normalize_url(href: str) -> str:
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return ARTICLE_BASE + href
        return href

    @asynccontextmanager
    async def _raw_browser_session(self, *, keep_open: bool | None = None):
        """Persistent Camoufox window; optional keep-open until user closes it."""
        from camoufox.async_api import AsyncCamoufox

        keep_open = self._keep_browser_open if keep_open is None else keep_open
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        lock = BROWSER_PROFILE_DIR / "parent.lock"
        if lock.exists() and not self._daemon_mode:
            self.log.error(
                "browser profile locked (%s) — close any HKEJ/Camoufox window, "
                "or run: kb hkej browser start",
                lock,
            )
            raise RuntimeError(
                "HKEJ browser profile is locked. "
                "Use [kb hkej browser start] for a persistent session."
            )
        async with AsyncCamoufox(
            headless=False,
            humanize=True,
            persistent_context=True,
            user_data_dir=str(BROWSER_PROFILE_DIR),
            disable_coop=True,
            i_know_what_im_doing=True,
        ) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                yield page
            finally:
                if keep_open:
                    self.log.info(
                        "scrape finished — browser stays open; close the window when done"
                    )
                    try:
                        while not page.is_closed():
                            await asyncio.sleep(1)
                    except Exception:
                        pass
                try:
                    await context.storage_state(path=str(SESSION_STATE_PATH))
                    await page.context.storage_state(path=str(STATE_PATH))
                except Exception as exc:
                    self.log.debug("save browser state: %s", exc)

    @asynccontextmanager
    async def _browser_session(self):
        """One persistent Camoufox window: login → search → articles (no restart)."""
        async with self._raw_browser_session() as page:
            await self._ensure_logged_in(page)
            yield page

    async def _ensure_logged_in(self, page, wait_sec: float = 900.0) -> bool:
        """Check member session on landing; wait for manual login if needed."""
        try:
            await page.goto(
                f"{BASE}/landing/index",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await self._page_settle()
            await self._wait_cloudflare_clear(page, "www.hkej.com", timeout_sec=180.0)
            if await self._is_logged_in(page):
                self.log.info("HKEJ already logged in (this browser session)")
                return True
        except Exception as exc:
            self.log.debug("session check: %s", exc)

        return await self._wait_for_manual_login(page, wait_sec=wait_sec)

    async def _is_session_warm(self, page) -> bool:
        """True when the open browser tab is past Cloudflare and logged in."""
        try:
            if page.is_closed():
                return False
        except Exception:
            return False
        try:
            await page.goto(
                f"{BASE}/landing/index",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await self._page_settle()
            html, title = await self._page_html_title(page)
            if self._is_cloudflare(html, title):
                return False
            return await self._is_logged_in(page)
        except Exception as exc:
            self.log.debug("session warm check: %s", exc)
            return False

    async def _prepare_session(
        self,
        page,
        author_handle: str,
        login_wait_sec: float,
        *,
        skip_if_warm: bool = True,
    ) -> bool:
        if skip_if_warm and await self._is_session_warm(page):
            self.log.info("reusing warm browser session — skipping Cloudflare/login")
            return True
        if not await self._prime_search_on_page(page, author_handle):
            return False
        return await self._wait_for_manual_login(page, wait_sec=login_wait_sec)

    async def _prime_search_on_page(
        self, page, author_handle: str, *, timeout_sec: float = 300.0,
    ) -> bool:
        """Open author search; wait on Cloudflare until search results load."""
        resolved = await self._author_search_url(author_handle)
        if not resolved:
            self.log.error("author %r not in DB", author_handle)
            return False
        name, search_url = resolved
        self.log.info("step 1: priming search.hkej.com for %s", name)
        await self.limiter.wait(search_url)
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            await self._page_settle()
        except Exception as exc:
            self.log.warning("search prime goto failed: %s", exc)
            return False
        if not await self._wait_cloudflare_clear(page, "search.hkej.com", timeout_sec):
            return False
        ok = await self._wait_search_ready(page, timeout_sec=timeout_sec)
        if ok:
            self.log.info("search primed for %s", name)
        return ok

    async def _wait_for_manual_login(
        self,
        page,
        *,
        forward_url: str | None = None,
        wait_sec: float = 900.0,
    ) -> bool:
        """Open subscribe login; wait on Cloudflare, then poll until member header appears."""
        login_url = self._forward_login_url(forward_url)
        self.log.info(
            "step 2: priming subscribe.hkej.com — complete Cloudflare, then log in manually"
        )
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            await self._page_settle()
        except Exception as exc:
            self.log.error("login page unreachable: %s", exc)
            return False

        if not await self._wait_cloudflare_clear(
            page, "subscribe.hkej.com", timeout_sec=min(wait_sec, 300.0),
        ):
            return False

        if await self._is_logged_in(page):
            self.log.info("already logged in after Cloudflare")
            return True

        await self._wait_login_ready(page, timeout_sec=180.0)
        self.log.info(
            "please log in in the browser (green 登入) — window stays open: %s",
            login_url,
        )

        deadline = asyncio.get_running_loop().time() + wait_sec
        while asyncio.get_running_loop().time() < deadline:
            html, title = await self._page_html_title(page)
            if self._is_cloudflare(html, title):
                self.log.info("waiting for Cloudflare on subscribe.hkej.com …")
                await asyncio.sleep(3)
                continue
            if await self._is_logged_in(page):
                break
            await asyncio.sleep(2)
        else:
            self.log.warning("manual login timed out after %.0fs", wait_sec)
            return False

        verify_url = forward_url or f"{BASE}/landing/index"
        try:
            await page.goto(verify_url, wait_until="domcontentloaded", timeout=60000)
            await self._page_settle()
            await self._wait_cloudflare_clear(page, "www.hkej.com", timeout_sec=120.0)
        except Exception as exc:
            self.log.debug("post-login verify: %s", exc)

        if not await self._is_logged_in(page):
            self.log.warning("login not confirmed on %s", verify_url)
            return False

        await self._save_page_state(page)
        self.log.info("manual login OK — continuing in same browser")
        return True

    @staticmethod
    def _forward_login_url(article_url: str | None = None) -> str:
        """Build subscribe login URL; forward back to article after auth."""
        if article_url:
            p = urlparse(article_url)
            host = p.netloc.replace("www1.", "www.")
            fwd = f"//{host}{p.path}"
            return (
                "https://subscribe.hkej.com/member/login"
                f"?forwardURL={quote(fwd, safe='')}"
            )
        return LOGIN_URL

    @staticmethod
    def _html_is_excerpt(html: str) -> bool:
        return "isFullArticle='n'" in html or "（節錄）" in html

    def _cached_is_full(self, md_path: Path) -> bool:
        """Excerpt-only saves should be re-fetched after a real login."""
        text = md_path.read_text(encoding="utf-8", errors="replace")
        if "external_id:" not in text:
            return False
        parts = text.split("---", 2)
        body = parts[2].strip() if len(parts) >= 3 else text
        lines = [
            ln for ln in body.splitlines()
            if ln.strip() and not ln.startswith("#") and not ln.startswith("*")
        ]
        content = "\n".join(lines).strip()
        if len(content) < 400:
            return False
        if content.endswith("...") or content.endswith("…"):
            return False
        return "（節錄）" not in content

    async def _header_shows_login_link(self, page) -> bool:
        try:
            return await page.evaluate(
                """
                () => Array.from(
                  document.querySelectorAll('.hkej_upper_registration_btn_2014 a')
                ).some(a => (a.textContent || '').trim() === '登入')
                """
            )
        except Exception:
            return False

    async def _is_logged_in(self, page) -> bool:
        """Logged in when header shows 歡迎（我的賬戶｜登出）, not top「登入」."""
        try:
            return await page.evaluate(
                """
                () => {
                  const menu = document.querySelector('#hkej_logon_menu_container_2014')
                    || document.querySelector('.hkej_upper_registration_logon_menu_2014');
                  if (menu) {
                    const t = menu.textContent || '';
                    if (t.includes('歡迎') && t.includes('我的賬戶') && t.includes('登出')) {
                      return true;
                    }
                  }
                  const topLogin = Array.from(
                    document.querySelectorAll('.hkej_upper_registration_btn_2014 a')
                  ).some(a => (a.textContent || '').trim() === '登入');
                  return !topLogin && (document.body.textContent || '').includes('登出');
                }
                """
            )
        except Exception:
            return False

    async def _wait_until_logged_in(self, page, timeout_sec: float = 45.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            if await self._is_logged_in(page):
                return True
            if not await self._header_shows_login_link(page):
                # header changed but wording differs — recheck body
                html = await page.content()
                if "歡迎" in html and "登出" in html and "我的賬戶" in html:
                    return True
            await asyncio.sleep(1.5)
        return False

    async def _wait_login_ready(self, page, timeout_sec: float = 180.0) -> bool:
        """Wait for Cloudflare to clear and the login form to appear."""
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            html, title = await self._page_html_title(page)
            if self._is_cloudflare(html, title):
                self.log.info("waiting for Cloudflare on subscribe.hkej.com …")
                await asyncio.sleep(3)
                continue
            if await self._is_logged_in(page):
                return True
            has_pwd = await page.evaluate(
                """
                () => !!Array.from(document.querySelectorAll("input[type='password']"))
                         .find(i => i.offsetParent !== null)
                """
            )
            if has_pwd:
                return True
            await asyncio.sleep(2)
        return False

    async def _login_subscribe(
        self, page, forward_url: str | None = None, *, force: bool = False,
    ) -> bool:
        """Log in via subscribe.hkej.com; keep the browser open afterward."""
        s = settings()
        if not (s.hkej_user and s.hkej_pass):
            self.log.warning("HKEJ credentials missing; articles may be excerpt-only")
            return False

        login_url = self._forward_login_url(forward_url)

        try:
            check_url = forward_url or f"{SEARCH_BASE}/template/fulltextsearch/php/search.php"
            await page.goto(check_url, wait_until="domcontentloaded", timeout=60000)
            await self._page_settle()
            if await self._is_logged_in(page):
                self.log.info("HKEJ already logged in (profile session)")
                return True
        except Exception as exc:
            self.log.debug("login pre-check: %s", exc)

        self.log.info("logging in at %s", login_url)
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            await self._page_settle()
        except Exception as exc:
            self.log.warning("login page unreachable: %s", exc)
            return False

        if not await self._wait_login_ready(page, timeout_sec=120.0):
            self.log.warning(
                "login form did not appear — complete Cloudflare manually "
                "(kb hkej prime-login)"
            )
            return False

        ok = await self._fill_login_form(page)
        if not ok:
            self.log.warning("HKEJ login unsuccessful — check credentials in .env")
            return False

        if forward_url:
            try:
                await page.goto(forward_url, wait_until="domcontentloaded", timeout=60000)
                await self._page_settle()
            except Exception as exc:
                self.log.debug("post-login article redirect: %s", exc)
        else:
            try:
                await page.goto(
                    f"{SEARCH_BASE}/template/fulltextsearch/php/search.php",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await self._wait_search_ready(page, timeout_sec=30.0)
            except Exception as exc:
                self.log.debug("post-login search check: %s", exc)

        if not await self._is_logged_in(page):
            self.log.warning(
                "login submitted but header still shows 登入 — "
                "expected 歡迎（我的賬戶｜登出）"
            )
            return False

        self.log.info("HKEJ login OK — header shows 歡迎（我的賬戶｜登出）")
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        await page.context.storage_state(path=str(STATE_PATH))
        await self._save_page_state(page)
        return True

    async def _click_form_login_button(self, page) -> bool:
        """Click the green「登入」submit in the login form (not the top nav link)."""
        try:
            clicked = await page.evaluate(
                """
                () => {
                  const pwd = document.querySelector("input[type='password']");
                  if (!pwd) return false;
                  const form = pwd.closest('form') || document.body;
                  const inHeader = (el) => !!el.closest(
                    '.hkej_upper_registration_btn_2014, .hkej_funcBar_login_2019'
                  );
                  const candidates = Array.from(form.querySelectorAll(
                    "input[type='submit'], button[type='submit'], button, input[type='button']"
                  ));
                  const score = (el) => {
                    const text = (el.innerText || el.value || '').trim();
                    const bg = getComputedStyle(el).backgroundColor || '';
                    let s = 0;
                    if (text === '登入') s += 10;
                    if (bg.includes('0, 128') || bg.includes('34, 139') || bg.includes('0, 153')) s += 5;
                    if ((el.className || '').toLowerCase().includes('green')) s += 3;
                    if (el.type === 'submit') s += 2;
                    return s;
                  };
                  let best = null, bestScore = 0;
                  for (const el of candidates) {
                    if (!el.offsetParent || inHeader(el)) continue;
                    const sc = score(el);
                    if (sc > bestScore) { best = el; bestScore = sc; }
                  }
                  if (best && bestScore > 0) { best.click(); return true; }
                  const sub = form.querySelector("input[type='submit'], button[type='submit']");
                  if (sub && !inHeader(sub)) { sub.click(); return true; }
                  return false;
                }
                """
            )
            return bool(clicked)
        except Exception as exc:
            self.log.debug("form login click: %s", exc)
            return False

    async def _fill_login_form(self, page) -> bool:
        s = settings()
        try:
            form_handle = await page.evaluate_handle(
                """
                () => {
                  const pwd = Array.from(document.querySelectorAll("input[type='password']"))
                                   .find(i => i.offsetParent !== null);
                  if (!pwd) return null;
                  const form = pwd.closest('form') || document.body;
                  const user = form.querySelector(
                    "input[type='email'],input[type='text'],input:not([type])"
                  );
                  return { userName: user ? (user.name || user.id || '') : '',
                           userType: user ? user.type || 'text' : '',
                           pwdName: pwd.name || pwd.id || '' };
                }
                """
            )
            info = await form_handle.json_value()
        except Exception:
            info = None
        if not info:
            self.log.info("no password field on login page")
            return False
        try:
            u_sel = (f"input[name='{info['userName']}']" if info.get("userName") else
                     "input[type='email'], input[type='text']")
            p_sel = (f"input[name='{info['pwdName']}']" if info.get("pwdName") else
                     "input[type='password']")
            await page.fill(u_sel, s.hkej_user, timeout=8000)
            await page.fill(p_sel, s.hkej_pass, timeout=8000)
        except Exception as exc:
            self.log.info("login fill failed: %s", exc)
            return False

        await self._page_settle()
        if not await self._click_form_login_button(page):
            self.log.warning("green form 登入 button not found")
            return False

        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await self._page_settle()
        return await self._wait_until_logged_in(page, timeout_sec=45.0)

    async def _save_page_state(self, page) -> None:
        try:
            await page.context.storage_state(path=str(SESSION_STATE_PATH))
        except Exception as exc:
            self.log.debug("save page state: %s", exc)

    @staticmethod
    def _search_page_url(base_url: str, page: int) -> str:
        if page <= 1:
            return base_url
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}page={page}"

    @staticmethod
    def _article_id_from_href(href: str) -> str | None:
        m = (
            re.search(r"/article/(\d+)", href)
            or re.search(r"/article/id/(\d+)", href)
            or re.search(r"[?&]id=(\d+)", href)
        )
        return m.group(1) if m else None

    async def _wait_search_ready(self, page, timeout_sec: float = 300.0) -> bool:
        """Wait for Cloudflare challenge on search.hkej.com to clear."""
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            html, title = await self._page_html_title(page)
            if not html:
                await asyncio.sleep(2)
                continue
            if self._is_cloudflare(html, title):
                self.log.info(
                    "waiting for Cloudflare on search.hkej.com — stay on this page"
                )
                await asyncio.sleep(3)
                continue
            if "General.Init" in html or (
                "span.timeStamp" in html and "/article/" in html
            ):
                await self._save_page_state(page)
                return True
            if "/article/" in html and "challenges.cloudflare.com" not in html:
                await self._save_page_state(page)
                return True
            await asyncio.sleep(2)
        return False

    @staticmethod
    def _parse_search_meta(soup) -> tuple[int | None, int]:
        """Read「共 N 個結果」and last pagination page from search HTML."""
        text = soup.get_text(" ", strip=True)
        total: int | None = None
        m = re.search(r"共\s*(\d+)\s*個結果", text)
        if m:
            total = int(m.group(1))
        max_page = 1
        for a in soup.select('a[href*="page="]'):
            hm = re.search(r"[?&]page=(\d+)", a.get("href", ""))
            if hm:
                max_page = max(max_page, int(hm.group(1)))
        return total, max_page

    def _count_author_articles(self, author_handle: str) -> int:
        author_dir = DATA_DIR / "hkej" / slugify(author_handle, 80)
        if not author_dir.exists():
            return 0
        # New flat layout: data/hkej/<author>/<year>/<date>-<title>.md
        return sum(1 for _ in author_dir.glob("*/*.md"))

    def _ensure_catalog_schema(self) -> None:
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        statements = [
            """
            CREATE TABLE IF NOT EXISTS hkej_author_state (
                channel_id          INT PRIMARY KEY REFERENCES channel(id) ON DELETE CASCADE,
                search_total        INT,
                max_page            INT,
                catalog_count       INT NOT NULL DEFAULT 0,
                last_seen_at        TIMESTAMPTZ,
                last_full_crawl_at  TIMESTAMPTZ,
                metadata            JSONB DEFAULT '{}'::jsonb
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS hkej_crawl_run (
                id                  BIGSERIAL PRIMARY KEY,
                channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
                started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at         TIMESTAMPTZ,
                status              TEXT NOT NULL DEFAULT 'running',
                search_total        INT,
                max_page            INT,
                pages_crawled       INT NOT NULL DEFAULT 0,
                pages_reused        INT NOT NULL DEFAULT 0,
                metadata            JSONB DEFAULT '{}'::jsonb
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS hkej_crawl_page (
                id                  BIGSERIAL PRIMARY KEY,
                run_id              BIGINT NOT NULL REFERENCES hkej_crawl_run(id) ON DELETE CASCADE,
                channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
                page_num            INT NOT NULL,
                search_total        INT,
                max_page            INT,
                url                 TEXT NOT NULL,
                crawled_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                article_count       INT NOT NULL DEFAULT 0,
                article_ids         JSONB NOT NULL DEFAULT '[]'::jsonb,
                page_fingerprint    TEXT NOT NULL,
                UNIQUE (run_id, page_num)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS hkej_article_catalog (
                id                  BIGSERIAL PRIMARY KEY,
                channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
                external_id         TEXT NOT NULL,
                published_at        TIMESTAMPTZ,
                title               TEXT NOT NULL,
                url                 TEXT NOT NULL,
                first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                first_seen_run_id   BIGINT REFERENCES hkej_crawl_run(id) ON DELETE SET NULL,
                last_seen_run_id    BIGINT REFERENCES hkej_crawl_run(id) ON DELETE SET NULL,
                last_seen_page      INT,
                downloaded          BOOLEAN NOT NULL DEFAULT false,
                downloaded_at       TIMESTAMPTZ,
                md_path             TEXT,
                raw_path            TEXT,
                metadata            JSONB DEFAULT '{}'::jsonb,
                UNIQUE (channel_id, external_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS hkej_crawl_run_channel_idx ON hkej_crawl_run(channel_id, started_at DESC)",
            """
            CREATE INDEX IF NOT EXISTS hkej_crawl_page_resume_idx
                ON hkej_crawl_page(channel_id, search_total, max_page, page_num)
            """,
            """
            CREATE INDEX IF NOT EXISTS hkej_article_catalog_channel_idx
                ON hkej_article_catalog(channel_id, published_at DESC, id DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS hkej_article_catalog_downloaded_idx
                ON hkej_article_catalog(channel_id, downloaded)
            """,
        ]
        with db_engine().begin() as conn:
            for stmt in statements:
                conn.execute(sql_text(stmt))

    @staticmethod
    def _page_fingerprint(article_ids: list[str]) -> str:
        return hashlib.sha256("\n".join(article_ids).encode("utf-8")).hexdigest()

    def _start_crawl_run(
        self, author: dict, search_total: int | None, max_page: int,
    ) -> int | None:
        channel_id = author.get("channel_id")
        if not channel_id:
            return None
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        with db_engine().begin() as conn:
            return conn.execute(
                sql_text(
                    "INSERT INTO hkej_crawl_run(channel_id, search_total, max_page) "
                    "VALUES (:ch, :total, :max_page) RETURNING id"
                ),
                {"ch": channel_id, "total": search_total, "max_page": max_page},
            ).scalar_one()

    def _compatible_resume_pages(
        self,
        author: dict,
        search_total: int | None,
        max_page: int,
        page1_fingerprint: str,
    ) -> dict[int, int]:
        channel_id = author.get("channel_id")
        if not channel_id or search_total is None:
            return {}
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        with db_engine().connect() as conn:
            rows = conn.execute(
                sql_text(
                    "WITH compatible_runs AS ("
                    "  SELECT run_id FROM hkej_crawl_page "
                    "  WHERE channel_id=:ch AND page_num=1 "
                    "    AND search_total=:total AND max_page=:max_page "
                    "    AND page_fingerprint=:fp"
                    ") "
                    "SELECT DISTINCT ON (p.page_num) p.page_num, p.run_id "
                    "FROM hkej_crawl_page p "
                    "JOIN compatible_runs r ON r.run_id=p.run_id "
                    "JOIN hkej_crawl_run cr ON cr.id=p.run_id "
                    "WHERE p.channel_id=:ch AND p.search_total=:total "
                    "  AND p.max_page=:max_page "
                    "ORDER BY p.page_num, cr.started_at DESC"
                ),
                {
                    "ch": channel_id,
                    "total": search_total,
                    "max_page": max_page,
                    "fp": page1_fingerprint,
                },
            ).fetchall()
        return {int(page_num): int(run_id) for page_num, run_id in rows}

    def _catalog_descriptors_for_page(
        self, author: dict, run_id: int, page_num: int,
    ) -> list[dict]:
        channel_id = author.get("channel_id")
        if not channel_id:
            return []
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        with db_engine().connect() as conn:
            rows = conn.execute(
                sql_text(
                    "SELECT c.external_id, c.url, c.title, c.published_at, c.metadata "
                    "FROM hkej_crawl_page p "
                    "CROSS JOIN LATERAL jsonb_array_elements_text(p.article_ids) "
                    "  WITH ORDINALITY AS a(external_id, ord) "
                    "JOIN hkej_article_catalog c ON c.channel_id=p.channel_id "
                    "  AND c.external_id=a.external_id "
                    "WHERE p.run_id=:run AND p.channel_id=:ch AND p.page_num=:page "
                    "ORDER BY a.ord"
                ),
                {"run": run_id, "ch": channel_id, "page": page_num},
            ).fetchall()
        out: list[dict] = []
        for ext_id, url, title, published_at, metadata in rows:
            meta = dict(metadata or {})
            out.append({
                "external_id": ext_id,
                "url": url,
                "title": title,
                "author": author,
                "published_at": published_at,
                "search_recap": meta.get("search_recap", ""),
            })
        return out

    def _record_catalog_page(
        self,
        author: dict,
        run_id: int | None,
        page_num: int,
        url: str,
        search_total: int | None,
        max_page: int,
        articles: list[dict],
    ) -> str:
        article_ids = [str(a["external_id"]) for a in articles]
        fingerprint = self._page_fingerprint(article_ids)
        channel_id = author.get("channel_id")
        if not channel_id or run_id is None:
            return fingerprint
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        with db_engine().begin() as conn:
            conn.execute(
                sql_text(
                    "INSERT INTO hkej_crawl_page(run_id, channel_id, page_num, "
                    "search_total, max_page, url, article_count, article_ids, "
                    "page_fingerprint) "
                    "VALUES (:run, :ch, :page, :total, :max_page, :url, :count, "
                    "CAST(:ids AS jsonb), :fp) "
                    "ON CONFLICT (run_id, page_num) DO UPDATE SET "
                    "  search_total=EXCLUDED.search_total, max_page=EXCLUDED.max_page, "
                    "  url=EXCLUDED.url, crawled_at=now(), "
                    "  article_count=EXCLUDED.article_count, "
                    "  article_ids=EXCLUDED.article_ids, "
                    "  page_fingerprint=EXCLUDED.page_fingerprint"
                ),
                {
                    "run": run_id,
                    "ch": channel_id,
                    "page": page_num,
                    "total": search_total,
                    "max_page": max_page,
                    "url": url,
                    "count": len(articles),
                    "ids": json.dumps(article_ids, ensure_ascii=False),
                    "fp": fingerprint,
                },
            )
            for article in articles:
                meta = {"search_recap": article.get("search_recap", "")}
                conn.execute(
                    sql_text(
                        "INSERT INTO hkej_article_catalog(channel_id, external_id, "
                        "published_at, title, url, first_seen_run_id, last_seen_run_id, "
                        "last_seen_page, metadata) "
                        "VALUES (:ch, :eid, :published_at, :title, :url, :run, :run, "
                        ":page, CAST(:meta AS jsonb)) "
                        "ON CONFLICT (channel_id, external_id) DO UPDATE SET "
                        "  published_at=COALESCE(EXCLUDED.published_at, hkej_article_catalog.published_at), "
                        "  title=EXCLUDED.title, url=EXCLUDED.url, "
                        "  last_seen_at=now(), last_seen_run_id=EXCLUDED.last_seen_run_id, "
                        "  last_seen_page=EXCLUDED.last_seen_page, "
                        "  metadata=hkej_article_catalog.metadata || EXCLUDED.metadata"
                    ),
                    {
                        "ch": channel_id,
                        "eid": str(article["external_id"]),
                        "published_at": article.get("published_at"),
                        "title": article.get("title") or f"hkej_{article['external_id']}",
                        "url": article["url"],
                        "run": run_id,
                        "page": page_num,
                        "meta": json.dumps(meta, ensure_ascii=False, default=str),
                    },
                )
        return fingerprint

    def _finish_crawl_run(
        self,
        author: dict,
        run_id: int | None,
        stats: dict,
        *,
        complete: bool,
    ) -> None:
        channel_id = author.get("channel_id")
        if not channel_id:
            return
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        with db_engine().begin() as conn:
            if run_id is not None:
                conn.execute(
                    sql_text(
                        "UPDATE hkej_crawl_run SET finished_at=now(), "
                        "status=:status, pages_crawled=:crawled, pages_reused=:reused, "
                        "search_total=:total, max_page=:max_page "
                        "WHERE id=:run"
                    ),
                    {
                        "run": run_id,
                        "status": "finished" if complete else "partial",
                        "crawled": stats.get("pages_crawled", 0),
                        "reused": stats.get("pages_reused", 0),
                        "total": stats.get("search_total"),
                        "max_page": stats.get("max_page", 1),
                    },
                )
            catalog_count = conn.execute(
                sql_text(
                    "SELECT COUNT(*) FROM hkej_article_catalog WHERE channel_id=:ch"
                ),
                {"ch": channel_id},
            ).scalar_one()
            conn.execute(
                sql_text(
                    "INSERT INTO hkej_author_state(channel_id, search_total, max_page, "
                    "catalog_count, last_seen_at, last_full_crawl_at) "
                    "VALUES (:ch, :total, :max_page, :count, now(), "
                    "CASE WHEN :complete THEN now() ELSE NULL END) "
                    "ON CONFLICT (channel_id) DO UPDATE SET "
                    "  search_total=EXCLUDED.search_total, max_page=EXCLUDED.max_page, "
                    "  catalog_count=EXCLUDED.catalog_count, last_seen_at=now(), "
                    "  last_full_crawl_at=CASE WHEN :complete THEN now() "
                    "    ELSE hkej_author_state.last_full_crawl_at END"
                ),
                {
                    "ch": channel_id,
                    "total": stats.get("search_total"),
                    "max_page": stats.get("max_page", 1),
                    "count": catalog_count,
                    "complete": complete,
                },
            )

    def _cached_full_md_path(self, d: dict) -> Path | None:
        ext_id = str(d.get("external_id", ""))
        author = d.get("author") or {}
        author_dir = DATA_DIR / "hkej" / _author_dir_slug(author)
        if not author_dir.exists():
            return None
        for md_path in author_dir.glob("*/*.md"):
            text = md_path.read_text(encoding="utf-8", errors="replace")
            if f"external_id: '{ext_id}'" not in text and f'external_id: "{ext_id}"' not in text:
                continue
            if not self._cached_is_full(md_path):
                return None
            raw = (DATA_DIR / "raw" / "hkej"
                   / md_path.relative_to(DATA_DIR / "hkej").with_suffix(".html"))
            if raw.exists() and self._html_is_excerpt(
                raw.read_text(encoding="utf-8", errors="replace")
            ):
                return None
            return md_path
        return None

    def _cached_full_md_paths(self, author: dict) -> dict[str, Path]:
        author_dir = DATA_DIR / "hkej" / _author_dir_slug(author)
        if not author_dir.exists():
            return {}
        out: dict[str, Path] = {}
        for md_path in author_dir.glob("*/*.md"):
            text = md_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"external_id:\s*['\"]?([^'\"\n]+)['\"]?", text)
            if not m:
                continue
            if not self._cached_is_full(md_path):
                continue
            raw = (DATA_DIR / "raw" / "hkej"
                   / md_path.relative_to(DATA_DIR / "hkej").with_suffix(".html"))
            if raw.exists() and self._html_is_excerpt(
                raw.read_text(encoding="utf-8", errors="replace")
            ):
                continue
            out[m.group(1).strip()] = md_path
        return out

    def _mark_catalog_downloaded(self, d: dict, md_path: Path | None) -> None:
        author = d.get("author") or {}
        channel_id = author.get("channel_id")
        if not channel_id or md_path is None:
            return
        raw_path = (DATA_DIR / "raw" / "hkej"
                    / md_path.relative_to(DATA_DIR / "hkej").with_suffix(".html"))
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        with db_engine().begin() as conn:
            conn.execute(
                sql_text(
                    "UPDATE hkej_article_catalog SET downloaded=true, "
                    "downloaded_at=now(), md_path=:md, raw_path=:raw "
                    "WHERE channel_id=:ch AND external_id=:eid"
                ),
                {
                    "ch": channel_id,
                    "eid": str(d.get("external_id", "")),
                    "md": str(md_path),
                    "raw": str(raw_path) if raw_path.exists() else None,
                },
            )

    # ------------------------------------------------------------------
    async def _list_articles_from_search(
        self, page, author: dict, limit: int,
    ) -> tuple[list[dict], dict]:
        """Discover articles via search.hkej.com ?author= endpoint only."""
        self._ensure_catalog_schema()
        urls: list[dict] = []
        seen_ids: set[str] = set()
        stats: dict = {
            "search_total": None,
            "max_page": 1,
            "pages_crawled": 0,
            "pages_reused": 0,
            "discovered": 0,
        }
        base_url = author["url"]
        if not base_url.startswith(SEARCH_BASE):
            base_url = (
                f"{SEARCH_BASE}/template/fulltextsearch/php/search.php"
                f"?author={author.get('slug') or author.get('name', '')}"
            )
        max_page = 1
        run_id = self._start_crawl_run(author, None, max_page)
        reusable_pages: dict[int, int] = {}

        def add_article(article: dict) -> bool:
            ext_id = str(article.get("external_id") or "")
            if not ext_id or ext_id in seen_ids:
                return False
            seen_ids.add(ext_id)
            urls.append(article)
            return True

        def parse_articles(soup) -> list[dict]:
            articles: list[dict] = []
            page_seen: set[str] = set()
            headline_links = [
                h3.find("a", href=True)
                for h3 in soup.select("h3")
                if h3.find("a", href=True)
            ]
            fallback_links = soup.select(
                "a[href*='/dailynews/'][href*='/article/'], "
                "a[href*='article?id='], a[href*='/wm/article/id/']"
            )
            for a in headline_links + fallback_links:
                if a is None:
                    continue
                href = a.get("href", "")
                if "/article/" not in href and "article?id=" not in href:
                    continue
                ext_id = self._article_id_from_href(href)
                if not ext_id or ext_id in page_seen:
                    continue
                title = (a.get("title") or a.get_text(strip=True) or "").strip()
                if title in ("全文", "") or len(title) < 2:
                    continue
                page_seen.add(ext_id)
                full = self._normalize_url(href.split("#")[0])
                published_at = None
                info = a.find_parent("h3")
                if info is not None:
                    info = info.find_next_sibling("p", class_="info")
                if info:
                    ts = info.select_one("span.timeStamp")
                    if ts:
                        m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", ts.get_text(strip=True))
                        if m:
                            try:
                                published_at = datetime(int(m[1]), int(m[2]), int(m[3]))
                            except ValueError:
                                pass
                articles.append({
                    "external_id": ext_id,
                    "url": full,
                    "title": title,
                    "author": author,
                    "published_at": published_at,
                    "search_recap": self._search_recap_for_link(a, soup),
                })
            return articles

        complete = False
        pg = 1
        while True:
            if pg > max_page:
                complete = True
                break
            url = self._search_page_url(base_url, pg)

            if pg > 1 and pg in reusable_pages:
                fresh = 0
                for article in self._catalog_descriptors_for_page(
                    author, reusable_pages[pg], pg,
                ):
                    if add_article(article):
                        fresh += 1
                        if len(urls) >= limit:
                            stats["discovered"] = len(urls)
                            self._finish_crawl_run(author, run_id, stats, complete=False)
                            return urls, stats
                stats["pages_reused"] += 1
                self.log.info(
                    "search page %d/%d: reused catalog (%d discovered so far)",
                    pg, max_page, len(urls),
                )
                await asyncio.sleep(_BETWEEN_PAGES_SEC)
                pg += 1
                continue

            await self.limiter.wait(url)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                if not await self._wait_search_ready(page):
                    self.log.warning(
                        "search page %d for %s: Cloudflare challenge did not clear "
                        "(complete verification once; profile is saved at %s)",
                        pg, author.get("name"), BROWSER_PROFILE_DIR,
                    )
                    break
                await self._page_settle()
                await self._save_page_state(page)
            except Exception as exc:
                self.log.warning("search page %d failed for %s: %s", pg, author.get("name"), exc)
                break
            html = await page.content()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            page_articles = parse_articles(soup)
            if pg == 1:
                stats["search_total"], max_page = self._parse_search_meta(soup)
                stats["max_page"] = max_page
                first_fingerprint = self._record_catalog_page(
                    author, run_id, pg, url, stats["search_total"], max_page,
                    page_articles,
                )
                reusable_pages = self._compatible_resume_pages(
                    author, stats["search_total"], max_page, first_fingerprint,
                )
                reusable_pages.pop(1, None)
                self.log.info(
                    "search lists %s results across %d page(s) for %s",
                    stats["search_total"] if stats["search_total"] is not None else "?",
                    max_page,
                    author.get("name"),
                )
            else:
                self._record_catalog_page(
                    author, run_id, pg, url, stats["search_total"], max_page,
                    page_articles,
                )
            fresh = 0
            for article in page_articles:
                if add_article(article):
                    fresh += 1
                if len(urls) >= limit:
                    stats["pages_crawled"] += 1
                    stats["discovered"] = len(urls)
                    self._finish_crawl_run(author, run_id, stats, complete=False)
                    return urls, stats
            stats["pages_crawled"] += 1
            self.log.info(
                "search page %d/%d: +%d new (%d discovered so far)",
                pg, max_page, fresh, len(urls),
            )
            if fresh == 0 and pg >= max_page:
                complete = True
                break
            if fresh == 0:
                self.log.warning(
                    "search page %d/%d returned no new links", pg, max_page,
                )
            await asyncio.sleep(_BETWEEN_PAGES_SEC)
            pg += 1
        stats["discovered"] = len(urls)
        self._finish_crawl_run(author, run_id, stats, complete=complete)
        listed = stats["search_total"]
        if listed is not None and len(urls) < listed:
            self.log.warning(
                "discovered %d URLs but search lists %d — pagination may be incomplete",
                len(urls), listed,
            )
        elif listed is not None:
            self.log.info(
                "search discovery for %s: %d/%d articles",
                author.get("name"), len(urls), listed,
            )
        else:
            self.log.info(
                "search discovery for %s: %d articles", author.get("name"), len(urls),
            )
        return urls, stats

    async def _list_articles(self, page, author: dict, limit: int) -> list[dict]:
        urls: list[dict] = []
        seen_ids: set[str] = set()
        for i in range(1, 50):
            url = f"{author['url']}?page={i}" if i > 1 else author["url"]
            await self.limiter.wait(url)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self._page_settle()
            except Exception as exc:
                self.log.warning("author %s page %d goto failed: %s", author.get('slug'), i, exc)
                break
            html = await page.content()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            anchors = soup.select("a[href*='/wm/article/id/']")
            fresh = 0
            for a in anchors:
                href = a.get("href", "")
                title = a.get_text(strip=True)
                m = re.search(r"/wm/article/id/(\d+)", href)
                if not m:
                    continue
                ext_id = m.group(1)
                if ext_id in seen_ids:
                    continue
                seen_ids.add(ext_id)
                full = self._normalize_url(href.split("?")[0].split("#")[0])
                if not title or len(title) < 2:
                    title = f"hkej_{ext_id}"
                urls.append({
                    "external_id": ext_id,
                    "url": full,
                    "title": title,
                    "author": author,
                })
                fresh += 1
                if len(urls) >= limit:
                    return urls
            if fresh == 0:
                break
            await asyncio.sleep(_BETWEEN_PAGES_SEC)
        return urls

    async def _discover_with_page(
        self, page, limit: int | None, author_handle: str | None = None,
    ) -> AsyncIterator[dict]:
        from ..db import engine as db_engine
        from sqlalchemy import text as sql_text

        sql = (
            "SELECT c.id, c.handle, c.name, c.url, c.metadata FROM channel c "
            "JOIN source s ON c.source_id=s.id WHERE s.code='hkej'"
        )
        params: dict = {}
        if author_handle:
            sql += " AND c.handle=:h"
            params["h"] = author_handle
        sql += " ORDER BY c.name"

        try:
            with db_engine().connect() as conn:
                rows = conn.execute(sql_text(sql), params).fetchall()
        except Exception as exc:
            self.log.error("Cannot load HKEJ channels from DB: %s", exc)
            return

        if not rows:
            self.log.warning(
                "No HKEJ authors in DB. Add with: kb hkej add-author <name_or_id>"
            )
            return

        authors = []
        for channel_id, handle, name, url, metadata in rows:
            meta = dict(metadata or {})
            authors.append({
                "channel_id": channel_id,
                "slug": handle,
                "name": name,
                "url": url or f"{BASE}/wm/authordetail/id/{handle}",
                "metadata": meta,
            })

        per_author = 10 if limit and limit < 200 else (10 ** 6)
        yielded = 0
        for author in authors:
            disc_url = author["url"]
            await self.limiter.wait(disc_url)
            if "search.hkej.com" in disc_url:
                arts, disc_stats = await self._list_articles_from_search(
                    page, author, per_author,
                )
                self.last_stats.update(disc_stats)
            else:
                arts = await self._list_articles(page, author, per_author)
                self.last_stats["discovered"] = len(arts)
            for a in arts:
                yield a
                yielded += 1
                if limit and yielded >= limit:
                    return
            await asyncio.sleep(_BETWEEN_AUTHORS_SEC)

    async def discover(
        self,
        limit: int | None = None,
        page=None,
        author_handle: str | None = None,
    ) -> AsyncIterator[dict]:
        if page is not None:
            async for item in self._discover_with_page(page, limit, author_handle):
                yield item
            return

        async with self._browser_session() as page:
            async for item in self._discover_with_page(page, limit, author_handle):
                yield item

    async def prime_login_session(self, wait_sec: float = 600.0) -> bool:
        """Open subscribe login; wait on Cloudflare, then wait for manual sign-in."""
        async with self._raw_browser_session() as page:
            return await self._wait_for_manual_login(page, wait_sec=wait_sec)

    async def prime_search_session(self, author_handle: str = "李聲揚") -> bool:
        """Open search.hkej.com once; wait on Cloudflare until results load."""
        async with self._raw_browser_session() as page:
            return await self._prime_search_on_page(page, author_handle)

    async def prime_session(
        self,
        author_handle: str = "李聲揚",
        *,
        login_wait_sec: float = 600.0,
        search_timeout_sec: float = 300.0,
    ) -> bool:
        """Prime search + login in one browser — Cloudflare first, then manual login."""
        async with self._raw_browser_session() as page:
            if not await self._prime_search_on_page(
                page, author_handle, timeout_sec=search_timeout_sec,
            ):
                return False
            return await self._wait_for_manual_login(page, wait_sec=login_wait_sec)

    def already_scraped(self, d: dict) -> bool:
        ext_id = str(d.get("external_id", ""))
        author = d.get("author") or {}
        author_dir = DATA_DIR / "hkej" / _author_dir_slug(author)
        if not author_dir.exists():
            return False
        # New flat layout: data/hkej/<author>/<year>/<date>-<title>.md
        for md_path in author_dir.glob("*/*.md"):
            text = md_path.read_text(encoding="utf-8", errors="replace")
            if f"external_id: '{ext_id}'" not in text and f'external_id: "{ext_id}"' not in text:
                continue
            if not self._cached_is_full(md_path):
                return False
            # Raw HTML lives under data/raw/hkej/<author>/<year>/<date>-<title>.html
            raw = (DATA_DIR / "raw" / "hkej"
                   / md_path.relative_to(DATA_DIR / "hkej").with_suffix(".html"))
            if raw.exists() and self._html_is_excerpt(
                raw.read_text(encoding="utf-8", errors="replace")
            ):
                return False
            return True
        return False

    async def _load_article_html(self, page, url: str) -> str:
        await self.limiter.wait(url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await self._page_settle()
        return await page.content()

    async def run_on_page(
        self,
        page,
        limit: int | None = None,
        author_handle: str | None = None,
        *,
        login_wait_sec: float = 900.0,
        skip_prime_if_warm: bool = True,
    ) -> tuple[list[Path], dict]:
        """Scrape using an already-open browser tab (daemon or shared session)."""
        handle = author_handle or "李聲揚"
        out: list[Path] = []
        stats: dict = {
            "search_total": None,
            "max_page": None,
            "pages_crawled": 0,
            "pages_reused": 0,
            "discovered": 0,
            "skipped": 0,
            "fetched": 0,
            "failed": 0,
            "on_disk_before": self._count_author_articles(handle),
            "on_disk_after": 0,
        }
        if not await self._prepare_session(
            page, handle, login_wait_sec, skip_if_warm=skip_prime_if_warm,
        ):
            self.log.error("session not ready — scrape aborted")
            stats["aborted"] = True
            self.last_stats = stats
            return out, stats

        self.log.info("step 3: discovering articles via search.hkej.com")
        to_fetch: list[dict] = []
        async for d in self._discover_with_page(page, limit, author_handle):
            to_fetch.append(d)
        stats["discovered"] = len(to_fetch)
        for key in ("search_total", "max_page", "pages_crawled", "pages_reused"):
            if self.last_stats.get(key) is not None:
                stats[key] = self.last_stats[key]

        cached_paths: dict[str, Path] = {}
        for d in to_fetch:
            author = d.get("author") or {}
            if author:
                cached_paths = self._cached_full_md_paths(author)
                break

        for d in to_fetch:
            cached_path = cached_paths.get(str(d.get("external_id", "")))
            if cached_path is not None:
                self._mark_catalog_downloaded(d, cached_path)
                stats["skipped"] += 1
                self.log.info(
                    "skip (cached) %s",
                    d.get("url") or d.get("external_id"),
                )
                continue
            self.log.info(
                "step 4: fetching %s [%d new, %d/%s total]",
                d.get("title") or d.get("url"),
                stats["fetched"] + 1,
                stats["on_disk_before"] + stats["fetched"] + 1,
                stats["search_total"] or stats["discovered"],
            )
            try:
                item = await self._fetch_with_page(page, d)
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                self.log.exception("fetch failed: %s :: %s", d, exc)
                continue
            if item is None:
                stats["failed"] += 1
                continue
            p = self.write_md(item)
            self._mark_catalog_downloaded(d, p)
            out.append(p)
            stats["fetched"] += 1
            if limit and len(out) >= limit:
                break

        stats["on_disk_after"] = self._count_author_articles(handle)
        missing = None
        if stats["search_total"]:
            missing = stats["search_total"] - stats["on_disk_after"]
        self.log.info(
            "scrape summary for %s: search=%s discovered=%d skipped=%d "
            "fetched=%d failed=%d on_disk=%d→%d%s",
            handle,
            stats["search_total"] or "?",
            stats["discovered"],
            stats["skipped"],
            stats["fetched"],
            stats["failed"],
            stats["on_disk_before"],
            stats["on_disk_after"],
            f" missing≈{missing}" if missing and missing > 0 else "",
        )
        self.last_stats = stats
        try:
            await self._save_page_state(page)
        except Exception:
            pass
        return out, stats

    async def run(
        self,
        limit: int | None = None,
        author_handle: str | None = None,
        *,
        keep_browser_open: bool = False,
        login_wait_sec: float = 900.0,
        use_daemon: bool = True,
    ) -> list[Path]:
        """Prime → login → search discover → fetch. Uses daemon when available."""
        if use_daemon:
            from .hkej_daemon import daemon_scrape_author, is_daemon_alive

            if is_daemon_alive():
                handle = author_handle or "李聲揚"
                self.log.info("using persistent browser daemon")
                resp = await daemon_scrape_author(
                    handle,
                    limit=limit,
                    login_wait_sec=login_wait_sec,
                )
                if resp and resp.get("ok"):
                    self.last_stats = resp.get("stats") or {}
                    return [Path(p) for p in resp.get("paths", [])]
                err = (resp or {}).get("error", "daemon scrape failed")
                raise RuntimeError(f"HKEJ daemon scrape failed: {err}")
            raise RuntimeError(
                "HKEJ browser daemon is not running. "
                "Run: kb hkej browser start"
            )

        self._keep_browser_open = keep_browser_open
        handle = author_handle or "李聲揚"
        async with self._raw_browser_session(keep_open=keep_browser_open) as page:
            out, _stats = await self.run_on_page(
                page,
                limit=limit,
                author_handle=author_handle,
                login_wait_sec=login_wait_sec,
                skip_prime_if_warm=False,
            )
        return out

    async def fetch(self, d: dict) -> ScrapedItem | None:
        async with self._browser_session() as page:
            return await self._fetch_with_page(page, d)

    async def _fetch_with_page(self, page, d: dict) -> ScrapedItem | None:
        url = d["url"]
        html = await self._load_article_html(page, url)

        if self._html_is_excerpt(html) or await self._header_shows_login_link(page):
            if not await self._is_logged_in(page):
                self.log.info(
                    "article %s needs login — please sign in (same browser)",
                    d["external_id"],
                )
                if not await self._wait_for_manual_login(page, forward_url=url):
                    self.log.warning(
                        "cannot fetch full article %s: login failed", d["external_id"],
                    )
                    return None
            html = await self._load_article_html(page, url)

        if self._html_is_excerpt(html):
            self.log.warning(
                "article %s still 節錄 after login — subscriber access required?",
                d["external_id"],
            )
            return None

        await self._save_page_state(page)

        from bs4 import BeautifulSoup, NavigableString
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.find("h1") or soup.find("h2")
        title = title_el.get_text(strip=True) if title_el else d["title"]

        # --- date extraction ---
        published_at = d.get("published_at")
        if published_at is None:
            date_match = re.search(r"/(20\d{2})(\d{2})(\d{2})/", d["url"])
            if date_match:
                published_at = datetime(
                    int(date_match[1]), int(date_match[2]), int(date_match[3])
                )
        if published_at is None and title_el:
            container = title_el.parent
            for sib in (container.children if container else []):
                if isinstance(sib, NavigableString) or sib is title_el:
                    continue
                if sib.name == "p":
                    txt = sib.get_text(strip=True)
                    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", txt)
                    if m:
                        try:
                            published_at = datetime(int(m[1]), int(m[2]), int(m[3]))
                        except ValueError:
                            pass
                        break
        if published_at is None:
            # 3. Plain ISO date anywhere in HTML
            m2 = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", html)
            if m2:
                try:
                    published_at = datetime.fromisoformat(m2.group(1))
                except ValueError:
                    pass
            # 4. YYYY/MM/DD in script tags
            if published_at is None:
                m3 = re.search(r"(20\d{2})/(\d{2})/(\d{2})", html)
                if m3:
                    try:
                        published_at = datetime(int(m3[1]), int(m3[2]), int(m3[3]))
                    except ValueError:
                        pass

        body_md = self._extract_article_body(soup, html, title, d)

        is_paywalled = self._html_is_excerpt(html)
        if is_paywalled:
            self.log.warning("article %s appears paywalled; got preview only", d["external_id"])

        # --- column / author metadata from p.info ---
        column_name = ""
        author = d.get("author") or {}
        if title_el:
            container = title_el.parent
            for sib in (container.children if container else []):
                if isinstance(sib, NavigableString):
                    continue
                if sib.name == "p" and "info" in (sib.get("class") or []):
                    info_txt = sib.get_text(strip=True)
                    author_name = author.get("name", "")
                    if author_name and info_txt.startswith(author_name):
                        column_name = info_txt[len(author_name):].strip()
                    break

        channel_dir = _author_dir_slug(author)
        extra: dict = {"hkej_author_id": author.get("slug")}
        if column_name:
            extra["column"] = column_name
        header = f"# {title}\n\n*{author.get('name', '')}*"
        if column_name:
            header += f" | {column_name}"
        date_part = published_at.strftime("%Y-%m-%d") if published_at else "undated"
        folder_name = f"{date_part}-{slugify(title, 80)}"
        return ScrapedItem(
            source="hkej",
            channel=channel_dir,
            channel_name=author.get("name") or "Unknown",
            external_id=d["external_id"],
            title=title,
            url=d["url"],
            published_at=published_at,
            language="zh-Hant",
            body_md=f"{header}\n\n{body_md}".strip(),
            raw_html=html,
            extra=extra,
            folder_name=folder_name,
            flat_layout=True,
        )

    @staticmethod
    def _search_recap_for_link(anchor, soup) -> str:
        """Pull the search-result teaser paragraph for an article headline link."""
        h3 = anchor.find_parent("h3")
        if h3 is None:
            return ""
        recap = h3.find_next_sibling("p", class_="recap")
        if not recap:
            return ""
        txt = recap.get_text(" ", strip=True)
        return re.sub(r"\s*全文\s*$", "", txt).strip()

    def _extract_article_body(self, soup, html: str, title: str, d: dict) -> str:
        from bs4 import NavigableString

        is_excerpt = self._html_is_excerpt(html)
        body_paras: list[str] = []

        if not is_excerpt:
            wrapper = soup.select_one("#article-detail-wrapper")
            if wrapper:
                for p in wrapper.find_all("p"):
                    if p.get("id") == "date":
                        continue
                    if "info" in (p.get("class") or []):
                        continue
                    txt = p.get_text(strip=True)
                    if not txt or txt in ("（節錄）", "（完）"):
                        continue
                    if any(c > "\x7f" for c in txt):
                        body_paras.append(txt)
            if body_paras:
                return "\n\n".join(body_paras)

        title_el = soup.find("h1") or soup.find("h2")
        if title_el:
            parent = title_el.parent
            past_h1 = False
            for child in list(parent.children if parent else []):
                if child is title_el:
                    past_h1 = True
                    continue
                if not past_h1:
                    continue
                if isinstance(child, NavigableString):
                    txt = str(child).strip()
                    if txt and len(txt) > 40 and any(c > "\x7f" for c in txt):
                        body_paras.append(txt)
                    continue
                if child.name in ("script", "style"):
                    continue
                if child.get("id") == "login-message-wrapper":
                    break
                classes = " ".join(child.get("class", []))
                if any(x in classes for x in ("enlargeImg", "thumb", "hkej_detail_thumb")):
                    continue
                if child.name == "p":
                    txt = child.get_text(strip=True)
                    if txt in ("（節錄）", "（完）", ""):
                        if txt == "（節錄）":
                            break
                        continue
                    if txt and any(c > "\x7f" for c in txt) and not txt.startswith("20"):
                        body_paras.append(txt)

        og = soup.find("meta", attrs={"property": "og:description"})
        og_text = (og.get("content") or "").strip() if og else ""
        search_recap = (d.get("search_recap") or "").strip()

        if is_excerpt and not body_paras:
            body_paras = [x for x in (search_recap, og_text) if x]
        elif not body_paras:
            body_paras = [x for x in (og_text, search_recap) if x]

        # Drop metadata-only lines accidentally captured before h1.
        cleaned: list[str] = []
        for para in body_paras:
            if para in (title, f"{d.get('author', {}).get('name', '')}金融第十人"):
                continue
            if re.fullmatch(r"20\d{2}年\d{1,2}月\d{1,2}日", para):
                continue
            cleaned.append(para)
        return "\n\n".join(cleaned)

