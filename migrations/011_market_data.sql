-- 011: market-data pipeline — price store + speaker leaderboard
-- (mirror of the statements appended to docker/postgres/init.sql; that file
--  is what `kb db migrate` actually replays, this one is convention/reference)

CREATE TABLE IF NOT EXISTS asset_price (
    ticker     TEXT NOT NULL,
    day        DATE NOT NULL,
    open       NUMERIC,
    high       NUMERIC,
    low        NUMERIC,
    close      NUMERIC,
    volume     BIGINT,
    source     TEXT NOT NULL DEFAULT 'yahoo',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, day)
);

CREATE TABLE IF NOT EXISTS asset_ticker (
    ticker         TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'ok',   -- ok | no_data | error
    first_day      DATE,
    last_day       DATE,
    n_days         INT NOT NULL DEFAULT 0,
    last_synced_at TIMESTAMPTZ,
    last_error     TEXT
);

CREATE TABLE IF NOT EXISTS leaderboard_speaker (
    speaker         TEXT PRIMARY KEY,
    main_channel_id INT REFERENCES channel(id) ON DELETE SET NULL,
    n_calls         INT NOT NULL DEFAULT 0,
    n_scored        INT NOT NULL DEFAULT 0,
    avg_score       REAL,
    hit_rate        REAL,
    last_call_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS leaderboard_speaker_score_idx
    ON leaderboard_speaker(avg_score DESC NULLS LAST);
