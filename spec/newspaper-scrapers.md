# Spec — Newspaper scrapers (HKEJ, Yahoo HK, Master Insight)

Read this when touching `src/kb/scrapers/hkej.py`, `yahoohk.py`,
`master_insight.py`, `businessfocus.py`, or the Camoufox browser container
(`docker/camoufox/`).

- **HKEJ browser + session model.** HKEJ is behind Cloudflare, so it drives a
  real anti-detect browser (Camoufox). Two launch modes via
  `HKEJ_BROWSER_MODE`: `docker` (default) runs Camoufox in a container exposing
  a Playwright WS endpoint (`ws://127.0.0.1:9222/hkej`); the scraper connects
  over WS, restores login cookies from `data/hkej/.browser_state.json`
  (Playwright `storage_state`, so you log in once — same model as the
  substack/patreon `.session.json` cookie files), scrapes, and disconnects.
  `local` falls back to an on-host Camoufox kept warm by the daemon
  (`kb hkej browser start`). Docker mode bypasses the local daemon entirely
  (the container *is* the persistent browser). Build/run the container:
  `docker compose build camoufox` then `kb hkej docker up`. The container's
  noVNC web UI (`http://localhost:7900`) lets a human solve interactive
  Cloudflare challenges / log in. Login is auto-filled from `HKEJ_USER`/
  `HKEJ_PASS` (`HKEJ_LOGIN_MODE=auto`, default); set `manual` to force a
  human wait. Override per-run with `--browser-mode`/`--login` on
  `kb hkej scrape-author`. See `hkej.py:_docker_session` /
  `_browser_context` for the dispatch.

## Yahoo HK columnist notes

- Authors are discovered from the contributors index; channels are auto-upserted on
  first scrape. No manual author registration step.
- The feed often labels items `雅虎香港財經`; `yahoohk.py` takes the article
  headline from the page/body (second `#` heading when the first is generic) and
  strips columnist chrome before saving.
- Older files saved with the generic filename stem can be repaired with
  `uv run python scripts/fix_yahoohk_titles.py`.

## BusinessFocus notes

- **Multi-author site, DB-managed author list** (`kb businessfocus add-author
  <slug>` / `list-authors` / `rm-author`; `shing`/龔成 is seeded in
  `init.sql`). Authors live in the `channel` table like master-insight — no
  contributors index to auto-discover from.
- **Index via the site's own backend API, not HTML.** The author page
  (/author/<slug>) is a Nuxt SSR app; its 查看更多 button pages through
  `node_api`: resolve the slug with
  `GET /node_api/v1/authors/blogger/<slug>?pageId=<PAGE_ID>` (numeric
  `blogger_id` — note the articles list is keyed by that id, NOT the slug),
  then list newest-first with
  `GET /node_api/v1/articles/blogger/<blogger_id>?pageId=<PAGE_ID>&offset=N&limit=12`
  until a page comes back empty. The numeric id is pinned into
  `channel.metadata['blogger_id']` on first resolve.
- **`pageId` is site config**, not per-page: it is the Facebook page id from
  the site's `fb:pages` meta tag (constant `PAGE_ID` in `businessfocus.py`).
  The API 400s with a list of acceptable values if it's missing/wrong.
- **Early-stop on cached pages.** The index is newest-first, so discovery
  stops paging once a whole 12-item page is already on disk
  (`already_scraped` uses a per-author cached set of front-matter
  `external_id`s). Nightly runs cost one index page, not 27.
- **Article body from the SSR HTML**: `.pl-main-article__title` (headline),
  JSON-LD `NewsArticle` (`datePublished`, fallback headline),
  `.pl-main-article__main` markdownified, with ad slots decomposed and
  whitespace-only lines (div indentation artifacts) collapsed.
  `external_id` is the WordPress post id from the URL (`/article/<id>/…`).
