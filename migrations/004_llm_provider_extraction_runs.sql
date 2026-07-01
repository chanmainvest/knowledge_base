-- Multi-provider LLM extraction + versioned storage.
--
-- Adds `extraction_run` (one row per item/provider/model/prompt_version
-- extraction attempt) and tags `view_market`/`prediction` rows with the run
-- that produced them, so the same article can be extracted by several LLM
-- providers/models without one overwriting another. Adds
-- `item.primary_extraction_run_id` (the canonical run used by the API,
-- frontend, and per-channel leaderboard) and `provider_model_leaderboard`
-- (cross-model accuracy rollup).
--
-- NOTE: `kb db migrate` replays the full, idempotent `docker/postgres/init.sql`
-- rather than this file, so init.sql is the actual source of truth. This file
-- is kept for a standalone, minimal-diff apply against an existing database
-- (e.g. via `psql -f migrations/004_llm_provider_extraction_runs.sql`) and as
-- a historical record of the change, matching the existing migrations/ convention.

CREATE TABLE IF NOT EXISTS extraction_run (
    id              BIGSERIAL PRIMARY KEY,
    item_id         BIGINT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL DEFAULT 'v1',
    status          TEXT NOT NULL DEFAULT 'running',
    error           TEXT,
    summary         TEXT,
    raw_response    JSONB,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INT,
    UNIQUE (item_id, provider, model, prompt_version)
);
CREATE INDEX IF NOT EXISTS extraction_run_item_idx ON extraction_run(item_id);
CREATE INDEX IF NOT EXISTS extraction_run_provider_model_idx ON extraction_run(provider, model);

ALTER TABLE item ADD COLUMN IF NOT EXISTS primary_extraction_run_id BIGINT
    REFERENCES extraction_run(id) ON DELETE SET NULL;

ALTER TABLE view_market ADD COLUMN IF NOT EXISTS extraction_run_id BIGINT
    REFERENCES extraction_run(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS view_market_run_idx ON view_market(extraction_run_id);

ALTER TABLE prediction ADD COLUMN IF NOT EXISTS extraction_run_id BIGINT
    REFERENCES extraction_run(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS prediction_run_idx ON prediction(extraction_run_id);

-- If this database already has extraction rows from before this migration
-- (extraction_run_id IS NULL), back-fill one 'legacy' run per item so the
-- NOT NULL constraint below can be applied. Fresh/empty installs skip this.
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
        ON CONFLICT (item_id, provider, model, prompt_version) DO UPDATE SET provider = EXCLUDED.provider
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
