-- Pipeline tracking: per-item stage timestamps + per-source progress rollup.
--
-- NOTE: as with all migrations in this repo, docker/postgres/init.sql is the
-- source of truth and `kb db migrate` replays it. This file mirrors the same
-- statements for standalone `psql -f` application / historical record.

-- Per-item stage timestamps.
ALTER TABLE item ADD COLUMN IF NOT EXISTS ingested_at  TIMESTAMPTZ;
ALTER TABLE item ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS item_ingested_at_idx  ON item(ingested_at);
CREATE INDEX IF NOT EXISTS item_extracted_at_idx ON item(extracted_at);

-- Per-source rollup (one row per source) read by the dashboard.
CREATE TABLE IF NOT EXISTS source_progress (
    source_id         INT PRIMARY KEY REFERENCES source(id) ON DELETE CASCADE,
    n_downloaded      INT NOT NULL DEFAULT 0,
    n_ingested        INT NOT NULL DEFAULT 0,
    n_extracted       INT NOT NULL DEFAULT 0,
    n_extract_pending INT NOT NULL DEFAULT 0,
    n_extract_error   INT NOT NULL DEFAULT 0,
    last_scrape_at    TIMESTAMPTZ,
    last_ingest_at    TIMESTAMPTZ,
    last_extract_at   TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO source_progress(source_id)
SELECT id FROM source
ON CONFLICT (source_id) DO NOTHING;

-- One-shot backfill of timestamps for pre-existing items (guarded so replay-safe).
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

-- Recompute counters from the item table (authoritative reconciliation).
UPDATE source_progress sp SET
    n_ingested        = COALESCE((SELECT COUNT(*) FROM item i WHERE i.source_id = sp.source_id AND i.ingested_at IS NOT NULL), 0),
    n_extracted       = COALESCE((SELECT COUNT(*) FROM item i WHERE i.source_id = sp.source_id AND i.extraction_status = 'done'), 0),
    n_extract_pending = COALESCE((SELECT COUNT(*) FROM item i WHERE i.source_id = sp.source_id AND i.extraction_status = 'pending'), 0),
    n_extract_error   = COALESCE((SELECT COUNT(*) FROM item i WHERE i.source_id = sp.source_id AND i.extraction_status = 'error'), 0),
    last_ingest_at    = (SELECT MAX(i.ingested_at)  FROM item i WHERE i.source_id = sp.source_id),
    last_extract_at   = (SELECT MAX(i.extracted_at) FROM item i WHERE i.source_id = sp.source_id),
    updated_at        = now();
