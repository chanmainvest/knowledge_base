# Spec — LLM extraction (providers, prompt versioning, prediction shaping)

Read this when touching `src/kb/llm.py`, `src/kb/extract.py`,
`src/kb/prompts.py`, `src/kb/prompts/extraction/`, or the
`extraction_run`/`prediction` tables.

- LLM calls go through `kb.llm.chat_json(system, user, schema, provider,
  model)`, which supports four providers: `openai` (or any OpenAI-compatible
  endpoint via `LLM_BASE_URL`, e.g. Azure OpenAI, GitHub Models, Ollama),
  `github` (shells out to the local `copilot` CLI in non-interactive mode —
  no separate API key, uses existing `copilot /login` auth), `anthropic`
  (Anthropic Messages API via forced tool-call JSON), and `zai` (Z.ai/Zhipu
  GLM, OpenAI-wire-compatible). `LLM_PROVIDER` in `.env` picks the default
  (zai since 2026-08-14); the zai default model is `ZAI_MODEL` in `.env`
  (config default `glm-5.3-flash` since 2026-08-27 — faster/cheaper than
  `glm-5.3`, which is still available as an explicit `--model` override);
  override per call with `provider=`/`--provider`. Every extraction attempt
  is recorded in `extraction_run` (one row per item/provider/model/prompt
  version), so multiple providers can extract the same item without
  clobbering each other — see `doc/llm-extraction.md` for the full design,
  including how `kb extract compare`/`provider_model_leaderboard` let you
  cross-reference which provider/model is most accurate.
- **Extraction prompt/schema versioning (file registry).** The extraction
  system prompt and its JSON schema are NOT constants in `extract.py` — they
  live as versioned file pairs under `src/kb/prompts/extraction/<version>/`
  (`system.md`, markdown with a YAML front-matter header whose `version:`
  must match the dir name, + `schema.json`), loaded by `kb.prompts`
  (`prompts.py`). The directory name is the `prompt_version` recorded in
  `extraction_run`, so iterating on a prompt/schema means copying to the
  next version dir — old runs and their predictions stay untouched, and the
  new version becomes the default (highest version present; pin with
  `EXTRACTION_PROMPT_VERSION` or `--prompt-version`; `kb extract prompts`
  lists them). `kb extract compare --providers zai,zai --model
  glm-4.6,glm-5.3` A/Bs models on one item without touching its primary
  run. The files MUST stay inside `src/kb/` — the Dockerfile only COPYs
  `src/`, and the wheel only packages `src/kb`.
- **Per-ticker prediction consolidation (read-time).** The LLM extracts per
  chunk, so the same ticker can appear as several flat `prediction` rows for
  one item (one per quote). Those rows are the source of truth for scoring
  and the leaderboard and are **not** merged in the DB. The item-detail
  endpoint collapses them at read time via `_consolidate_predictions()` in
  `src/kb/api/main.py` into one entry per ticker with a `quotes[]` array, a
  consensus `direction`, and a `conflict` flag set when the same ticker has
  both a bullish and a bearish call in the same article. The flat
  `/api/predictions` list still returns raw rows (primary-run scoped by
  default; `?all_runs=true` for raw). The frontend item page renders one
  card per ticker (with an amber **conflict** badge and a price sparkline
  from the market store) and makes each quote clickable to jump to and
  highlight it in the article body.
