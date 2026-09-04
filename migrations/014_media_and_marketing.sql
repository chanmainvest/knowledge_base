-- 014_media_and_marketing.sql — extraction v2: marketing classification +
-- media-mention tracking. Mirrors the section appended to init.sql (which is
-- the replayable source of truth; this file is the conventional reference).
-- Idempotent: safe to re-run on an already-migrated DB.

ALTER TABLE item ADD COLUMN IF NOT EXISTS is_marketing BOOLEAN;
CREATE INDEX IF NOT EXISTS item_is_marketing_idx
    ON item (is_marketing) WHERE is_marketing = true;

CREATE TABLE IF NOT EXISTS media_work (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('book','movie','paper')),
    title       TEXT NOT NULL,
    title_norm  TEXT NOT NULL,
    creators    TEXT,
    year        INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, title_norm)
);

CREATE TABLE IF NOT EXISTS media_mention (
    id                 BIGSERIAL PRIMARY KEY,
    media_work_id      BIGINT NOT NULL REFERENCES media_work(id) ON DELETE CASCADE,
    item_id            INT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    extraction_run_id  BIGINT NOT NULL REFERENCES extraction_run(id) ON DELETE CASCADE,
    speaker            TEXT,
    quote              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (extraction_run_id, media_work_id)
);
CREATE INDEX IF NOT EXISTS media_mention_item_idx  ON media_mention(item_id);
CREATE INDEX IF NOT EXISTS media_mention_work_idx ON media_mention(media_work_id);
