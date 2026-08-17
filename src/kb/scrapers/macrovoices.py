"""MacroVoices scraper.

The site has two episode indices whose coverage differs — a cross-index
dedup gotcha the scraper must handle:

* **Live index** — ``/podcasts-collection/macrovoices-podcasts``. This is the
  current, maintained list ("Page 1 of ~29", 20 episodes/page, item-offset
  ``?start=N``). It spans the *whole* archive (newest episode → oldest, back
  to early 2016) and is the only place new episodes appear. Episode links are
  root-level ``/<article_id>-macrovoices-<episode_num>-<slug>``.
* **Legacy index** — ``/podcast-transcripts`` (alias ``/all-podcasts``). This
  is **frozen at episode 1538 / 25 June 2026** and no longer receives new
  episodes; it is kept only as a fallback for the old-format pages.

The two indices assign **different article IDs to the same episode** (e.g.
Lyn Alden ep538 is article 1538 on the legacy index but 1537 on the live
index), so article IDs are *not* a stable cross-index key. We dedup by the
**episode slug text** (guest + title), which is identical on both, and by the
episode number embedded in the live-index URL (``macrovoices-<NNN>``).

Transcript source also differs by page format:

* **Old-format pages** (``/podcast-transcripts/<id>``): the inline transcript
  text is rendered across Joomla pagebreak pages; ``?showall=1`` collapses it
  onto one page.
* **New-format pages** (``/<id>-macrovoices-<NNN>-<slug>``): there is *no*
  inline transcript — the body is a "Download the podcast transcript
  [Click Here]" link to a PDF at
  ``/guest-content/list-guest-transcripts/<id>/file``. We download that PDF
  and text-extract it with pypdf.

Discovery walks the **live index**; ``fetch()`` branches on URL shape so both
page formats are handled. The flat-file layout (``data/blog/macrovoices/<YYYY>/``)
is preserved.
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
# Live, maintained episode index (newest episodes appear here only).
PODCASTS_URL = f"{BASE}/podcasts-collection/macrovoices-podcasts"
# Legacy index — frozen at episode 1538 (25 June 2026). Old-format pages under
# here still serve inline transcripts via ?showall=1, so it stays as a fallback.
LIST_URL = f"{BASE}/podcast-transcripts"
GUEST_TRANSCRIPTS_URL = f"{BASE}/guest-content/list-guest-transcripts"
# Live-index pagination: 20 episodes per page, ?start=N is an item offset.
PODCASTS_PAGE_SIZE = 20

# Root-level live-index episode link: /<article_id>-macrovoices-<ep_num>-<slug>
_RE_LIVE_EPISODE = re.compile(
    r"/(\d{3,4})-macrovoices-(\d+)-([a-z0-9][a-z0-9-]*?)(?:[\"'/?#]|$)"
)
# Old-format episode link: /podcast-transcripts/<article_id>-<slug>
_RE_LEGACY_EPISODE = re.compile(r"/podcast-transcripts/(\d+)-([^/?#\"']+)$")

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
        """Cross-index dedup.

        The two indices assign different article IDs to the same episode, so
        matching on the numeric id would re-scrape episodes already on disk
        under the other index's id. We match in two layers:

        1. **Slug-text prefix** (24 chars) — catches the same episode under a
           different article id, robust to filename truncation.
        2. **Guest-token overlap** — a fallback for when the slug itself differs
           across indices: diacritic transliteration (``stöferle`` →
           ``stoeferle`` vs ``sto-ferle``) and guest-name reordering
           (``jeffrey-snider-luke-gromen`` vs ``luke-gromen-jeff-snider``).
           We count significant tokens (len ≥ 4) shared with an existing
           filename stem; ≥ 3 shared tokens is a confident match.

        Filenames look like ``<YYYY>-<MM>-<DD>-<artid>-<slug>.md`` (legacy) or
        ``<YYYY>-<MM>-<DD>-mv-<ep>-<slug>.md`` (live).
        """
        mv_dir = DATA_DIR / "blog" / "macrovoices"
        needle = d.get("dedup_slug")  # set by discover(); slug-text only
        ep_num = d.get("episode_num")
        prefix = needle[:24] if needle else None
        my_tokens = set(re.findall(r"[a-z]{4,}", (needle or "").lower()))
        for md_path in mv_dir.glob("*/*.md"):
            if md_path.name == "README.md":
                continue
            stem = md_path.stem
            if md_path.stat().st_size <= 500:
                continue
            if prefix and prefix in stem:
                return True
            # Live-index descriptors also carry an episode number; old files
            # don't encode it, so this only matches newer mv-<ep> filenames.
            if ep_num and f"mv-{ep_num}" in stem:
                return True
            # Token-overlap fallback for transliteration/reordering mismatches.
            if my_tokens:
                stem_tokens = set(re.findall(r"[a-z]{4,}", stem.lower()))
                if len(my_tokens & stem_tokens) >= 3:
                    return True
        return False

    async def discover(self, limit: int | None = None) -> AsyncIterator[dict]:
        """Walk the **live** ``/podcasts-collection`` index.

        New episodes (post-redesign) appear *only* here; the legacy
        ``/podcast-transcripts`` index is frozen at episode 1538. This index
        paginates 20 per page (``?start=N`` is an item offset) and spans the
        whole archive, so it is the single source of truth for discovery.

        Each descriptor carries a ``dedup_slug`` (the slug text after the
        ``<artid>-macrovoices-<NNN>-`` prefix) so :meth:`already_scraped` can
        match against existing legacy-index filenames that use a different
        article id for the same episode.
        """
        from playwright.async_api import async_playwright
        self._seen_episodes: set[int] = set()
        n = 0
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=settings().scrape_user_agent)
            page = await ctx.new_page()
            await self._login(page)

            start = 0
            consecutive_fail = 0
            while True:
                url = f"{PODCASTS_URL}?start={start}"
                await self.limiter.wait(url)
                self.log.info("listing %s", url)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as exc:
                    self.log.warning("list page failed (%s): %s", url, exc)
                    consecutive_fail += 1
                    if consecutive_fail >= 3:
                        break
                    start += PODCASTS_PAGE_SIZE
                    await asyncio.sleep(5)
                    continue
                consecutive_fail = 0
                try:
                    raw_links = await page.eval_on_selector_all(
                        "a[href*='macrovoices-']",
                        "els => els.map(e => e.href)",
                    )
                except Exception as exc:
                    self.log.warning("list eval failed: %s", exc)
                    start += PODCASTS_PAGE_SIZE
                    continue
                fresh = 0
                seen_on_page: set[int] = set()
                for href in raw_links:
                    # Strip query/fragment (print/tmpl variants) and collapse.
                    clean = href.split("?")[0].split("#")[0].rstrip("/")
                    m = _RE_LIVE_EPISODE.search(clean)
                    if not m:
                        continue
                    # Skip MP3-file/research pages — they match the slug pattern
                    # but carry no transcript (just an audio download or a 404).
                    if "/macro-voices-research/" in clean or "/podcast-mp3-files/" in clean:
                        continue
                    article_id, ep_num, slug_text = m.group(1), int(m.group(2)), m.group(3)
                    if ep_num in seen_on_page:
                        continue
                    seen_on_page.add(ep_num)
                    if ep_num in self._seen_episodes:
                        continue
                    self._seen_episodes.add(ep_num)
                    fresh += 1
                    # Canonical external_id encodes the episode number so it is
                    # stable across indices; the article id lives in the URL.
                    yield {
                        "external_id": f"mv-{ep_num}-{slug_text}",
                        "url": clean,
                        "title": slug_text.replace("-", " ").strip() or clean,
                        "episode_num": ep_num,
                        "article_id": article_id,
                        # Slug-text tail (without the artid/macrovoices-NNN prefix)
                        # used for cross-index dedup against legacy filenames.
                        "dedup_slug": slug_text,
                    }
                    n += 1
                    if limit and n >= limit:
                        await browser.close()
                        return
                if fresh == 0:
                    break
                start += PODCASTS_PAGE_SIZE
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

    @staticmethod
    def _is_new_format(url: str) -> bool:
        """New-format episode page: ``/<id>-macrovoices-<NNN>-<slug>``.

        Old-format pages live under ``/podcast-transcripts/<id>-<slug>``.
        """
        return bool(_RE_LIVE_EPISODE.search(url))

    async def fetch(self, d: dict) -> ScrapedItem | None:
        await self.limiter.wait(d["url"])
        # New-format pages (post-redesign) are PDF-only; old-format pages still
        # serve the inline transcript via ?showall=1.
        if self._is_new_format(d["url"]):
            return await self._fetch_new_format(d)
        return await self._fetch_legacy_format(d)

    async def _fetch_new_format(self, d: dict) -> ScrapedItem | None:
        """Fetch a post-redesign episode page and its PDF transcript.

        These pages (``/<id>-macrovoices-<NNN>-<slug>``) carry *no* inline
        transcript — the body is a "Download the podcast transcript
        [Click Here]" link to a PDF at
        ``/guest-content/list-guest-transcripts/<id>/file``. We grab the show
        notes from the page, download the PDF, and text-extract the dialogue.
        """
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=settings().scrape_user_agent,
                accept_downloads=True,
            )
            page = await ctx.new_page()
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

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            # New-format pages put the site banner in <h1> ("Welcome to Macro
            # Voices"); the real episode title is the <h2> (e.g.
            # "MacroVoices #544 Viktor Shvets: How Markets Survive Disruption")
            # and matches <title>. Prefer <h2> when <h1> is the generic banner.
            h1 = soup.find("h1")
            h1_text = h1.get_text(strip=True) if h1 else ""
            if h1_text in ("", "Welcome to Macro Voices"):
                title_el = soup.find("h2")
                title_text = title_el.get_text(strip=True) if title_el else d["title"]
            else:
                title_text = h1_text
            published_at = _parse_mv_date(html)

            # Show notes = page body minus chrome (reuse the same stripper so
            # pagination/nav junk doesn't leak in even though these pages
            # aren't pagebroken).
            body_md = self._article_body_md(soup)

            # Dead-link / non-episode guard. The live index carries a handful of
            # dangling links that resolve to the site 404 page (title "404Error")
            # or redirect to the homepage, whose <h2> is still the generic
            # "Welcome to Macro Voices" with no episode title and no transcript
            # PDF link. Detect that and skip rather than write a junk file.
            if title_text in ("404Error", "Welcome to Macro Voices"):
                self.log.info("skip (dead link, %r): %s", title_text, d["url"])
                await browser.close()
                return None

            # The transcript PDF link: the <a> right after the literal
            # "Download the podcast transcript" label (the "[Click Here]" link),
            # or any /guest-content/list-guest-transcripts/<id>/file href.
            transcript_pdf_url: str | None = None
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/guest-content/list-guest-transcripts/" in href and href.rstrip("/").endswith("/file"):
                    transcript_pdf_url = href if href.startswith("http") else f"{BASE}{href}"
                    break
            transcript_pdfs = [transcript_pdf_url] if transcript_pdf_url else []

            date_str = published_at.date().isoformat() if published_at else "undated"
            year_str = published_at.strftime("%Y") if published_at else "undated"
            ext_slug = slugify(d["external_id"], 60)
            stem = f"{date_str}-{ext_slug}"
            raw_dir = DATA_DIR / "raw" / "blog" / "macrovoices" / year_str
            raw_dir.mkdir(parents=True, exist_ok=True)

            if transcript_pdf_url:
                try:
                    await self.limiter.wait(transcript_pdf_url)
                    resp = await ctx.request.get(transcript_pdf_url)
                    if resp.ok:
                        (raw_dir / f"{stem}.transcript.pdf").write_bytes(await resp.body())
                        try:
                            import pypdf
                            r = pypdf.PdfReader(raw_dir / f"{stem}.transcript.pdf")
                            text = "\n\n".join((p.extract_text() or "") for p in r.pages)
                            (raw_dir / f"{stem}.transcript.txt").write_text(text, encoding="utf-8")
                            body_md += "\n\n## Full Transcript\n\n" + text
                        except Exception as exc:  # noqa: BLE001
                            self.log.info("pypdf failed: %s", exc)
                    else:
                        self.log.warning("transcript pdf status %s for %s", resp.status, transcript_pdf_url)
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("transcript download fail: %s", exc)
            else:
                self.log.warning("no transcript PDF link on %s", d["url"])

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
            extra={"slides_pdfs": [], "transcript_pdfs": transcript_pdfs},
            folder_name=stem,
            flat_layout=True,
        )

    async def _fetch_legacy_format(self, d: dict) -> ScrapedItem | None:
        """Fetch an old-format ``/podcast-transcripts/<id>`` page (inline text).

        ``?showall=1`` collapses Joomla's pagebreak pagination onto one page;
        a defensive per-page walk runs if showall ever fails to concatenate.
        """
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=settings().scrape_user_agent,
                accept_downloads=True,
            )
            page = await ctx.new_page()
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

            from bs4 import BeautifulSoup
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
                    soup2 = BeautifulSoup(f"<div>{body_md}</div>", "lxml")
                    body_md = self._article_body_md(soup2)

            # Find PDF transcript + slide deck links (older episodes attach
            # slide decks and PDFs directly on the page).
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
