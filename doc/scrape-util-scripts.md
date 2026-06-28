# Scrape Utility Scripts

This project stores source content as Markdown first, then ingests the Markdown into Postgres. Scrapers and utility scripts are designed to be re-runnable: existing non-empty Markdown files are skipped or updated through explicit backfill commands.

## Main Scrape Commands

List available scrapers:

```pwsh
uv run kb scrape list
```

List registered channels for a source:

```pwsh
uv run kb scrape list-channels youtube
uv run kb hkej list-authors
```

Run a source scraper:

```pwsh
uv run kb scrape run youtube --limit 5
uv run kb scrape run macrovoices --limit 3
uv run kb scrape run hkej --limit 20
```

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
uv run kb hkej prime-search "李聲揚"
uv run kb hkej prime-login --wait-minutes 15
uv run kb hkej prime "李聲揚" --login-wait-minutes 15
```

Register authors:

```pwsh
uv run kb hkej add-author "李聲揚"
uv run kb hkej add-author 18839
```

Scrape one author:

```pwsh
uv run kb hkej scrape-author "高天佑" --limit 1
uv run kb hkej scrape-author "高天佑"
```

The HKEJ scraper records search-page discovery in Postgres before downloads. If the machine stops mid-run, rerun the same command. The next run crawls page 1 first, compares the current search total and page-1 fingerprint, and only reuses old page snapshots when the result set has not shifted.

## Patreon Utilities

Patreon uses either a saved session cookie or browser-derived cookies.

```pwsh
uv run kb patreon check-session
uv run kb patreon list-subscriptions
uv run kb patreon resolve <creator>
uv run kb patreon list-years <creator>
uv run kb patreon scrape <creator> --limit 3
```

Browser daemon helpers:

```pwsh
uv run kb patreon browser start
uv run kb patreon browser login --wait-minutes 10
uv run kb patreon browser status
uv run kb patreon browser stop
```

## Scripts Folder

The `scripts/` directory contains maintenance and debugging helpers:

- `migrate_data_layout.py`: migrates older source folders to the current flat Markdown/raw layout. Safe to re-run.
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
data/patreon/<channel>/<YYYY-MM-DD>__<id>/content.md
```

Markdown front matter is the canonical scrape metadata. Database rows can be regenerated from Markdown by running `uv run kb ingest`.