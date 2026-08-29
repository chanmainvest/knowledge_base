# Spec — Blog scrapers (MacroVoices, MadX, Gorozen, Blogspot)

Read this when touching `src/kb/scrapers/macrovoices.py`, `madxcap.py`,
`gorozen.py`, `blogspot.py` (and its deprecated shim `greenhorn.py`), the
`blog` source kind, or adding a new blog-style source.

- **Multi-page articles (Joomla pagebreak).** Some sources split a single
  article across several pages via Joomla's pagebreak plugin (an "Article
  Index" TOC of `?start=N` links). The bare URL returns only page 1 + the
  TOC, so a naive fetch saves nav chrome instead of the body. `?showall=1`
  collapses the whole article onto one page — MacroVoices' `fetch()` uses
  it, strips the pagination nav (`_strip_pagination_nav`) before
  markdownify, and falls back to walking `?start=1..N` if `showall` ever
  fails to concatenate. When adding a Joomla-ish source, check for an
  Article Index and prefer `?showall=1` over per-page walking.
- **MacroVoices two-index discovery (cross-index dedup gotcha).** The site
  has two episode indices whose coverage differs, and they assign **different
  article IDs to the same episode** (e.g. Lyn Alden ep538 = article 1538 on
  the legacy index, 1537 on the live one). Discovery walks the **live**
  `/podcasts-collection/macrovoices-podcasts` index (20/page, item-offset
  `?start=N`, ~29 pages, spans the whole archive back to 2016); the legacy
  `/podcast-transcripts` index is **frozen at episode 1538 (25 June 2026)**
  and no longer receives new episodes, so it cannot be the discovery source.
  `already_scraped()` must dedup by **episode slug text** (guest+title), not
  article id — it uses a 24-char slug prefix plus a ≥3-guest-token overlap
  fallback to survive diacritic transliteration (`stöferle`→`stoeferle` vs
  `sto-ferle`) and guest-name reordering. Episode links are root-level
  `/<artid>-macrovoices-<NNN>-<slug>`; the `<NNN>` episode number is the
  stable cross-index key and is encoded in the canonical `external_id`
  (`mv-<NNN>-<slug>`).
- **MacroVoices transcript source differs by page format.** Post-redesign
  episode pages (`/<id>-macrovoices-<NNN>-…`) carry **no inline transcript** —
  the body is a "Download the podcast transcript [Click Here]" link to a PDF
  at `/guest-content/list-guest-transcripts/<id>/file`; `fetch()` downloads
  that PDF and text-extracts it with pypdf. Old-format pages
  (`/podcast-transcripts/<id>`) still serve inline text via `?showall=1`.
  `fetch()` branches on URL shape (`_is_new_format`). The live index also
  carries a few dangling links that resolve to the site 404 or homepage
  chrome (title `404Error` / `Welcome to Macro Voices` with no transcript) —
  these are detected and skipped. New-format page titles live in `<h2>` (the
  `<h1>` is the generic site banner), so title extraction prefers `<h2>`.
