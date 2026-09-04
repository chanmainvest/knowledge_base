# Spec — LLM extraction (providers, prompt versioning, prediction shaping)

Read this when touching `src/kb/llm.py`, `src/kb/extract.py`,
`src/kb/prompts.py`, `src/kb/prompts/extraction/`, or the
`extraction_run`/`prediction` tables.

- LLM calls go through `kb.llm.chat_json(system, user, schema, provider,
  model)`, which supports five providers: `openai` (or any OpenAI-compatible
  endpoint via `LLM_BASE_URL`, e.g. Azure OpenAI, GitHub Models, Ollama),
  `github` (shells out to the local `copilot` CLI in non-interactive mode —
  no separate API key, uses existing `copilot /login` auth), `anthropic`
  (Anthropic Messages API via forced tool-call JSON), `zai` (Z.ai/Zhipu
  GLM, OpenAI-wire-compatible), and `openrouter` (OpenAI-compatible;
  configured as the chat endpoint's backup — `/api/chat` always tries the
  primary provider first and only calls `openrouter` when that raises and
  `OPENROUTER_API_KEY` is set; there is no free GLM tier on OpenRouter). `LLM_PROVIDER` in `.env` picks the default
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
- **Single-flight batches.** `extract.run()` takes a session-scoped
  `pg_try_advisory_lock(7261001)` on a dedicated NullPool connection before
  picking pending items; a second concurrent batch (local run vs the Jenkins
  nightly's `kb extract run --limit 200`) exits with 0 instead of
  interleaving `_persist()` writes into the same `extraction_run` row.
  Closing the connection releases the lock, so a crashed batch never wedges
  the queue.
- **Token usage + reference cost (2026-09-02).** `chat_json()` returns
  `(parsed, usage)`; `extract_item()` sums per-chunk usage and `_finish_run()`
  persists `prompt_tokens`/`cached_tokens`/`completion_tokens` on
  `extraction_run` (NULL = pre-capture runs, or the copilot CLI which reports
  no usage). `cached_tokens` is a subset of `prompt_tokens`. `kb extract cost`
  aggregates and prices it via `kb.pricing` (OpenRouter public catalogue,
  `zai` → `z-ai/<model>` id mapping; reference prices, not actual billing).
  The single-flight advisory lock (below) is why you can't `run` while the
  nightly holds the lock — use `compare` for ad-hoc testing, it skips the
  lock.
- **v2 additions: marketing flag + media mentions (2026-08-30).** The v2
  prompt/schema adds two targets on top of v1: `is_marketing` (per-chunk
  boolean — is this text predominantly promotional? sponsor reads inside an
  otherwise substantive piece do NOT count) and `media_mentions[]`
  (finance-related books/movies/papers with `kind`/`title`/`creators`/
  `year`/`speaker`/`quote`). Persistence: chunk flags are majority-voted in
  `_promote_primary()` onto `item.is_marketing` (NULL = unclassified, i.e.
  v1-era); mentions upsert a canonical `media_work` row (deduped on
  `(kind, title_norm)` — `_norm_title()` strips parenthetical localized
  suffixes like "The Big Short (華爾街大沽空)" and leading articles) plus one
  `media_mention` row per run (`speaker` + `quote` attribution; re-runs
  idempotent, scoped to the run). glm-flash does NOT strictly honour the
  json_schema — `_persist` normalizes the seen-in-the-wild drifts (`kind`
  → `type`, `creators` as list) before the enum guard. Serving: `/api/media`
  (in `src/kb/api/media.py`, Core expressions because the security hook
  rejects new raw-`text()` SELECTs) ranks works by mention count with
  speaker attribution and drills down per work; item detail gains
  `media_mentions`; `/api/search` + `/api/items` hide `is_marketing=true`
  items by default (`?marketing=include|only` to override — NULL counts as
  not-marketing so the v1 backlog stays visible).
