"""Goehring & Rozencwajg (gorozen) scraper.

Two content streams under one ``blog`` source:

* **Blog posts** — static HTML at ``https://blog.gorozen.com/blog`` (paginated
  ``/blog/page/N``). Fetched with httpx and parsed with BeautifulSoup; no JS.
* **Quarterly commentaries** — a form-gated PDF at
  ``https://www.gorozen.com/commentaries/<slug>``. The HubSpot embedded form
  refuses to render under automated browsers (bot detection), so we submit it
  directly via the HubSpot public form API instead, follow the returned
  ``redirectUri`` (newer forms) or ``inlineMessage`` (older forms) to the PDF
  link, download it to ``data/raw/blog/gorozen/<year>/``, and extract its text
  with pypdf.

Selected via the ``--source-type`` flag (``blog`` | ``commentary``); omit it to
scrape both. Mirrors how :mod:`kb.scrapers.madxcap` splits dcard/facebook under
one ``blog`` source.
"""
from __future__ import annotations

import asyncio
import ipaddress
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


BLOG_BASE = "https://blog.gorozen.com"
COMMENTARY_BASE = "https://www.gorozen.com"

# Form field values for the commentary download gate (not secrets).
GOROZEN_FIRST = "hev"
GOROZEN_LAST = "angel"
GOROZEN_EMAIL = "gorozen@hevangel.com"
GOROZEN_CATEGORY = "Individual or Personal Investor"

# Slugs: newer = "YYYY-q#" (2026-q1), older = "#qYYYY" (2q2024).
_COMMENTARY_SLUG = re.compile(r"/commentaries/(202\d-q[1-4]|[1-4]q20(?:1[7-9]|2\d))(?:[/?#]|$)")


def _is_safe_url(url: str) -> bool:
    """Reject non-http(s) URLs and localhost/loopback/private/reserved hosts.

    Applied to page-extracted URLs before we fetch them, as defense-in-depth so
    a malformed ``href`` from a scraped page can never drive us onto a private
    address or a non-http scheme.
    """
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host or host in ("localhost",):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # a DNS name — allowed
    return not (ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local)


