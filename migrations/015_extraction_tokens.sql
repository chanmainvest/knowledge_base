-- 015_extraction_tokens.sql — per-run LLM token usage on extraction_run.
-- Mirrors the section appended to init.sql (which is the replayable source
-- of truth; this file is the conventional reference). Idempotent: safe to
-- re-run on an already-migrated DB.

-- NULL = the run predates usage capture, or the provider can't report it
-- (the github/copilot CLI). cached_tokens is the provider-reported subset
-- of prompt_tokens served from cache (subset, not additive). Cost is not
-- stored: it's derived at read time from OpenRouter reference prices
-- (`kb extract cost`), so price updates never touch the data.
ALTER TABLE extraction_run ADD COLUMN IF NOT EXISTS prompt_tokens INT;
ALTER TABLE extraction_run ADD COLUMN IF NOT EXISTS cached_tokens INT;
ALTER TABLE extraction_run ADD COLUMN IF NOT EXISTS completion_tokens INT;
