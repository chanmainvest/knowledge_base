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
        for date_dir in (DATA_DIR / "macrovoices" / "macrovoices").glob("*"):
            if date_dir.name.endswith(slug) and (date_dir / "content.md").exists():
                return (date_dir / "content.md").stat().st_size > 500
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
            # Skip login since /login returns 404 on this site; transcript text is public
            try:
                await page.goto(d["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                self.log.warning("goto failed for %s: %s", d["url"], exc)
                await browser.close()
                return None
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            html = await page.content()

            # Extract publish date and download links
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            title = (soup.find("h1") or soup.find("h2"))
            title_text = title.get_text(strip=True) if title else d["title"]
            published_at = _parse_mv_date(html)

            # Convert main article body to markdown
            from markdownify import markdownify as md_of
            article = soup.find("div", id=re.compile("itemFullText|item-content|content")) or soup.body
            body_md = md_of(str(article), heading_style="ATX") if article else ""

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

            # Try to download via the authenticated context
            folder = (DATA_DIR / "macrovoices" / "macrovoices" /
                      f"{(published_at.date().isoformat() if published_at else 'undated')}"
                      f"__{slugify(d['external_id'], 60)}")
            folder.mkdir(parents=True, exist_ok=True)
            for url in slides_pdfs:
                try:
                    await self.limiter.wait(url)
                    resp = await ctx.request.get(url)
                    if resp.ok:
                        out = folder / "slides.pdf"
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
                        (folder / "transcript.pdf").write_bytes(await resp.body())
                        # extract text
                        try:
                            import pypdf
                            r = pypdf.PdfReader(folder / "transcript.pdf")
                            text = "\n\n".join((p.extract_text() or "") for p in r.pages)
                            (folder / "transcript.txt").write_text(text, encoding="utf-8")
                            body_md += "\n\n## Full Transcript\n\n" + text
                        except Exception as exc:
                            self.log.info("pypdf failed: %s", exc)
                        break
                except Exception as exc:
                    self.log.warning("transcript download fail: %s", exc)

            await browser.close()

        return ScrapedItem(
            source="macrovoices",
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
        )
