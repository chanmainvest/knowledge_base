-- YouTube transcript-availability tracking.
--
-- NOTE: as with all migrations in this repo, docker/postgres/init.sql is the
-- source of truth and `kb db migrate` replays it. This file mirrors the same
-- statements for standalone `psql -f` application / historical record.
--
-- YouTube markdown files whose "## Transcript" section is the placeholder
-- "_(no transcript available)_" are marked has_transcript=false. All other
-- items (YouTube with a real transcript, and every non-YouTube source, which
-- always has body content) default to true.

ALTER TABLE item ADD COLUMN IF NOT EXISTS has_transcript BOOLEAN NOT NULL DEFAULT true;

-- Partial index: only the (relatively few) rows missing a transcript, so the
-- dashboard's "videos without transcript" query stays cheap.
CREATE INDEX IF NOT EXISTS item_has_transcript_idx
    ON item (source_id) WHERE has_transcript = false;

-- Backfill: flag any existing YouTube items whose body carries the marker.
UPDATE item SET has_transcript = false
WHERE has_transcript = true
  AND content LIKE '%## Transcript%_(no transcript available)_%';
