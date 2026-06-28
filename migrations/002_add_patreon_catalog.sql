-- Patreon crawl catalog (mirrors the hkej_* tables).
--
-- Patreon uses cursor pagination, so each crawl page stores its resume cursor
-- (next_url); interrupted crawls resume from the next uncrawled page, and new
-- posts (which shift page alignment) are detected via the page-1 fingerprint.
-- The catalog tracks every post (date, title, link) plus a downloaded flag so a
-- shutdown mid-download only leaves the remaining posts pending.

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