def _parse_blog_date(text: str) -> datetime | None:
    """Parse blog dates shown as MM/DD/YYYY."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    # Some posts may carry ISO dates.
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d")
        except ValueError:
            pass
    return None


# HubSpot chrome that shares the post body with no distinguishing markup. The
# CTA is a fixed "Want to learn more ... download ... letter" line and the SEC
# disclaimer always starts with "Registration with the SEC". They are boiler-
# plate on every post, so strip them by text pattern rather than fragile CSS.
_BLOG_CTA_RE = re.compile(
    r"\*{0,3}Want to learn more from Goehring & Rozencwajg\?.*",
    re.DOTALL | re.IGNORECASE,
)
_BLOG_DISCLAIMER_RE = re.compile(
    r"\*{0,2}Registration with the SEC should not be construed.*",
    re.DOTALL,
)


def _strip_blog_chrome(body_md: str) -> str:
    """Remove the fixed CTA line and SEC disclaimer from a blog post body."""
    out = _BLOG_DISCLAIMER_RE.sub("", body_md)
    out = _BLOG_CTA_RE.sub("", out)
    return out.strip()


# Trailing chrome stripped from commentary card titles pulled off the index:
# a quarter tag like "2025Q4" and/or a "Month DD, YYYY" date often leaks into
# the link text because the card bundles title + date in one container.
_TITLE_TRAIL_RE = re.compile(
    r"\s+("
    r"\d{4}[Qq][1-4]"               # 2025Q4
    r"|"
    r"[1-4][Qq]\d{4}"               # Q42025
    r"|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{4}"          # May 21, 2026
    r")\s*$",
)


def _clean_commentary_title(title: str) -> str:
    """Strip trailing date/quarter-tag chrome from a commentary card title."""
    if not title:
        return title
    out = title.strip()
    # Apply repeatedly in case date + quarter tag both trail.
    for _ in range(3):
        new = _TITLE_TRAIL_RE.sub("", out).strip()
        if new == out:
            break
        out = new
    return out


# Quarter end-dates, keyed by quarter number (1→Mar 31, …, 4→Dec 31).
_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _date_from_slug(slug: str) -> datetime | None:
    """Derive a publication date from a commentary slug.

    Slugs encode the quarter and year in two shapes: ``YYYY-q#`` (2025-q4) and
    ``#qYYYY`` (1q2024). We return the last day of that quarter as a best-effort
    ``published_at`` when the page HTML exposes no parseable date.
    """
    m = re.match(r"(?:20\d{2})-q([1-4])$", slug)
    if m:
        mon, day = _QUARTER_END[int(m.group(1))]
        return datetime(int(slug[:4]), mon, day)
    m = re.match(r"([1-4])q(20\d{2})$", slug)
    if m:
        mon, day = _QUARTER_END[int(m.group(1))]
        return datetime(int(m.group(2)), mon, day)
    return None


_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}


def _parse_commentary_date(text: str) -> datetime | None:
    """Parse commentary dates like 'May 21, 2026' or 'August 27, 2025'."""
    if not text:
        return None
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            try:
                return datetime(int(m.group(3)), mon, int(m.group(2)))
            except ValueError:
                pass
    return _parse_blog_date(text)


class GorozenScraper(BaseScraper):
    code = "gorozen"
    name = "Goehring & Rozencwajg"
    source_code = "blog"

    def __init__(self) -> None:
        super().__init__()
        self._discovered: dict[str, dict] = {}

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            timeout=60.0,
            http2=True,
        )

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------
    async def _discover_blog(
        self, limit: int | None
    ) -> AsyncIterator[dict]:
        """Walk the blog listing pages; yield one descriptor per post."""
        seen: set[str] = set()
        n = 0
        page_no = 1
        async with await self._client() as client:
            while True:
                # The blog index lives at /blog (the bare host root 404s).
                # Page 1 = /blog; page N = /blog/page/N.
                url = f"{BLOG_BASE}/blog" if page_no == 1 else f"{BLOG_BASE}/blog/page/{page_no}"
                await self.limiter.wait(url)
                self.log.info("blog listing %s", url)
                try:
                    r = await client.get(url)
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("blog list fetch failed %s: %s", url, exc)
                    break
                if r.status_code == 404 or r.status_code >= 400:
                    self.log.info("blog listing ended at page %d (status %d)",
                                  page_no, r.status_code)
                    break
                soup = BeautifulSoup(r.text, "lxml")
                fresh = 0
                for a in soup.find_all("a", href=True):
                    href = str(a["href"]).split("?")[0].split("#")[0]
                    # Normalize relative links.
                    abs_href = urljoin(BLOG_BASE + "/", href)
                    path = urlparse(abs_href).path.rstrip("/")
                    # Must be exactly /blog/<slug>, not /blog, /blog/page/N,
                    # /blog/all, /blog/author/..., /blog/rss.xml, etc.
                    if not path.startswith("/blog/"):
                        continue
                    slug = path[len("/blog/"):]
                    if not slug or "/" in slug:
                        continue
                    if slug.lower() in {"page", "all", "rss.xml", "author", "tag", "topic"}:
                        continue
                    if slug.lower().startswith(("page/", "author/", "tag/", "topic/")):
                        continue
                    if slug in seen:
                        continue
                    seen.add(slug)
                    # Title: prefer link text, else a nearby heading.
                    title = a.get_text(strip=True)
                    if not title or len(title) < 5:
                        heading = a.find_parent(["h2", "h3", "h4", "li", "div"])
                        if heading:
                            title = heading.get_text(" ", strip=True)
                    # Date: look around the link for an MM/DD/YYYY stamp.
                    published_at = None
                    ctx = a.find_parent(["div", "li", "article", "section"]) or a.parent
                    if ctx:
                        published_at = _parse_blog_date(ctx.get_text(" ", strip=True))
                    fresh += 1
                    yield {
                        "external_id": slug,
                        "url": abs_href,
                        "title": title or slug,
                        "published_at": published_at,
                        "source_type": "blog",
                    }
                    n += 1
                    if limit and n >= limit:
                        return
                if fresh == 0:
                    self.log.info("blog listing: no new posts on page %d; stopping", page_no)
                    break
                page_no += 1
                await asyncio.sleep(1)

    async def _discover_commentaries(
        self, limit: int | None
    ) -> AsyncIterator[dict]:
        """Fetch the commentaries index; yield one descriptor per report."""
        url = f"{COMMENTARY_BASE}/commentaries"
        await self.limiter.wait(url)
        async with await self._client() as client:
            try:
                r = await client.get(url)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("commentary index fetch failed: %s", exc)
                return
            if r.status_code >= 400:
                self.log.warning("commentary index status %d", r.status_code)
                return
            soup = BeautifulSoup(r.text, "lxml")

        # Collect all links first, keeping the most descriptive title per slug,
        # so a generic nav label ("latest research") pointing at the newest
        # commentary doesn't shadow the real card title.
        entries: dict[str, dict] = {}
        for a in soup.find_all("a", href=True):
            href = str(a["href"]).split("?")[0].split("#")[0]
            abs_href = urljoin(COMMENTARY_BASE + "/", href)
            m = _COMMENTARY_SLUG.search(abs_href)
            if not m:
                continue
            slug = m.group(1)
            ctx = a.find_parent(["div", "li", "article", "section"]) or a.parent
            # Prefer the parent container's text (the card with title + date).
            title = a.get_text(" ", strip=True)
            if ctx:
                ctx_text = ctx.get_text(" ", strip=True)
                if len(ctx_text) > len(title):
                    title = ctx_text
            title = _clean_commentary_title(title)
            published_at = None
            if ctx:
                published_at = _parse_commentary_date(ctx.get_text(" ", strip=True))
            prev = entries.get(slug)
            if prev is None or len(title) > len(prev["title"]):
                entries[slug] = {
                    "external_id": slug,
                    "url": f"{COMMENTARY_BASE}/commentaries/{slug}",
                    "title": title or slug,
                    "published_at": prev["published_at"] if prev else published_at,
                    "source_type": "commentary",
                }
        n = 0
        for d in entries.values():
            yield d
            n += 1
            if limit and n >= limit:
                return

    async def discover(
        self,
        limit: int | None = None,
        *,
        source_type: str | None = None,
    ) -> AsyncIterator[dict]:
        """Yield descriptors. ``source_type`` filters ``blog`` vs ``commentary``;
        ``None`` discovers both (blog first, then commentaries)."""
        if source_type in (None, "blog"):
            async for d in self._discover_blog(limit):
                yield d
        if source_type in (None, "commentary"):
            async for d in self._discover_commentaries(limit):
                yield d

    # ------------------------------------------------------------------
    # caching / skip
    # ------------------------------------------------------------------
    def already_scraped(self, d: dict) -> bool:
        ext_slug = slugify(d["external_id"], 60)
        channel_dir = DATA_DIR / self.effective_source_code / "gorozen"
        if not channel_dir.exists():
            return False
        for md_path in channel_dir.glob("*/*.md"):
            if md_path.stat().st_size < 200:
                continue
            if ext_slug in md_path.stem:
                return True
        return False

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------
    async def _fetch_blog(self, d: dict) -> ScrapedItem | None:
        url = d["url"]
        async with await self._client() as client:
            await self.limiter.wait(url)
            r = await client.get(url)
            if r.status_code >= 400:
                self.log.warning("blog fetch %s -> %s", url, r.status_code)
                return None
            html = r.text

        soup = BeautifulSoup(html, "lxml")
        # HubSpot blog templates put the real title in og:title / the
        # h2.blog-post__title; the on-page <h1> is often a sub-headline, so
        # prefer the metadata title and fall back to the descriptor.
        og_title = soup.find("meta", attrs={"property": "og:title"})
        title_el = soup.select_one("h2.blog-post__title") or soup.find("h1") or soup.find("h2")
        title = (og_title["content"].strip() if og_title and og_title.get("content")
                 else (title_el.get_text(strip=True) if title_el else "")
                 or d.get("title") or d["external_id"])

        published_at = d.get("published_at")
        if not published_at:
            published_at = _parse_blog_date(soup.get_text(" ", strip=True))

        # HubSpot wraps the article body in div.custom-post-body-content; that
        # container holds only the post text (share buttons / CTA / disclaimer
        # / "Recent Posts" live in siblings). Fall back to broader selectors if
        # the template differs, then strip residual chrome.
        body_el = (soup.select_one("div.custom-post-body-content")
                   or soup.select_one(".blog-post__body")
                   or soup.select_one("article")
                   or soup.select_one(".post-body")
                   or soup.select_one(".post-content")
                   or soup.select_one("main")
                   or soup.select_one("#content"))
        if body_el:
            for junk in body_el.select(
                "nav, footer, aside, .related, .recent-posts, .navigation, "
                ".share, .social, .hs-cta, .hs_cos_type_cta, script, style"
            ):
                junk.decompose()
            body_md = md_of(str(body_el), heading_style="ATX").strip()
        else:
            body_md = title

        # Drop a duplicate leading title heading.
        lines = body_md.splitlines()
        if lines and lines[0].startswith("#"):
            if lines[0].lstrip("# ").strip() == title:
                body_md = "\n".join(lines[1:]).strip()

        # Strip HubSpot chrome that shares the post body with no distinguishing
        # markup: the "download our letter" CTA line + the SEC compliance
        # disclaimer block. These are fixed boilerplate on every post.
        body_md = _strip_blog_chrome(body_md)

        date_part = published_at.strftime("%Y-%m-%d") if published_at else "undated"
        folder_name = f"{date_part}-{slugify(d['external_id'], 80)}"

        return ScrapedItem(
            source=self.effective_source_code,
            channel="gorozen",
            channel_name="Goehring & Rozencwajg",
            external_id=d["external_id"],
            title=title,
            url=url,
            published_at=published_at,
            body_md=body_md,
            raw_html=html,
            language="en",
            flat_layout=True,
            folder_name=folder_name,
            extra={"source_type": "blog"},
        )

    async def _fetch_commentary(self, d: dict) -> ScrapedItem | None:
        """Download a form-gated commentary PDF via the HubSpot public form API.

        The commentary page embeds a HubSpot form whose fields refuse to render
        under automated browsers (bot detection). Instead we POST directly to
        HubSpot's public submission endpoint with the field values, which either
        returns a ``redirectUri`` (newer forms → thank-you page hosting the PDF
        link) or an ``inlineMessage`` (older forms → HTML with the PDF link
        embedded). In both cases the PDF itself is then publicly fetchable, so
        we download it, save it under ``data/raw/blog/gorozen/<year>/``, and
        extract its text with pypdf. Falls back to the page teaser if the form
        or PDF is unreachable.
        """
        import json as _json

        url = d["url"]
        # Fetch the commentary page to harvest the per-commentary formId +
        # portalId (each commentary has its own form) and the teaser content.
        await self.limiter.wait(url)
        async with await self._client() as client:
            r = await client.get(url)
            if r.status_code >= 400:
                self.log.warning("commentary fetch %s -> %s", url, r.status_code)
                return None
            page_html = r.text

        m_portal = re.search(r'portalId["\':\s]+["\']?(\d+)', page_html)
        m_form = re.search(r'formId["\':\s]+["\']([0-9a-f-]+)', page_html)
        if not (m_portal and m_form):
            self.log.warning("could not find HubSpot portal/form id for %s", url)
            return None
        portal_id, form_id = m_portal.group(1), m_form.group(1)

        # Submit the form. Required fields vary by form version: newer forms
        # want categorize_yourself; older ones also want what_type_of_firm…
        # We supply all known fields and iteratively add any further required
        # field the API complains about (discovered via its error messages).
        pdf_url = await self._submit_hs_form(portal_id, form_id, url)

        # Resolve the actual PDF link from the submission response.
        pdf_bytes: bytes | None = None
        if pdf_url:
            pdf_bytes = await self._download_pdf(pdf_url)

        # Parse page title/date from the commentary HTML for metadata.
        soup = BeautifulSoup(page_html, "lxml")
        page_title = d.get("title") or d["external_id"]
        h1 = soup.find("h1") or soup.find("h2")
        if h1:
            page_title = h1.get_text(strip=True) or page_title
        published_at = d.get("published_at") or _parse_commentary_date(
            soup.get_text(" ", strip=True))
        # Fallback: derive a date from the quarter/year encoded in the slug
        # (e.g. 2025-q4 → 2025-12-31, 1q2024 → 2024-03-31). Older commentary
        # pages sometimes don't expose a parseable date in their HTML.
        if not published_at:
            published_at = _date_from_slug(d["external_id"])

        # Save PDF + extract text.
        slides_path = None
        body_md = ""
        date_part = published_at.strftime("%Y-%m-%d") if published_at else "undated"
        year_part = published_at.strftime("%Y") if published_at else "undated"
        stem = f"{date_part}-{slugify(d['external_id'], 80)}"
        raw_dir = DATA_DIR / "raw" / "blog" / "gorozen" / year_part
        raw_dir.mkdir(parents=True, exist_ok=True)

        if pdf_bytes:
            pdf_path = raw_dir / f"{stem}.commentary.pdf"
            pdf_path.write_bytes(pdf_bytes)
            slides_path = str(pdf_path)
            try:
                import pypdf
                reader = pypdf.PdfReader(pdf_path)
                body_md = "\n\n".join(
                    (p.extract_text() or "") for p in reader.pages).strip()
            except Exception as exc:  # noqa: BLE001
                self.log.warning("pypdf extract failed for %s: %s", url, exc)
                body_md = ""
        else:
            self.log.warning("no commentary PDF captured for %s; using page teaser", url)

        # Fallback: build a body from the page teaser if PDF text is empty.
        if not body_md.strip():
            teaser_el = (soup.select_one("article") or soup.select_one("main")
                         or soup.select_one(".w-richtext") or soup.body)
            if teaser_el:
                for junk in teaser_el.select(
                    "nav, footer, aside, script, style, .hs-form, form"
                ):
                    junk.decompose()
                body_md = md_of(str(teaser_el), heading_style="ATX").strip()

        return ScrapedItem(
            source=self.effective_source_code,
            channel="gorozen",
            channel_name="Goehring & Rozencwajg",
            external_id=d["external_id"],
            title=page_title,
            url=url,
            published_at=published_at,
            body_md=body_md or f"# {page_title}\n\n_(commentary PDF could not be extracted)_",
            raw_html=page_html,
            slides_path=slides_path,
            language="en",
            flat_layout=True,
            folder_name=stem,
            extra={"source_type": "commentary", "pdf_url": pdf_url},
        )

    async def _submit_hs_form(self, portal_id: str, form_id: str, page_url: str) -> str | None:
        """Submit the HubSpot form via its public API and return the revealed
        commentary PDF URL.

        Handles both response shapes: ``redirectUri`` (→ thank-you page hosting
        the PDF link) and ``inlineMessage`` (→ HTML with the PDF link). Required
        fields differ by form version, so we iteratively discover them from the
        API's ``REQUIRED_FIELD`` error messages.
        """
        api_url = (f"https://api.hsforms.com/submissions/v3/integration/submit/"
                   f"{portal_id}/{form_id}")
        # Known field values across form versions; extra required fields are
        # discovered below and filled with a generic default.
        fields: dict[str, str] = {
            "firstname": GOROZEN_FIRST,
            "lastname": GOROZEN_LAST,
            "email": GOROZEN_EMAIL,
            "categorize_yourself": GOROZEN_CATEGORY,
            "what_type_of_firm_do_you_work_for_": "Other",
        }
        await self.limiter.wait(api_url)
        async with await self._client() as client:
            for _ in range(8):
                payload = {
                    "fields": [{"name": k, "value": v} for k, v in fields.items()],
                    "context": {"pageUri": page_url, "pageName": "gorozen commentary"},
                }
                resp = await client.post(api_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    # Newer forms: redirect to a thank-you page.
                    redirect = data.get("redirectUri")
                    if redirect:
                        self.log.info("form submitted -> thank-you page %s", redirect)
                        return await self._pdf_from_thank_you(redirect)
                    # Older forms: inline HTML message with the PDF link.
                    inline = data.get("inlineMessage")
                    if inline:
                        self.log.info("form submitted -> inline PDF link")
                        href = self._first_pdf_link(inline)
                        return href
                    self.log.warning("form submitted but no redirect/message: %s",
                                     str(data)[:200])
                    return None
                # On error, discover any newly-required field and retry.
                try:
                    errs = resp.json().get("errors", [])
                except Exception:  # noqa: BLE001
                    errs = []
                missing = [
                    e["message"].split("'")[1] for e in errs
                    if e.get("errorType") == "REQUIRED_FIELD" and "'" in e.get("message", "")
                ]
                added = False
                for name in missing:
                    if name not in fields:
                        fields[name] = "Other"
                        added = True
                if not added:
                    self.log.warning("form submit stuck for %s: %s",
                                     page_url, str(errs)[:200])
                    return None
        return None

    async def _pdf_from_thank_you(self, thank_you_url: str) -> str | None:
        """Fetch the thank-you page and return the first non-compliance PDF link."""
        if not _is_safe_url(thank_you_url):
            return None
        await self.limiter.wait(thank_you_url)
        async with await self._client() as client:
            r = await client.get(thank_you_url)
            if r.status_code >= 400:
                self.log.warning("thank-you page %s -> %s", thank_you_url, r.status_code)
                return None
            return self._first_pdf_link(r.text)

    @staticmethod
    def _first_pdf_link(html: str) -> str | None:
        """Extract the first non-compliance ``.pdf`` href from an HTML string."""
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = str(a["href"]).split("?")[0]
            if href.lower().endswith(".pdf") and "compliance" not in href.lower():
                if _is_safe_url(href):
                    return href
        return None

    async def _download_pdf(self, pdf_url: str) -> bytes | None:
        """Download a PDF, verifying it is a real PDF by its magic bytes."""
        if not _is_safe_url(pdf_url):
            return None
        await self.limiter.wait(pdf_url)
        async with await self._client() as client:
            r = await client.get(pdf_url)
            if r.status_code >= 400:
                self.log.warning("PDF download %s -> %s", pdf_url, r.status_code)
                return None
            body = r.content
            if body[:4] == b"%PDF":
                return body
            self.log.warning("PDF at %s is not a valid PDF (magic %s)",
                             pdf_url, body[:4])
            return None

    async def fetch(self, d: dict) -> ScrapedItem | None:
        st = d.get("source_type", "blog")
        if st == "commentary":
            return await self._fetch_commentary(d)
        return await self._fetch_blog(d)

    # ------------------------------------------------------------------
    # run (thread source_type through _recording_discover, like madxcap)
    # ------------------------------------------------------------------
    async def run(
        self,
        limit: int | None = None,
        *,
        source_type: str | None = None,
    ) -> list[Path]:
        out: list[Path] = []
        async for d in self._recording_discover(limit=limit, source_type=source_type):
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
