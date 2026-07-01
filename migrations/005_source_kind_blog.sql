-- Re-classify one-off, homepage-discovery website scrapers under a 'blog'
-- source kind, matching madxcap's existing classification. These scrapers
-- (macrovoices, madxcap) have no per-author crawl/catalog state, unlike the
-- resumable multi-author crawlers (hkej, yahoohk, master-insight), which
-- remain 'newspaper'.
--
-- NOTE: `kb db migrate` replays the full, idempotent `docker/postgres/init.sql`
-- rather than this file, so init.sql is the actual source of truth. This file
-- is kept for a standalone, minimal-diff apply against an existing database,
-- matching the existing migrations/ convention.

UPDATE source SET kind='blog' WHERE code='macrovoices' AND kind<>'blog';
