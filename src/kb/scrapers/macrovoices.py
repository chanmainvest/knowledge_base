"""MacroVoices scraper.

Public episode index lives at https://www.macrovoices.com/podcast-transcripts
and https://www.macrovoices.com/all-podcasts (paginated). Login is needed to
download the full PDF transcript and the slide deck of each episode. We use
Playwright for the auth + per-episode download, and parse the rendered HTML
for the show notes.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from ..config import DATA_DIR, settings
from ..io_md import slugify
from .base import BaseScraper, ScrapedItem


BASE = "https://www.macrovoices.com"
LIST_URL = f"{BASE}/podcast-transcripts"

_MV_MONTHS = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
              "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}


def _parse_mv_date(html: str) -> datetime | None:
    # ISO date first
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", html)
    if m:
        try:
            return datetime.fromisoformat(m.group(1))
        except ValueError:
            pass
    # "Created: 23 April 2026"
    m = re.search(r"Created:\s*(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", html)
    if m:
        day, mon, year = int(m.group(1)), _MV_MONTHS.get(m.group(2).lower()), int(m.group(3))
        if mon:
            return datetime(year, mon, day)
    # "Published: 23 April 2026"
    m = re.search(r"Published:\s*(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", html)
    if m:
        day, mon, year = int(m.group(1)), _MV_MONTHS.get(m.group(2).lower()), int(m.group(3))
        if mon:
            return datetime(year, mon, day)
    return None


class MacroVoicesScraper(BaseScraper):
    code = "macrovoices"
    name = "MacroVoices"
    source_code = "blog"

    async def _login(self, page) -> None:
        s = settings()
        if not (s.macrovoices_user and s.macrovoices_pass):
            self.log.warning("MacroVoices credentials missing; will scrape free pages only")
            return
        await page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        # The site uses a Joomla login form (#form-login or .login)
        for sel_user, sel_pass, sel_btn in [
            ("input[name='username']", "input[name='password']", "button[type=submit]"),
            ("#mod-login-username", "#mod-login-password", "button.btn-primary"),
            ("input#username", "input#password", "input[type=submit]"),
        ]:
            try:
                await page.fill(sel_user, s.macrovoices_user, timeout=4000)
                await page.fill(sel_pass, s.macrovoices_pass, timeout=4000)
                await page.click(sel_btn, timeout=4000)
                await page.wait_for_load_state("networkidle", timeout=20000)
                self.log.info("MacroVoices login submitted (%s)", sel_user)
                return
            except Exception:
                continue
        self.log.warning("MacroVoices login form not detected")

    def already_scraped(self, d: dict) -> bool:
        slug = slugify(d["external_id"], 60)
        mv_dir = DATA_DIR / "blog" / "macrovoices"
        # Flat layout: data/blog/macrovoices/<year>/<date>-<slug>.md
        for md_path in mv_dir.glob("*/*.md"):
            if slug in md_path.stem:
                return md_path.stat().st_size > 500
        return False

    async def discover(self, limit: int | None = None) -> AsyncIterator[dict]:
        from playwright.async_api import async_playwright
        self._seen_ids: set[str] = set()
        n = 0
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=settings().scrape_user_agent)
            page = await ctx.new_page()
            await self._login(page)

            page_num = 1
            consecutive_fail = 0
            while True:
                url = f"{LIST_URL}?start={(page_num - 1) * 20}"
                await self.limiter.wait(url)
                self.log.info("listing %s", url)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as exc:
                    self.log.warning("list page failed (%s): %s", url, exc)
                    consecutive_fail += 1
                    if consecutive_fail >= 3:
                        break
                    page_num += 1
                    await asyncio.sleep(5)
                    continue
                consecutive_fail = 0
                # Episode links look like /podcast-transcripts/<slug>
                try:
                    links = await page.eval_on_selector_all(
                        "a[href*='/podcast-transcripts/'], a[href*='/all-podcasts/']",
                        "els => els.map(e => ({href: e.href, text: e.innerText}))",
                    )
                except Exception as exc:
                    self.log.warning("list eval failed: %s", exc)
                    page_num += 1
                    continue
                fresh = 0
                for ln in links:
                    # Strip query/fragment so ?tmpl=component variants dedupe
                    href = ln["href"].split("?")[0].split("#")[0].rstrip("/")
                    if not re.search(r"/podcast-transcripts/\d+-[^/]+$", href):
                        continue
                    ext_id = href.rsplit("/", 1)[-1]
                    # Just the numeric prefix as canonical id (e.g. 1519)
                    m = re.match(r"(\d+)-", ext_id)
                    canonical_id = m.group(1) if m else ext_id
                    if canonical_id in self._seen_ids:
                        continue
                    self._seen_ids.add(canonical_id)
                    fresh += 1
                    yield {
                        "external_id": ext_id,
                        "url": href,
                        "title": ln["text"].strip() or href,
                    }
                    n += 1
                    if limit and n >= limit:
                        await browser.close()
                        return
                if fresh == 0:
                    break
                page_num += 1
                await asyncio.sleep(2)
            await browser.close()

    @staticmethod
    def _strip_pagination_nav(soup) -> None:
        """Remove Joomla's multi-page navigation in-place.

        When an article is split via the pagebreak plugin, every page renders an
        "Article Index" TOC (a <ul class="pagination"> of ?start=N links) plus
        per-item Print/Email actions. These leak into the markdown body as bare
        links ("Page 2", "Page 3", ...) with no transcript value, so strip them
        before converting the body to markdown.
        """
        from bs4 import Tag
        # Drop the pagination list and any "Article Index" heading that titles it.
        for ul in soup.find_all("ul", class_="pagination"):
            ul.decompose()
        # Some templates wrap the TOC in a div with a known id/class.
        for div in soup.find_all(
            "div", id=re.compile(r"article-index|articleIndex", re.I)
        ):
            div.decompose()
        for div in soup.find_all("div", class_=re.compile(r"pagination|article-index", re.I)):
            div.decompose()
        # Remove the literal "Article Index" heading(s) left behind.
        for tag in soup.find_all(["h3", "h2", "h4"]):
            if tag.get_text(strip=True).lower() == "article index":
                tag.decompose()

    def _article_body_md(self, soup) -> str:
        """Pick the main article element, strip nav chrome, return as markdown."""
        from markdownify import markdownify as md_of
        self._strip_pagination_nav(soup)
        article = soup.find("div", id=re.compile("itemFullText|item-content|content")) or soup.body
        return md_of(str(article), heading_style="ATX") if article else ""

    async def _fetch_remaining_pages(self, ctx, base_url: str) -> str:
        """Walk ?start=1,2,... and concatenate each page's article body.

        Used only as a fallback when ?showall=1 did not collapse a multi-page
        article. Stops at the first page that yields no fresh body or after a
        hard cap (50) to bound the walk.
        """
        from bs4 import BeautifulSoup
        parts: list[str] = []
        for n in range(1, 51):
            url = f"{base_url}?start={n}"
            await self.limiter.wait(url)
            try:
                resp = await ctx.request.get(url)
                if not resp.ok:
                    break
                html = await resp.text()
            except Exception as exc:  # noqa: BLE001
                self.log.warning("page walk fetch fail %s: %s", url, exc)
                break
            soup = BeautifulSoup(html, "lxml")
            chunk = self._article_body_md(soup)
            if not chunk.strip() or chunk.strip() in {p.strip() for p in parts}:
                break
            parts.append(chunk)
        return "\n\n".join(parts)

    async def fetch(self, d: dict) -> ScrapedItem | None:
        from playwright.async_api import async_playwright
        await self.limiter.wait(d["url"])
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=settings().scrape_user_agent,
                accept_downloads=True,
            )
            page = await ctx.new_page()
            # Skip login since /login returns 404 on this site; transcript text is public.
            # Joomla splits long articles across pages via a pagebreak plugin; the
            # bare URL returns only page 1 (+ an "Article Index" table of contents).
            # `?showall=1` makes Joomla render the entire article on one page.
            fetch_url = f"{d['url']}?showall=1"
            try:
                await page.goto(fetch_url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                self.log.warning("goto failed for %s: %s", fetch_url, exc)
                await browser.close()
                return None
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            html = await page.content()

            # Extract publish date and download links
            from bs4 import BeautifulSoup, Tag
            soup = BeautifulSoup(html, "lxml")
            title = (soup.find("h1") or soup.find("h2"))
            title_text = title.get_text(strip=True) if title else d["title"]
            published_at = _parse_mv_date(html)

            body_md = self._article_body_md(soup)

            # Defensive fallback: if ?showall=1 didn't actually concatenate (site
            # changed, or the episode has no pagebreaks but is short), and the
            # rendered body still carries ?start= page links, walk them and stitch
            # the per-page bodies together. `showall` is verified to work as of
            # 2026-08, but this guards against Joomla config / template changes.
            if "?start=" in body_md and len(body_md) < 8000:
                self.log.info("showall produced paginated body for %s; walking pages", d["url"])
                extra = await self._fetch_remaining_pages(ctx, d["url"])
                if extra:
                    body_md = body_md + "\n\n" + extra
                    # Re-strip any nav that the stitched pages reintroduced.
                    soup2 = BeautifulSoup(f"<div>{body_md}</div>", "lxml")
                    body_md = self._article_body_md(soup2)

            # Find PDF transcript + slide deck links
            pdfs = [a["href"] for a in soup.find_all("a", href=True)
                    if a["href"].lower().endswith(".pdf")]
            slides_path = None
            slides_pdfs: list[str] = []
            transcript_pdfs: list[str] = []
            for href in pdfs:
                full = href if href.startswith("http") else f"{BASE}{href}"
                if "slide" in href.lower() or "deck" in href.lower():
                    slides_pdfs.append(full)
                else:
                    transcript_pdfs.append(full)

            # Save slides / transcripts under data/raw/blog/macrovoices/<year>/
            date_str = published_at.date().isoformat() if published_at else "undated"
            year_str = published_at.strftime("%Y") if published_at else "undated"
            ext_slug = slugify(d["external_id"], 60)
            stem = f"{date_str}-{ext_slug}"
            raw_dir = DATA_DIR / "raw" / "blog" / "macrovoices" / year_str
            raw_dir.mkdir(parents=True, exist_ok=True)
            for url in slides_pdfs:
                try:
                    await self.limiter.wait(url)
                    resp = await ctx.request.get(url)
                    if resp.ok:
                        out = raw_dir / f"{stem}.slides.pdf"
                        out.write_bytes(await resp.body())
                        slides_path = str(out)
                        break
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("slides download fail: %s", exc)
            for url in transcript_pdfs:
                try:
                    await self.limiter.wait(url)
                    resp = await ctx.request.get(url)
                    if resp.ok:
                        (raw_dir / f"{stem}.transcript.pdf").write_bytes(await resp.body())
                        # extract text
                        try:
                            import pypdf
                            r = pypdf.PdfReader(raw_dir / f"{stem}.transcript.pdf")
                            text = "\n\n".join((p.extract_text() or "") for p in r.pages)
                            (raw_dir / f"{stem}.transcript.txt").write_text(text, encoding="utf-8")
                            body_md += "\n\n## Full Transcript\n\n" + text
                        except Exception as exc:
                            self.log.info("pypdf failed: %s", exc)
                        break
                except Exception as exc:
                    self.log.warning("transcript download fail: %s", exc)

            await browser.close()

        return ScrapedItem(
            source=self.effective_source_code,
            channel="macrovoices",
            channel_name="MacroVoices",
            external_id=d["external_id"],
            title=title_text,
            url=d["url"],
            published_at=published_at,
            language="en",
            body_md=body_md,
            raw_html=html,
            slides_path=slides_path,
            extra={"slides_pdfs": slides_pdfs, "transcript_pdfs": transcript_pdfs},
            folder_name=stem,
            flat_layout=True,
        )
