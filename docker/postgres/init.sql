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
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS item_published_at_idx ON item(published_at DESC);
CREATE INDEX IF NOT EXISTS item_channel_idx     ON item(channel_id);
CREATE INDEX IF NOT EXISTS item_status_idx      ON item(extraction_status);

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

-- LLM-extracted structured records.
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

-- Seed sources.
INSERT INTO source(code,name,url,kind) VALUES
  ('macrovoices','MacroVoices','https://www.macrovoices.com/','podcast'),
  ('youtube','YouTube','https://www.youtube.com/','youtube'),
  ('hkej','Hong Kong Economic Journal','https://www.hkej.com/','newspaper'),
  ('patreon','Patreon','https://www.patreon.com/','membership')
ON CONFLICT (code) DO NOTHING;
