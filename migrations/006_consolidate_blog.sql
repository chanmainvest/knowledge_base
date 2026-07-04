-- Consolidate macrovoices + madxcap into a single `blog` source.
--
-- Previously macrovoices and madxcap were separate source rows (both with
-- kind='blog'). This migration merges them into one `blog` source, moving
-- their items and channels under it, then drops the old source rows.
--
-- Run AFTER running scripts/migrate_to_blog_source.py, which rewrites
-- `source:` front-matter in the markdown files from 'macrovoices'/'madxcap'
-- to 'blog' and moves the data folders to data/blog/<channel>/...
--
-- NOTE: `kb db migrate` replays the full, idempotent `docker/postgres/init.sql`
-- rather than this file, so init.sql is the actual source of truth. This file
-- is kept for a standalone, minimal-diff apply against an existing database,
-- matching the existing migrations/ convention.

-- 1. Create the consolidated blog source (no-op if it already exists).
INSERT INTO source(code, name, url, kind)
VALUES ('blog', 'Blogs', NULL, 'blog')
ON CONFLICT (code) DO NOTHING;

-- 2. Move macrovoices/madxcap channels to the blog source.
--    (handle conflicts are resolved by ON CONFLICT DO UPDATE.)
INSERT INTO channel(source_id, handle, name, url)
SELECT (SELECT id FROM source WHERE code='blog'),
       c.handle, c.name, c.url
FROM channel c
JOIN source s ON s.id = c.source_id
WHERE s.code IN ('macrovoices', 'madxcap')
ON CONFLICT (source_id, handle) DO UPDATE SET
    name = EXCLUDED.name, url = EXCLUDED.url;

-- 3. Re-point items to the blog source + new channel IDs.
--    a) macrovoices items: source -> blog, channel -> blog/macrovoices
UPDATE item i
SET source_id = (SELECT id FROM source WHERE code='blog'),
    channel_id = (
        SELECT c2.id FROM channel c2
        JOIN source s2 ON s2.id = c2.source_id
        WHERE s2.code = 'blog' AND c2.handle = 'macrovoices'
    )
FROM source old_s
WHERE i.source_id = old_s.id AND old_s.code = 'macrovoices';

--    b) madxcap items: resolve old channel handle, then match to blog channel.
UPDATE item i
SET source_id = (SELECT id FROM source WHERE code='blog'),
    channel_id = (
        SELECT c2.id FROM channel c2
        JOIN source s2 ON s2.id = c2.source_id
        JOIN channel old_c ON old_c.id = i.channel_id
        WHERE s2.code = 'blog' AND c2.handle = old_c.handle
    )
FROM source old_s
WHERE i.source_id = old_s.id AND old_s.code = 'madxcap';

-- 4. Drop old channels that belong to macrovoices/madxcap sources.
DELETE FROM channel
WHERE source_id IN (
    SELECT id FROM source WHERE code IN ('macrovoices', 'madxcap')
);

-- 5. Drop the old macrovoices/madxcap source rows (items/channels now moved).
DELETE FROM source WHERE code IN ('macrovoices', 'madxcap');