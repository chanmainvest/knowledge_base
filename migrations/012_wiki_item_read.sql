-- 012: LLM-wiki item read tracking.
-- Which items scripts/build_llm_wiki.py has already consumed (fed to an LLM
-- pass / rendered into a page), with a sha256 of exactly what was read, so
-- unchanged items are never re-read (no wasted tokens). Dual-written with
-- llm-wiki/read-state.json — the JSON copy is committed with the wiki, so the
-- state survives losing the DB (re-ingest the data/ markdown; entries
-- re-attach by source_code+external_id even though item ids changed).
-- Historical/reference copy of the block appended to init.sql.

CREATE TABLE IF NOT EXISTS wiki_item_read (
    item_id     INT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL,          -- e.g. 'weekly-digest'
    source_code TEXT NOT NULL,
    external_id TEXT NOT NULL,          -- stable key across re-ingest
    content_sha TEXT NOT NULL,          -- sha256[:16] of what was consumed
    week_start  DATE,                   -- set by the weekly digest purpose
    read_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, purpose)
);
CREATE INDEX IF NOT EXISTS wiki_item_read_ext_idx
    ON wiki_item_read(source_code, external_id);
