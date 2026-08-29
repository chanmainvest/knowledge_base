# Spec — Scrape → ingest pipeline mechanics (data layout, progress, catalogs)

Read this when touching `src/kb/ingest.py`, `src/kb/progress.py`,
`src/kb/catalog.py`, `src/kb/io_md.py`, `src/kb/scrapers/base.py`, or the
data directory layout.

- The data directory is configurable via `DATA_DIR` in `.env` (or the
  `DATA_DIR` env var). Relative paths resolve against the repo root; absolute
  paths are used as-is. `kb.config.DATA_DIR` is a module-level `Path` computed
  at import time from the setting, so all `from ..config import DATA_DIR`
  sites pick up the configured value automatically. Changing `DATA_DIR`
  requires re-running `kb ingest` to refresh `item.md_path` in the database.
  `item.md_path` is stored **repo-root-relative** (`data/<source>/...`,
  forward slashes) by `_stored_md_path()` in `ingest.py` — never absolute.
  Absolute paths flip-flop between the host (`B:\...`) and the Jenkins
  container (`/app/data/...`) depending on which context ingested last, and
  the other side's file checks silently fail (2026-08-26: the transcript
  backfill found "0 candidates" for exactly this reason after the first
  successful container ingest since 2026-08-15). `data/...` resolves from
  both — host scripts run from the repo root, and in the kb container
  DATA_DIR (/app/data) hangs off WORKDIR /app.
- Scrape `--limit` is source-unit scoped where implemented, not necessarily a
  global output cap. For YouTube, `kb youtube scrape --limit N` inspects up to
  N videos per registered channel and does not stop after N total new files.
- **Flat-file layout** (blog, hkej, yahoohk, youtube, substack): content lives at
  `data/<source>/[<channel>/]<YYYY>/<YYYY-MM-DD>-<title>.md`; raw HTML at
  `data/raw/<source>/[<channel>/]<YYYY>/<YYYY-MM-DD>-<title>.html`.
  Set `flat_layout=True` on `ScrapedItem` to use this layout; `BaseScraper.write_md()`
  handles both old (patreon) and new layouts automatically.
- **Pipeline progress tracking.** The scrape → ingest → extract pipeline
  records its progress in two places: per-item stage timestamps
  (`item.ingested_at`, `item.extracted_at`) and a per-source rollup table
  `source_progress` (one row per source: `n_downloaded`, `n_ingested`,
  `n_extracted`, `n_extract_pending`, `n_extract_error`, plus last-run
  timestamps). The boundary functions in `src/kb/progress.py`
  (`mark_downloaded` / `mark_ingested` / `mark_extracted`) are called from
  `scrapers.base.write_md`, `ingest.ingest_file`, and the extract success /
  error paths respectively, so all sources are tracked from one instrumented
  boundary each. `recompute()` does an authoritative full recount from the
  `item` table and is called at the end of every `kb extract run` batch and
  on demand via `kb progress recompute` (which also re-derives
  `n_downloaded` from the hkej/patreon catalog tables). `n_downloaded` is
  best-effort for the filesystem-discovery sources (no historical catalog to
  reconstruct from) — it accrues from when the feature shipped forward.
- **Discovery catalog.** Every item a filesystem-discovery scraper sees during
  `discover()` is upserted into a generic `discovery_catalog` table (one row
  per `(source_id, external_id)`, with a `downloaded` flag and the full
  original discovery `descriptor` stored as JSONB) via the
  `_recording_discover()` wrapper in `scrapers/base.py`. `write_md()` flips
  the row to `downloaded=true`. So "discovered but not downloaded" (a scrape
  that died halfway) is queryable, and `kb scrape resume <code>` re-attempts
  just those items without re-discovering the whole source. hkej and patreon
  keep their richer native catalogs (`hkej_article_catalog` /
  `patreon_post_catalog`) with run/page fingerprinting + resume cursors and do
  NOT use the generic table; their pending counts are unioned in at read time
  by `catalog.pending_counts()`. `src/kb/catalog.py` is the module.
