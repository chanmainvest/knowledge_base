-- Discovery catalog: records every item a scraper sees during discovery so
-- "discovered but not downloaded" is queryable and a half-dead scrape can be
-- resumed. Used by the 5 filesystem-discovery sources; hkej/patreon keep
-- their native catalogs.
--
-- As with all migrations here, init.sql is the source of truth and `kb db
-- migrate` replays it; this file mirrors the same statements for standalone
-- `psql -f` use.

CREATE TABLE IF NOT EXISTS discovery_catalog (
    id            BIGSERIAL PRIMARY KEY,
    source_id     INT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    channel_id    INT REFERENCES channel(id) ON DELETE SET NULL,
    channel_ref   TEXT,
    external_id   TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    published_at  TIMESTAMPTZ,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    downloaded    BOOLEAN NOT NULL DEFAULT false,
    downloaded_at TIMESTAMPTZ,
    md_path       TEXT,
    descriptor    JSONB DEFAULT '{}'::jsonb,
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS discovery_catalog_pending_idx
    ON discovery_catalog(source_id) WHERE downloaded = false;
CREATE INDEX IF NOT EXISTS discovery_catalog_channel_idx ON discovery_catalog(channel_id);
