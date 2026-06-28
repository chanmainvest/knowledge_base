# Scrape Utility Scripts

This project stores source content as Markdown first, then ingests the Markdown into Postgres. Scrapers and utility scripts are designed to be re-runnable: existing non-empty Markdown files are skipped or updated through explicit backfill commands.

## Main Scrape Commands

Source-specific commands are preferred where they exist:

```pwsh
uv run kb youtube list-channels
uv run kb youtube add-channel @SomeChannel "Some Channel"
uv run kb youtube scrape --limit 5

uv run kb hkej list-authors
uv run kb hkej add-author "高天佑"
uv run kb hkej scrape-author "高天佑" --limit 5

uv run kb patreon check-session
uv run kb patreon list-creators
uv run kb patreon scrape <creator> --limit 3
```

List available scrapers:

```pwsh
uv run kb scrape list
```

List registered channels for a source:

```pwsh
uv run kb youtube list-channels
uv run kb hkej list-authors
uv run kb patreon list-creators
```

The generic form also works for any channel-based source:

```pwsh
uv run kb scrape list-channels <source>
```

Run a source scraper:

```pwsh
uv run kb youtube scrape --limit 5
uv run kb scrape run macrovoices --limit 3
uv run kb scrape run hkej --limit 20
```

For YouTube, `--limit 5` inspects up to 5 videos per registered channel. Cached videos are skipped, and the scraper does not stop after 5 new files total.

For HKEJ, `uv run kb scrape run hkej --limit 20` applies the limit per registered author. With three HKEJ authors registered, the command attempts up to 20 new articles for each author.

Run all registered scrapers with a per-source limit:

```pwsh
uv run kb scrape all --limit 5
```

## HKEJ Scraping

HKEJ uses a persistent Camoufox browser because Cloudflare and subscriber login state are browser-bound.

Start and check the browser daemon:

```pwsh
uv run kb hkej browser start --login-wait-minutes 15
uv run kb hkej browser status
```

Prime search/login manually if needed:

```pwsh
uv run kb hkej prime "李聲揚" --login-wait-minutes 15
```

The author name in `prime` only opens a real HKEJ search page so Cloudflare can clear. It does not restrict later scraping.

Register authors:

```pwsh
uv run kb hkej add-author "李聲揚"
uv run kb hkej add-author "何啟聰"
uv run kb hkej add-author "高天佑"
```

Scrape one author:

```pwsh
uv run kb hkej scrape-author "高天佑" --limit 1
uv run kb hkej scrape-author "高天佑"
```

The HKEJ scraper records search-page discovery in Postgres before downloads. If the machine stops mid-run, rerun the same command. The next run crawls page 1 first, compares the current search total and page-1 fingerprint, and only reuses old page snapshots when the result set has not shifted.

## Patreon Utilities

Patreon uses a saved `session_id` cookie, optionally refreshed from the
persistent browser daemon. Run `check-session` first when diagnosing access
problems; it should report the logged-in Patreon account without printing the
cookie value.

```pwsh
uv run kb patreon check-session
uv run kb patreon list-creators
uv run kb patreon list-creators --all
uv run kb patreon resolve <creator>
uv run kb patreon list-years <creator>
uv run kb patreon status <creator>
uv run kb patreon scrape <creator> --limit 3
```

Browser daemon helpers:

```pwsh
uv run kb patreon browser start
uv run kb patreon browser login --wait-minutes 10
uv run kb patreon browser status
uv run kb patreon browser stop
```

For scheduled creator scraping, keep the browser daemon logged in and run:

```pwsh
uv run kb patreon scrape-creator --limit 20
uv run kb patreon scrape-creator <creator> --limit 20
uv run kb patreon scrape-creator --no-download
```

The Patreon scraper keeps a DB crawl catalog for each creator, then downloads
pending posts newest-first. A scrape can be re-run safely: already-downloaded
Markdown files are skipped, and the catalog records downloaded/pending state.
`list-creators` shows creators already in that scrape catalog; add `--all` to
include registered creator rows that have not produced catalog entries yet.
Post discovery uses Patreon's JSON API. Downloads first use the post-detail API;
if Patreon returns an empty `content` field, the scraper renders the post page
with Playwright and extracts visible rich text from `.patreon-post-content`.
This matters for posts whose `post_type` is `image_file` but which still contain
article text.

## Scripts Folder

The `scripts/` directory contains maintenance and debugging helpers:

- `migrate_data_layout.py`: migrates older source folders to the current flat Markdown/raw layout. Safe to re-run.
- `scrape_patreon.ps1`: Windows Task Scheduler wrapper for `uv run kb patreon scrape-creator`.
- `reextract_hkej.py`: re-runs extraction workflows for HKEJ content.
- `debug_hkej_search.py`: probes HKEJ search result HTML while debugging selectors or Cloudflare behavior.
- `probe_hkej_login.py`: checks HKEJ login/session behavior.
- `test_hkej_article.py`: focused article fetch/debug helper.
- `test_hkej_author.py`: focused author-page/search debug helper.

Run maintenance scripts through `uv` from the repository root:

```pwsh
uv run python scripts/migrate_data_layout.py
```

## Raw Output Layout

Current flat-file layout:

```text
data/hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.md
data/raw/hkej/<author>/<YYYY>/<YYYY-MM-DD>-<title>.html

data/macrovoices/<YYYY>/<YYYY-MM-DD>-<episode>-<title>.md
data/raw/macrovoices/<YYYY>/<YYYY-MM-DD>-<episode>-<title>.html

data/youtube/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.md

data/patreon/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.md
data/raw/patreon/<channel>/<YYYY>/<YYYY-MM-DD>-<title>.html
```

Markdown front matter is the canonical scrape metadata. Database rows can be regenerated from Markdown by running `uv run kb ingest`.