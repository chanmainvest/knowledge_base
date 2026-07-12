-- Initial extensions and schema for KB.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Sources: blog, youtube, hkej, etc.
CREATE TABLE IF NOT EXISTS source (
    id          SERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,           -- 'blog' | 'youtube' | 'hkej' | 'patreon' …
    name        TEXT NOT NULL,
    url         TEXT,
    kind        TEXT NOT NULL                   -- 'blog' | 'youtube' | 'newspaper' | 'membership'
);

-- Channels / authors within a source.
CREATE TABLE IF NOT EXISTS channel (
    id          SERIAL PRIMARY KEY,
    source_id   INT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    handle      TEXT NOT NULL,                  -- '@Fedguy12' or HKEJ author slug
    name        TEXT NOT NULL,
    url         TEXT,
    metadata    JSONB DEFAULT '{}'::jsonb,
    UNIQUE (source_id, handle)
);

-- A scraped item (episode / video / article).
CREATE TABLE IF NOT EXISTS item (
    id              BIGSERIAL PRIMARY KEY,
    source_id       INT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    channel_id      INT REFERENCES channel(id) ON DELETE SET NULL,
    external_id     TEXT NOT NULL,              -- url, video id, episode id
    title           TEXT NOT NULL,
    url             TEXT,
    published_at    TIMESTAMPTZ,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    language        TEXT,
    duration_sec    INT,
    md_path         TEXT,                       -- path to markdown on disk
    raw_path        TEXT,                       -- raw html/transcript file
    slides_path     TEXT,                       -- pdf path if any
    content         TEXT,                       -- canonical markdown text
    summary         TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb,
    extraction_status TEXT NOT NULL DEFAULT 'pending', -- pending|done|error
    extraction_error  TEXT,
    primary_extraction_run_id BIGINT,              -- FK added below, after extraction_run exists
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS item_published_at_idx ON item(published_at DESC);
CREATE INDEX IF NOT EXISTS item_channel_idx     ON item(channel_id);
CREATE INDEX IF NOT EXISTS item_status_idx      ON item(extraction_status);

-- HKEJ search crawl catalog. This stores search-page discovery separately from
-- downloaded items so interrupted HKEJ runs can resume page discovery safely.
CREATE TABLE IF NOT EXISTS hkej_author_state (
    channel_id          INT PRIMARY KEY REFERENCES channel(id) ON DELETE CASCADE,
    search_total        INT,
    max_page            INT,
    catalog_count       INT NOT NULL DEFAULT 0,
    last_seen_at        TIMESTAMPTZ,
    last_full_crawl_at  TIMESTAMPTZ,
    metadata            JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS hkej_crawl_run (
    id                  BIGSERIAL PRIMARY KEY,
    channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'running',
    search_total        INT,
    max_page            INT,
    pages_crawled       INT NOT NULL DEFAULT 0,
    pages_reused        INT NOT NULL DEFAULT 0,
    metadata            JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS hkej_crawl_run_channel_idx ON hkej_crawl_run(channel_id, started_at DESC);

CREATE TABLE IF NOT EXISTS hkej_crawl_page (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES hkej_crawl_run(id) ON DELETE CASCADE,
    channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    page_num            INT NOT NULL,
    search_total        INT,
    max_page            INT,
    url                 TEXT NOT NULL,
    crawled_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    article_count       INT NOT NULL DEFAULT 0,
    article_ids         JSONB NOT NULL DEFAULT '[]'::jsonb,
    page_fingerprint    TEXT NOT NULL,
    UNIQUE (run_id, page_num)
);
CREATE INDEX IF NOT EXISTS hkej_crawl_page_resume_idx
    ON hkej_crawl_page(channel_id, search_total, max_page, page_num);

CREATE TABLE IF NOT EXISTS hkej_article_catalog (
    id                  BIGSERIAL PRIMARY KEY,
    channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    external_id         TEXT NOT NULL,
    published_at        TIMESTAMPTZ,
    title               TEXT NOT NULL,
    url                 TEXT NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_seen_run_id   BIGINT REFERENCES hkej_crawl_run(id) ON DELETE SET NULL,
    last_seen_run_id    BIGINT REFERENCES hkej_crawl_run(id) ON DELETE SET NULL,
    last_seen_page      INT,
    downloaded          BOOLEAN NOT NULL DEFAULT false,
    downloaded_at       TIMESTAMPTZ,
    md_path             TEXT,
    raw_path            TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    UNIQUE (channel_id, external_id)
);
CREATE INDEX IF NOT EXISTS hkej_article_catalog_channel_idx
    ON hkej_article_catalog(channel_id, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS hkej_article_catalog_downloaded_idx
    ON hkej_article_catalog(channel_id, downloaded);

-- Patreon crawl catalog. Patreon uses cursor pagination, so each crawl page
-- stores its resume cursor (next_url); interrupted crawls resume from the next
-- uncrawled page, and new posts (which shift page alignment) are detected via
-- the page-1 fingerprint.
CREATE TABLE IF NOT EXISTS patreon_creator_state (
    channel_id          INT PRIMARY KEY REFERENCES channel(id) ON DELETE CASCADE,
    campaign_id         TEXT,
    total_posts         INT,
    catalog_count       INT NOT NULL DEFAULT 0,
    last_seen_at        TIMESTAMPTZ,
    last_full_crawl_at  TIMESTAMPTZ,
    metadata            JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS patreon_crawl_run (
    id                  BIGSERIAL PRIMARY KEY,
    channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'running',
    total_posts         INT,
    pages_crawled       INT NOT NULL DEFAULT 0,
    pages_reused        INT NOT NULL DEFAULT 0,
    metadata            JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS patreon_crawl_run_channel_idx
    ON patreon_crawl_run(channel_id, started_at DESC);

CREATE TABLE IF NOT EXISTS patreon_crawl_page (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES patreon_crawl_run(id) ON DELETE CASCADE,
    channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    page_num            INT NOT NULL,
    total_posts         INT,
    url                 TEXT NOT NULL,
    next_url            TEXT,
    crawled_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    post_count          INT NOT NULL DEFAULT 0,
    post_ids            JSONB NOT NULL DEFAULT '[]'::jsonb,
    page_fingerprint    TEXT NOT NULL,
    UNIQUE (run_id, page_num)
);
CREATE INDEX IF NOT EXISTS patreon_crawl_page_resume_idx
    ON patreon_crawl_page(channel_id, total_posts, page_num);

CREATE TABLE IF NOT EXISTS patreon_post_catalog (
    id                  BIGSERIAL PRIMARY KEY,
    channel_id          INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    external_id         TEXT NOT NULL,
    published_at        TIMESTAMPTZ,
    year                INT,
    title               TEXT NOT NULL,
    url                 TEXT NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_seen_run_id   BIGINT REFERENCES patreon_crawl_run(id) ON DELETE SET NULL,
    last_seen_run_id    BIGINT REFERENCES patreon_crawl_run(id) ON DELETE SET NULL,
    last_seen_page      INT,
    downloaded          BOOLEAN NOT NULL DEFAULT false,
    downloaded_at       TIMESTAMPTZ,
    md_path             TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    UNIQUE (channel_id, external_id)
);
CREATE INDEX IF NOT EXISTS patreon_post_catalog_channel_idx
    ON patreon_post_catalog(channel_id, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS patreon_post_catalog_downloaded_idx
    ON patreon_post_catalog(channel_id, downloaded);
CREATE INDEX IF NOT EXISTS patreon_post_catalog_year_idx
    ON patreon_post_catalog(channel_id, year);

-- Full-text search on title + content.
ALTER TABLE item ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title,'')), 'A') ||
        setweight(to_tsvector('simple', coalesce(content,'')), 'B')
    ) STORED;
CREATE INDEX IF NOT EXISTS item_tsv_idx ON item USING gin(tsv);
CREATE INDEX IF NOT EXISTS item_title_trgm_idx ON item USING gin(title gin_trgm_ops);

-- Chunks for embeddings / passage search.
CREATE TABLE IF NOT EXISTS chunk (
    id          BIGSERIAL PRIMARY KEY,
    item_id     BIGINT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    idx         INT NOT NULL,
    text        TEXT NOT NULL,
    embedding   vector(1536),
    UNIQUE (item_id, idx)
);
CREATE INDEX IF NOT EXISTS chunk_item_idx ON chunk(item_id);
CREATE INDEX IF NOT EXISTS chunk_emb_idx ON chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- One row per (item, provider, model, prompt_version) extraction attempt. This
-- lets the same article be re-extracted with several LLM providers/models so
-- their outputs and downstream prediction scores can be cross-referenced for
-- accuracy, instead of each new extraction silently overwriting the last.
CREATE TABLE IF NOT EXISTS extraction_run (
    id              BIGSERIAL PRIMARY KEY,
    item_id         BIGINT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,              -- openai|github|anthropic|zai
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL DEFAULT 'v1', -- bump when SYSTEM/SCHEMA changes materially
    status          TEXT NOT NULL DEFAULT 'running', -- running|done|error
    error           TEXT,
    summary         TEXT,
    raw_response    JSONB,                      -- full aggregated JSON, for audit/debugging
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INT,
    UNIQUE (item_id, provider, model, prompt_version)
);
CREATE INDEX IF NOT EXISTS extraction_run_item_idx ON extraction_run(item_id);
CREATE INDEX IF NOT EXISTS extraction_run_provider_model_idx ON extraction_run(provider, model);

-- Point item at the extraction_run considered canonical (used by the API,
-- frontend, and per-channel leaderboard). Defaults to whichever provider/model
-- is configured as LLM_PROVIDER; other runs remain queryable via extraction_run.
ALTER TABLE item ADD COLUMN IF NOT EXISTS primary_extraction_run_id BIGINT
    REFERENCES extraction_run(id) ON DELETE SET NULL;

-- LLM-extracted structured records. Each row belongs to exactly one
-- extraction_run so multiple providers/models can coexist per item.
CREATE TABLE IF NOT EXISTS view_market (
    id          BIGSERIAL PRIMARY KEY,
    item_id     BIGINT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    speaker     TEXT,                           -- author or guest
    asset_class TEXT,                           -- equities|bonds|fx|commodities|crypto|macro
    region      TEXT,
    direction   TEXT,                           -- bullish|bearish|neutral
    horizon     TEXT,                           -- short|medium|long
    confidence  REAL,
    rationale   TEXT,
    quote       TEXT
);
CREATE INDEX IF NOT EXISTS view_market_item_idx ON view_market(item_id);

CREATE TABLE IF NOT EXISTS prediction (
    id            BIGSERIAL PRIMARY KEY,
    item_id       BIGINT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    speaker       TEXT,
    ticker        TEXT,                         -- e.g. AAPL, ES=F, GC=F, ^GSPC
    asset_name    TEXT,
    action        TEXT,                         -- buy|sell|short|hold|watch|long|cover
    target_price  NUMERIC,
    stop_price    NUMERIC,
    timeframe     TEXT,                         -- '3M' '1Y' etc
    direction     TEXT,                         -- up|down|flat
    quote         TEXT,
    made_at       TIMESTAMPTZ,                  -- copied from item.published_at
    -- scoring fields filled later
    price_at_call NUMERIC,
    price_at_eval NUMERIC,
    eval_at       TIMESTAMPTZ,
    score         REAL,                         -- -1..1
    metadata      JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS prediction_ticker_idx ON prediction(ticker);
CREATE INDEX IF NOT EXISTS prediction_speaker_idx ON prediction(speaker);
CREATE INDEX IF NOT EXISTS prediction_made_idx ON prediction(made_at DESC);

-- Tag existing extraction output tables with the run that produced them.
-- Added via ALTER (not baked into the CREATE TABLE above) so this applies
-- cleanly to databases that already have view_market/prediction tables.
ALTER TABLE view_market ADD COLUMN IF NOT EXISTS extraction_run_id BIGINT
    REFERENCES extraction_run(id) ON DELETE CASCADE;
ALTER TABLE prediction ADD COLUMN IF NOT EXISTS extraction_run_id BIGINT
    REFERENCES extraction_run(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS view_market_run_idx ON view_market(extraction_run_id);
CREATE INDEX IF NOT EXISTS prediction_run_idx ON prediction(extraction_run_id);

-- Back-fill a synthetic 'legacy' extraction_run for any rows written before
-- this migration existed, so extraction_run_id can be made NOT NULL below.
-- No-op on fresh installs / databases with no pre-existing rows.
DO $$
DECLARE
    r RECORD;
    new_run_id BIGINT;
BEGIN
    FOR r IN
        SELECT DISTINCT item_id FROM view_market WHERE extraction_run_id IS NULL
        UNION
        SELECT DISTINCT item_id FROM prediction WHERE extraction_run_id IS NULL
    LOOP
        INSERT INTO extraction_run (item_id, provider, model, prompt_version, status)
        VALUES (r.item_id, 'unknown', 'legacy-pre-versioning', 'v1', 'done')
        ON CONFLICT (item_id, provider, model, prompt_version)
            DO UPDATE SET provider = EXCLUDED.provider
        RETURNING id INTO new_run_id;

        UPDATE view_market SET extraction_run_id = new_run_id
            WHERE item_id = r.item_id AND extraction_run_id IS NULL;
        UPDATE prediction SET extraction_run_id = new_run_id
            WHERE item_id = r.item_id AND extraction_run_id IS NULL;
        UPDATE item SET primary_extraction_run_id = new_run_id
            WHERE id = r.item_id AND primary_extraction_run_id IS NULL;
    END LOOP;
END $$;

ALTER TABLE view_market ALTER COLUMN extraction_run_id SET NOT NULL;
ALTER TABLE prediction ALTER COLUMN extraction_run_id SET NOT NULL;

CREATE TABLE IF NOT EXISTS entity (                -- people, companies, countries
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,                     -- person|company|country|theme
    name        TEXT NOT NULL,
    ticker      TEXT,
    metadata    JSONB DEFAULT '{}'::jsonb,
    UNIQUE (kind, name)
);
CREATE INDEX IF NOT EXISTS entity_ticker_idx ON entity(ticker);

CREATE TABLE IF NOT EXISTS item_entity (           -- many-to-many graph edges
    item_id     BIGINT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    entity_id   BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    weight      REAL DEFAULT 1.0,
    PRIMARY KEY (item_id, entity_id)
);

CREATE TABLE IF NOT EXISTS item_link (             -- "see also" between items
    a_id        BIGINT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    b_id        BIGINT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    similarity  REAL NOT NULL,
    PRIMARY KEY (a_id, b_id)
);

-- Leaderboard rollup (per channel per week).
CREATE TABLE IF NOT EXISTS leaderboard_weekly (
    channel_id  INT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    week_start  DATE NOT NULL,
    n_calls     INT NOT NULL DEFAULT 0,
    n_scored    INT NOT NULL DEFAULT 0,
    avg_score   REAL,
    hit_rate    REAL,
    PRIMARY KEY (channel_id, week_start)
);

-- Cross-model accuracy rollup: same predictions, scored the same way, grouped
-- by which LLM provider/model extracted them. channel_id NULL = across all
-- channels for that provider/model. Lets you compare e.g. openai/gpt-4o-mini
-- vs anthropic/claude on the *same* underlying articles.
CREATE TABLE IF NOT EXISTS provider_model_leaderboard (
    id          BIGSERIAL PRIMARY KEY,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    channel_id  INT REFERENCES channel(id) ON DELETE CASCADE,
    n_calls     INT NOT NULL DEFAULT 0,
    n_scored    INT NOT NULL DEFAULT 0,
    avg_score   REAL,
    hit_rate    REAL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (provider, model, channel_id)
);
CREATE INDEX IF NOT EXISTS provider_model_leaderboard_pm_idx
    ON provider_model_leaderboard(provider, model);

-- Seed sources. The `blog` source consolidates one-off, homepage-discovery
-- website scrapers (macrovoices, madxcap) that share no per-author crawl/
-- catalog state; each site is a separate channel under the single `blog`
-- source, just as individual creators are channels under `patreon` or
-- `substack`. 'newspaper' is used for multi-author, resumable crawlers that
-- track discovery state in their own *_crawl_run/*_article_catalog tables
-- (hkej, yahoohk, master-insight).
INSERT INTO source(code,name,url,kind) VALUES
  ('blog','Blogs',NULL,'blog'),
  ('youtube','YouTube','https://www.youtube.com/','youtube'),
  ('hkej','Hong Kong Economic Journal','https://www.hkej.com/','newspaper'),
  ('patreon','Patreon','https://www.patreon.com/','membership'),
  ('substack','Substack','https://substack.com/','membership'),
  ('yahoohk','Yahoo Finance Hong Kong','https://hk.finance.yahoo.com/','newspaper'),
  ('master-insight','Master Insight','https://www.master-insight.com/','newspaper')
ON CONFLICT (code) DO NOTHING;

-- Seed channels for the consolidated `blog` source (one per site/author).
INSERT INTO channel(source_id, handle, name, url)
SELECT id, 'macrovoices', 'MacroVoices', 'https://www.macrovoices.com/'
FROM source WHERE code='blog'
ON CONFLICT (source_id, handle) DO NOTHING;
INSERT INTO channel(source_id, handle, name, url)
SELECT id, 'kuangtu', '狂徒', 'https://madxcap.com/'
FROM source WHERE code='blog'
ON CONFLICT (source_id, handle) DO NOTHING;

-- --- Blog consolidation (macrovoices + madxcap → blog) ---------------------
-- This block re-points any items/channels from the old separate 'macrovoices'
-- and 'madxcap' source rows into the consolidated 'blog' source, then removes
-- those old source rows. No-op on databases already migrated (the old source
-- rows no longer exist). Idempotent: safe to replay.

-- Move any surviving old channels under blog (handle-based upsert).
INSERT INTO channel(source_id, handle, name, url)
SELECT (SELECT id FROM source WHERE code='blog'), c.handle, c.name, c.url
FROM channel c
JOIN source s ON s.id = c.source_id
WHERE s.code IN ('macrovoices', 'madxcap')
ON CONFLICT (source_id, handle) DO UPDATE SET
    name = EXCLUDED.name, url = EXCLUDED.url;

-- Re-point macrovoices items to blog/macrovoices.
UPDATE item i
SET source_id = (SELECT id FROM source WHERE code='blog'),
    channel_id = (
        SELECT c2.id FROM channel c2
        JOIN source s2 ON s2.id = c2.source_id
        WHERE s2.code = 'blog' AND c2.handle = 'macrovoices'
    )
FROM source old_s
WHERE i.source_id = old_s.id AND old_s.code = 'macrovoices';

-- Re-point madxcap items to blog/<matching-handle>.
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

-- Drop old channels + source rows now that items are moved.
DELETE FROM channel
WHERE source_id IN (
    SELECT id FROM source WHERE code IN ('macrovoices', 'madxcap')
);
DELETE FROM source WHERE code IN ('macrovoices', 'madxcap');

-- --- Pipeline tracking ------------------------------------------------------
-- Per-item stage timestamps so each item records when it moved through the
-- scrape → ingest → extract pipeline, and a per-source rollup table the
-- dashboard reads in one row instead of GROUP BY-ing the item table.
ALTER TABLE item ADD COLUMN IF NOT EXISTS ingested_at  TIMESTAMPTZ;
ALTER TABLE item ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS item_ingested_at_idx  ON item(ingested_at);
CREATE INDEX IF NOT EXISTS item_extracted_at_idx ON item(extracted_at);

-- YouTube transcript availability. True by default: only YouTube videos whose
-- markdown body carries the "_(no transcript available)_" marker are false.
-- The backfill block below sets the value for pre-existing rows; ingest and
-- the scraper keep it current for new rows. Non-YouTube sources are always
-- true (they always have body content).
ALTER TABLE item ADD COLUMN IF NOT EXISTS has_transcript BOOLEAN NOT NULL DEFAULT true;
-- Partial index: only the (relatively few) rows missing a transcript, so the
-- dashboard's "videos without transcript" query stays cheap.
CREATE INDEX IF NOT EXISTS item_has_transcript_idx
    ON item (source_id) WHERE has_transcript = false;

-- Whisper ASR transcription pipeline. For YouTube videos where no subtitle/
-- transcript could be fetched (has_transcript=false), the transcription script
-- downloads the audio, runs faster-whisper on GPU, and writes the generated
-- transcript back into the .md file + DB. These columns track that lifecycle.
--   NULL              → not a transcription candidate (has_transcript=true or non-YouTube)
--   'pending'         → queued for ASR
--   'audio_downloaded'→ audio file downloaded to tmp/, ready to transcribe
--   'transcribing'    → whisper currently running on this item
--   'done'            → transcript generated, written to .md + DB, has_transcript=true
--   'failed'          → could not download audio or transcription failed
ALTER TABLE item ADD COLUMN IF NOT EXISTS transcription_status   TEXT;      -- pending|audio_downloaded|transcribing|done|failed
ALTER TABLE item ADD COLUMN IF NOT EXISTS transcription_error    TEXT;      -- error message on failure
ALTER TABLE item ADD COLUMN IF NOT EXISTS transcription_language TEXT;      -- whisper-detected language code (en, yue, etc.)
ALTER TABLE item ADD COLUMN IF NOT EXISTS transcribed_at         TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS item_transcription_status_idx ON item (transcription_status);

CREATE TABLE IF NOT EXISTS source_progress (
    source_id         INT PRIMARY KEY REFERENCES source(id) ON DELETE CASCADE,
    n_downloaded      INT NOT NULL DEFAULT 0,   -- files written under data/ (best-effort, tracked forward)
    n_ingested        INT NOT NULL DEFAULT 0,   -- item rows with ingested_at NOT NULL
    n_extracted       INT NOT NULL DEFAULT 0,   -- extraction_status='done'
    n_extract_pending INT NOT NULL DEFAULT 0,   -- extraction_status='pending'
    n_extract_error   INT NOT NULL DEFAULT 0,   -- extraction_status='error'
    last_scrape_at    TIMESTAMPTZ,
    last_ingest_at    TIMESTAMPTZ,
    last_extract_at   TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per source (LEFT JOIN so sources with no items yet still appear).
INSERT INTO source_progress(source_id)
SELECT id FROM source
ON CONFLICT (source_id) DO NOTHING;

-- One-shot backfill of stage timestamps for pre-existing items. Guarded by a
-- sentinel so it only fires the first time (when the columns are all-NULL),
-- making it safe to replay. ingested_at falls back to scraped_at (set at row
-- creation by ingest); extracted_at is recovered from the primary run's
-- finished_at for items already marked 'done'.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM item WHERE ingested_at IS NULL LIMIT 1) THEN
        UPDATE item SET ingested_at = scraped_at WHERE ingested_at IS NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM item WHERE extraction_status='done' AND extracted_at IS NULL LIMIT 1) THEN
        UPDATE item i SET extracted_at = er.finished_at
        FROM extraction_run er
        WHERE er.id = i.primary_extraction_run_id
          AND i.extraction_status = 'done'
          AND i.extracted_at IS NULL;
    END IF;
END $$;

-- One-shot backfill of has_transcript for pre-existing YouTube items. Guarded
-- by a sentinel so it only fires when there are still-defaulted rows that are
-- actually missing a transcript (avoids re-scanning content on every replay).
-- Detection: the markdown body's "## Transcript" section is exactly the
-- placeholder "_(no transcript available)_". Non-YouTube rows stay true.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM item
               WHERE has_transcript = true
                 AND content LIKE '%## Transcript%_(no transcript available)_%'
               LIMIT 1) THEN
        UPDATE item SET has_transcript = false
        WHERE has_transcript = true
          AND content LIKE '%## Transcript%_(no transcript available)_%';
    END IF;
END $$;

-- One-shot backfill of transcription_status for YouTube items missing a
-- transcript. Guarded by a sentinel so it only fires when there are rows that
-- need transcription but haven't been queued yet. Non-YouTube sources and
-- videos that already have a transcript are left NULL (not candidates).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM item i
               JOIN source s ON s.id = i.source_id
               WHERE s.code = 'youtube'
                 AND i.has_transcript = false
                 AND i.transcription_status IS NULL
               LIMIT 1) THEN
        UPDATE item SET transcription_status = 'pending'
        WHERE id IN (
            SELECT i.id FROM item i
            JOIN source s ON s.id = i.source_id
            WHERE s.code = 'youtube'
              AND i.has_transcript = false
              AND i.transcription_status IS NULL
        );
    END IF;
END $$;

-- Recompute the per-source counters from the item table (authoritative; the
-- boundary hooks increment them at runtime, this reconciles any drift and
-- seeds correct values on first apply). Mirrors progress.recompute().
UPDATE source_progress sp SET
    n_ingested        = COALESCE((SELECT COUNT(*) FROM item i WHERE i.source_id = sp.source_id AND i.ingested_at IS NOT NULL), 0),
    n_extracted       = COALESCE((SELECT COUNT(*) FROM item i WHERE i.source_id = sp.source_id AND i.extraction_status = 'done'), 0),
    n_extract_pending = COALESCE((SELECT COUNT(*) FROM item i WHERE i.source_id = sp.source_id AND i.extraction_status = 'pending'), 0),
    n_extract_error   = COALESCE((SELECT COUNT(*) FROM item i WHERE i.source_id = sp.source_id AND i.extraction_status = 'error'), 0),
    last_ingest_at    = (SELECT MAX(i.ingested_at)  FROM item i WHERE i.source_id = sp.source_id),
    last_extract_at   = (SELECT MAX(i.extracted_at) FROM item i WHERE i.source_id = sp.source_id),
    updated_at        = now();

-- --- Discovery catalog ------------------------------------------------------
-- Records every item a scraper sees during discovery (before fetch), so
-- "discovered but not downloaded" is queryable and a half-dead scrape can be
-- resumed. Used by the 5 filesystem-discovery sources (youtube, blog/
-- macrovoices/madxcap, substack, yahoohk, master-insight); hkej and patreon
-- keep their richer native catalogs (hkej_article_catalog / patreon_post_catalog)
-- and are unioned in at read time.
CREATE TABLE IF NOT EXISTS discovery_catalog (
    id            BIGSERIAL PRIMARY KEY,
    source_id     INT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    channel_id    INT REFERENCES channel(id) ON DELETE SET NULL,
    channel_ref   TEXT,                 -- handle/slug; NULL where no channel row exists
    external_id   TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    published_at  TIMESTAMPTZ,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    downloaded    BOOLEAN NOT NULL DEFAULT false,
    downloaded_at TIMESTAMPTZ,
    md_path       TEXT,
    descriptor    JSONB DEFAULT '{}'::jsonb,  -- original discovery dict, for resume fetch()
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS discovery_catalog_pending_idx
    ON discovery_catalog(source_id) WHERE downloaded = false;
CREATE INDEX IF NOT EXISTS discovery_catalog_channel_idx ON discovery_catalog(channel_id);


