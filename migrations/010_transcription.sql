-- Whisper ASR transcription pipeline.
--
-- NOTE: as with all migrations in this repo, docker/postgres/init.sql is the
-- source of truth and `kb db migrate` replays it. This file mirrors the same
-- statements for standalone `psql -f` application / historical record.
--
-- For YouTube videos where no subtitle/transcript could be fetched
-- (has_transcript=false), the transcription script downloads the audio, runs
-- faster-whisper on GPU, and writes the generated transcript back into the
-- .md file + DB. These columns track that lifecycle:
--   NULL              → not a transcription candidate
--   'pending'         → queued for ASR
--   'audio_downloaded'→ audio downloaded to tmp/, ready to transcribe
--   'transcribing'    → whisper currently running on this item
--   'done'            → transcript generated, has_transcript flipped to true
--   'failed'          → could not download audio or transcription failed

ALTER TABLE item ADD COLUMN IF NOT EXISTS transcription_status   TEXT;
ALTER TABLE item ADD COLUMN IF NOT EXISTS transcription_error    TEXT;
ALTER TABLE item ADD COLUMN IF NOT EXISTS transcription_language TEXT;
ALTER TABLE item ADD COLUMN IF NOT EXISTS transcribed_at         TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS item_transcription_status_idx ON item (transcription_status);

-- Backfill: queue all YouTube items missing a transcript for ASR.
UPDATE item SET transcription_status = 'pending'
WHERE id IN (
    SELECT i.id FROM item i
    JOIN source s ON s.id = i.source_id
    WHERE s.code = 'youtube'
      AND i.has_transcript = false
      AND i.transcription_status IS NULL
);