- **Gorozen (Goehring & Rozencwajg) two-stream scraper.** `gorozen.py`
  scrapes two content streams under one `blog` source, selected by the shared
  `--source-type` flag (same mechanism madxcap uses for dcard/facebook):
  - `blog` — static HTML at `blog.gorozen.com/blog`, paginated
    `/blog/page/N` (page 1 = `/blog`; the bare host root 404s). `discover()`
    increments `N` until a page 404s or yields no new posts. Posts are plain
    server-rendered HTML, so httpx + BeautifulSoup suffices (no JS). The
    article body lives in `div.custom-post-body-content`; the fixed "Want to
    learn more…" CTA line and SEC disclaimer are stripped by text pattern
    (`_strip_blog_chrome`) since they share the body with no markup. Title
    comes from og:title (the on-page `<h1>` is often a sub-headline).
  - `commentary` — quarterly commentaries at
    `gorozen.com/commentaries/<slug>` whose PDF is **form-gated** behind a
    HubSpot embedded form. The form refuses to render its fields under
    automated browsers (Playwright/Camoufox — bot detection), so `fetch()`
    submits it directly via HubSpot's **public form API** instead: it reads
    the per-commentary `portalId`/`formId` (each commentary has its own form)
    from the page's embed config, POSTs to
    `api.hsforms.com/submissions/v3/integration/submit/<portal>/<form>` with
    firstname/lastname/email/category (hard-coded non-secret values at the top
    of `gorozen.py`), and the response either carries a `redirectUri` (→ a
    thank-you page hosting the PDF link) or an `inlineMessage` (→ HTML with the
    PDF link). Required fields differ by form version, so `_submit_hs_form`
    iteratively discovers them from the API's `REQUIRED_FIELD` errors. The PDF
    itself is publicly fetchable once revealed; it's saved to
    `data/raw/blog/gorozen/<year>/` and its text extracted with `pypdf`.
    Commentary slugs come in two shapes: newer `YYYY-q#` (`2026-q1`) and older
    `#qYYYY` (`2q2024`) — `_COMMENTARY_SLUG` matches both. If the form/PDF
    can't be reached the scraper degrades gracefully to the page teaser.
- **Blogspot / Blogger platform scraper.** `blogspot.py` is the generic
  Google Blogger scraper; each Blogspot-hosted site is a *channel* under
  the shared `blog` source (same shape as madxcap/gorozen). The registry
  `BLOGSPOT_SITES` holds known handles — `greenhorn` →
  `https://greenhornfinancefootnote.blogspot.com/` (綠角財經筆記) is the
  first entry. Add a new Blogspot blog by extending the dict (handle,
  base URL, display name, lang). Discovery can also scrape an **arbitrary**
  custom Blogspot site without code changes via
  `--source-type https://<name>.blogspot.com/` — the handle is derived
  from the hostname. Static HTML — no JS; `httpx` + `BeautifulSoup`
  suffices. Discovery walks Blogger's `search?updated-max=<ISO>&max-results=N`
  pagination per site: the homepage (`/`) shows 3 posts and an
  `a.blog-pager-older-link` (`id=Blog1_blog-pager-older-link`) to the next
  page; subsequent pages honour `max-results=25` for efficiency. Post URLs
  are `/YYYY/MM/slug.html`; `external_id` is the path without `.html`
  (`YYYY/MM/slug`). Article date is `span.date-header` (`YYYY年M月D日`).
  Body extraction uses a regex fallback
  (`<div class="post-body">…</div> → <div class="post-footer"`) because the
  full-document `lxml` parse sees an empty `div.post-body` (Blogger template
  quirk); the fragment is then `markdownify`'d. Feeds
  (`/feeds/posts/default`) currently redirect through follow.it and are not
  used. `already_scraped()` checks `data/blog/<handle>/<YYYY>/` stems for
  the URL slug. `code="blogspot"` is the canonical scraper; `code="greenhorn"`
  (class `GreenhornScraper` in `blogspot.py`, re-exported by the deprecated
  shim `greenhorn.py`) remains as an alias so `kb blog scrape greenhorn`
  and `SCRAPERS["greenhorn"]` keep working.
- `source.kind` groups sources by scraping/discovery shape, and drives `kb
  scrape list --kind` and the Search page's source list: `blog` = one-off,
  homepage-discovery scrapers with no per-author crawl/catalog state
  (macrovoices, madxcap/狂徒, gorozen are channels under a single `blog` source — each
  site keeps its own scraper class but they share the `blog` source code and
  `source_code = "blog"` on the scraper); `newspaper` = resumable multi-author
  crawlers with their own catalog tables (hkej, yahoohk, master-insight);
  `youtube` and `membership` (patreon, substack) are their own kinds.
- Blog scrapers set `source_code = "blog"` on the scraper class; the
  `effective_source_code` property returns the DB source code to write to
  markdown front-matter. The registry still keys by the unique scraper `code`
  (e.g. `macrovoices`, `madxcap`). Use `kb blog scrape <site>` to scrape a
  specific blog, or the generic `kb scrape run <code>`.
