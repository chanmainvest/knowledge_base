-- Initial extensions and schema for KB.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Sources: macrovoices, youtube, hkej, etc.
CREATE TABLE IF NOT EXISTS source (
    id          SERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,           -- 'macrovoices' | 'youtube' | 'hkej'
    name        TEXT NOT NULL,
    url         TEXT,
    kind        TEXT NOT NULL                   -- 'podcast' | 'youtube' | 'newspaper'
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

-- Seed sources. `kind` groups sources for filtering in the UI/CLI:
-- 'blog' is used for simple, single-page/homepage-discovery scrapers with no
-- per-author crawl/catalog state (macrovoices, madxcap); 'newspaper' is used
-- for multi-author, resumable crawlers that track discovery state in their
-- own *_crawl_run/*_article_catalog tables (hkej, yahoohk, master-insight).
INSERT INTO source(code,name,url,kind) VALUES
  ('macrovoices','MacroVoices','https://www.macrovoices.com/','blog'),
  ('youtube','YouTube','https://www.youtube.com/','youtube'),
  ('hkej','Hong Kong Economic Journal','https://www.hkej.com/','newspaper'),
  ('patreon','Patreon','https://www.patreon.com/','membership'),
  ('substack','Substack','https://substack.com/','membership'),
  ('yahoohk','Yahoo Finance Hong Kong','https://hk.finance.yahoo.com/','newspaper'),
  ('master-insight','Master Insight','https://www.master-insight.com/','newspaper'),
  ('madxcap','狂徒投資','https://madxcap.com/','blog')
ON CONFLICT (code) DO NOTHING;

-- Re-classify macrovoices as 'blog' for databases seeded before this change
-- (the INSERT above is a no-op on existing rows because of ON CONFLICT DO
-- NOTHING, so existing 'podcast' rows need an explicit UPDATE here).
UPDATE source SET kind='blog' WHERE code='macrovoices' AND kind<>'blog';

-- Seed channels for single-author sources.
INSERT INTO channel(source_id, handle, name, url)
SELECT id, 'kuangtu', '狂徒', 'https://madxcap.com/'
FROM source WHERE code='madxcap'
ON CONFLICT (source_id, handle) DO NOTHING;
