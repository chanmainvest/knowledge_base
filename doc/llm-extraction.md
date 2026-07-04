# LLM Extraction: How Markdown Becomes Evaluable Data

This page explains, precisely, how `kb extract run` turns a scraped Markdown
article/transcript into the structured rows that power search, the
prediction browser, and the channel leaderboard — and how to read that data
if your goal is to decide **which financial writer/channel is worth
following, and which one you should ignore.**

It is in three parts:

1. **What the pipeline does today** (the original, single-provider design).
2. **Proposed improvements** to make the output more decision-useful for a
   retail investor (not all implemented — flagged where still an idea).
3. **The multi-provider, versioned-extraction design** that now exists, and
   how to use it to cross-reference model accuracy.

---

## Part 1 — The pipeline as originally built

### Input

`item.content` — plain text produced by `kb ingest` from a scraped Markdown
file (front matter stripped). One `item` row = one article, video transcript,
or podcast episode, each linked to a `channel` (the author/creator) and a
`source` (hkej, youtube, blog, yahoohk, master-insight, patreon, substack, ...).

### Chunking

`extract._chunks(text, max_chars=14000)` (`src/kb/extract.py`) splits long
content on blank-line paragraph boundaries and packs paragraphs into chunks
up to ~14,000 characters, so a single LLM call's input never gets too large.
Short items (the common case) are a single chunk.

### The prompt

Every chunk is sent with the same fixed `SYSTEM` prompt:

> "You are a careful financial analyst. From a transcript or article,
> extract: (1) the speaker/author's broad market views, (2) any specific
> predictions about tickers/assets, (3) any tradable buy/sell calls. Use the
> exact words from the source as 'quote'. Do NOT invent. ... Output strict
> JSON per the schema."

...plus a per-chunk user message with `TITLE`, `DATE`, `LANGUAGE`, and the
chunk text. The **same prompt is used for every source and every author** —
there is no per-channel customization.

### The schema

The LLM must return one JSON object matching a fixed schema (`extract.SCHEMA`):

- `summary` (string) — one-shot summary, only kept from the *first* chunk.
- `speakers` (string[]).
- `market_views[]` — each with `speaker`, `asset_class`, `region`,
  `direction` (`bullish|bearish|neutral|mixed`), `horizon`
  (`short|medium|long|unspecified`), `confidence` (free-form number, no
  defined scale), `rationale`, `quote`.
- `predictions[]` — each with `speaker`, `ticker` (Yahoo-Finance style, e.g.
  `AAPL`, `GC=F`, `^GSPC`), `asset_name`, `action`
  (`buy|sell|short|hold|watch|long|cover|avoid|none`), `direction`
  (`up|down|flat|unspecified`), `target_price`, `stop_price`, `timeframe`
  (free-text, e.g. "3 months"), `quote`.
- `entities[]` — `kind` (`person|company|country|theme`), `name`, `ticker`.

Chunk results are naively concatenated (`aggregate[k].extend(...)`) — there
is no de-duplication across chunks, and only chunk 0's `summary` survives.
This means the same ticker can show up as several flat `prediction` rows for
one item (one per quote, possibly one per chunk). Those duplicate rows are
consolidated at read time — see "Within-article consolidation" below.

### Persistence

- `market_views` → `view_market` (one row per view, all on that item).
- `predictions` → `prediction` (one row per call).
- `entities` → upserted into `entity` (unique on `kind,name`), linked via
  `item_entity`.
- On success, `item.summary`, `item.extraction_status='done'`.
- **Original bug**: if the LLM call raised (rate limit, bad JSON, network
  error, out of retries), the exception was logged but `item.extraction_status`
  was **never updated** — the item stayed `'pending'` forever with no
  visible error, indistinguishable from "not yet processed". *(Fixed as part
  of the multi-provider rewrite — see Part 3.)*

### Within-article consolidation (read time)

Because chunk results are concatenated with no de-duplication, the same ticker
commonly appears as several flat `prediction` rows for one item — once per
quote, and potentially once per chunk that mentions it. The flat rows are the
source of truth for scoring and the leaderboard and are **not** merged in the
DB. Instead the item-detail endpoint collapses them at read time via
`_consolidate_predictions()` in `src/kb/api/main.py`:

- Rows are grouped by normalized ticker (uppercase, stripped). Rows without a
  ticker each stay as their own one-off entry, so untickered predictions
  aren't all lumped together.
- Each group becomes one object: `{ticker, asset_name, speaker, direction,
  conflict, quotes[]}`, where `quotes[]` carries the original `action`,
  `direction`, `target_price`, `stop_price`, `timeframe`, `quote`, `score`,
  and `made_at` of every row in the group.
- **Conflict detection** is purely directional. A quote is *bullish* if its
  `action ∈ {buy, long, cover}` or `direction == 'up'`; *bearish* if
  `action ∈ {sell, short, avoid}` or `direction == 'down'`; neutral otherwise
  (`hold`/`watch`/`avoid`/`none`/`flat`/`unspecified`). `conflict` is set when
  the same ticker has at least one bullish and at least one bearish quote, and
  the consensus `direction` becomes `mixed` in that case (otherwise `up`,
  `down`, or `neutral`).

This is a read-only view: no schema change, no re-extraction, and the flat
`GET /api/predictions` list (used by the `/predictions` page) still returns
raw rows. This is distinct from Part 2 proposal #6, which concerns
*cross-time* reversals on the same ticker by the same channel.

### Scoring — turning a call into a number

`leaderboard.score_prediction()` (`src/kb/leaderboard.py`) is what actually
judges whether a call was "right":

1. **Horizon**: mapped from the free-text `timeframe` string to a fixed
   number of days — `day→7`, `week→14`, `month`/`quarter→90`, `year→365`,
   anything unparsed → `90`. This is a coarse heuristic; "3 months" and
   "next quarter" both collapse to 90 days regardless of the author's actual
   wording.
2. **Prices**: `yfinance` daily close on/after `made_at` (the item's
   published date) and on/after `made_at + horizon` (or "now" if the horizon
   hasn't elapsed yet).
3. **Sign**: `+1` if `direction=='up'` or `action in {buy, long}`; `-1` if
   `direction=='down'` or `action in {short, sell}`; **`0` (neutral) for
   everything else** — including `hold`, `watch`, `avoid`, `none`, or an
   empty/ambiguous direction.
4. **Score**: `sign * ((price_eval - price_call) / price_call) * 5`, clamped
   to `[-1, +1]` — i.e. a ±20% move earns the maximum score.
5. Calls with no `ticker`, no `made_at`, or unresolvable prices are left
   `score = NULL` (excluded from averages, not counted as failures).

`leaderboard.rebuild()` then rolls per-item scores up into
`leaderboard_weekly` (per channel, per ISO week: `n_calls`, `n_scored`,
`avg_score`, `hit_rate` = fraction of scored calls with `score > 0`), and the
API's `/api/leaderboard` also computes an all-time `overall` rollup the same
way.

### What you could see today (before this change)

- One `view_market`/`prediction` set per item — whichever provider/model was
  configured in `.env` at the time, with **no record of what model produced
  it** and **no way to keep more than one version** (re-running extraction
  for an item overwrote its rows).
- A single global LLM config (`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`),
  OpenAI-wire-compatible only.
- `avg_score`/`hit_rate` per channel per week, and all-time — a raw average
  of every scored call, with no adjustment for volume, benchmark, or the
  large fraction of calls that score exactly `0` (vague/neutral calls).

### Why the current output is limited for a retail investor

Even with correct chunking/scoring, the numbers as originally computed have
real weaknesses if your goal is "should I keep reading this person":

- **A "0" score is ambiguous.** It's given to `hold`/`watch`/`none` calls
  *and* to calls the scorer couldn't evaluate for other reasons — but more
  importantly, an author who mostly hedges ("could go either way") scores
  close to 0 average, which looks *identical* to an author whose bold calls
  cancel out (some big wins, some big losses). Those are very different
  risk profiles.
- **No benchmark.** A `+0.3` average score sounds fine, but if the S&P 500
  was up 15% over the same period, "always bullish on tech" would also score
  `+0.3`+ while adding no insight — there's nothing here to tell a permabull
  apart from a genuine market timer.
- **No permabull/permabear detection.** An author who calls "buy" on
  everything, every time, isn't necessarily wrong (bull markets reward this)
  but isn't *useful* either — you already know what they'll say tomorrow.
  Nothing in `leaderboard_weekly` currently flags "this channel's direction
  has ~0 variance."
- **No confidence weighting.** The schema has a `confidence` field, but
  `score_prediction()` never reads it — a hedged "maybe AAPL could rise" and
  a firm "AAPL to $250 by June" score identically if both resolve up.
- **No volume normalization / small-sample protection.** A channel with 3
  lucky calls in a quiet quarter can outrank a channel with 200 calls and a
  genuinely repeatable edge, because the leaderboard sorts on raw
  `avg_score` with no confidence interval or minimum-sample floor.
- **No tracking of revisions/reversals.** If a writer says "buy" one week
  and "sell" the next on the same ticker without acknowledging the flip,
  that's a meaningful reliability signal (or a legitimate re-assessment) —
  today it's just two independent, unrelated `prediction` rows.

---

## Part 2 — Proposed improvements for retail-investor usefulness

These are **proposals** — a roadmap for making `leaderboard_weekly` and a new
per-channel "report card" genuinely answer "should I follow this person."
Only the multi-provider/versioning work in Part 3 has been implemented so
far; the ideas below are documented for prioritization, not yet built.

1. **Benchmark-relative scoring (alpha, not just return).**
   For every scored prediction, also fetch the benchmark return over the
   same window (e.g. `^GSPC` for US equities, `^HSI` for HK names, `GC=F`'s
   own sector benchmark, etc.) and store `score_vs_benchmark = sign * (asset
   return - benchmark return) * 5`. A channel that's "always bullish" would
   show a near-zero alpha score even while its raw score looks good in a
   rising market — immediately separating genuine calls from "stocks go up
   long-term" cheerleading.

2. **Directional-variance / permabull flag.**
   Per channel, per rolling window: `stdev(sign)` and `% bullish` across all
   `market_views`/`predictions`. Surface a simple badge: "92% bullish over
   last 100 calls — low variance, treat conviction claims with caution."
   This turns an implicit pattern into an explicit, visible signal on the
   channel page.

3. **Calibration / Brier-style scoring using `confidence`.**
   Start actually using the `confidence` field the schema already collects:
   bucket calls by stated confidence (low/med/high, or numeric quintiles)
   and report realized hit-rate per bucket. A well-calibrated author's
   "high confidence" bucket should out-perform their "low confidence"
   bucket — if it doesn't (or confidence doesn't correlate with outcome at
   all), that's a very concrete piece of information ("this person's
   confidence is not informative").

4. **Penalize/segregate vague calls instead of scoring them as neutral 0.**
   Track `n_vague` (action in `hold|watch|none|avoid`, or empty direction)
   as its own metric, separate from `avg_score`. Report "hit rate among
   *actionable* calls" alongside "% of calls that were non-committal" so a
   channel that mostly hedges doesn't get an artificially inflated
   (or deflated) score just by avoiding a stance.

5. **Volume-normalized / confidence-interval leaderboard.**
   Rank channels by a lower confidence bound (e.g. Wilson interval on
   hit-rate, or `avg_score - 1.96*stderr`) rather than raw `avg_score`, and
   require a minimum `n_scored` (e.g. 20) before a channel appears in the
   headline leaderboard at all — with a separate "provisional / small
   sample" tier for newer or low-volume channels.

6. **Call-level "still open / resolved / reversed" tracking.**
   Detect same-ticker predictions from the same channel across time
   (`item_entity`/`prediction.ticker` already link this) and flag reversals
   ("said buy on 3/1, said sell on 4/15 with no acknowledgment") as their own
   metric — reliability of *narrative*, not just of individual calls.

7. **Per-author "report card" view**: combine 1–6 into a single page per
   channel: alpha vs. benchmark, directional bias, calibration curve,
   actionable hit-rate, volume, and — from Part 3 — how consistent their
   extracted calls are across LLM providers (low cross-model agreement on
   what a channel "said" is itself a signal that the source material is
   ambiguous/hedge-y, independent of market outcome).

None of proposals 1–7 require schema changes beyond what already exists
(`confidence`, `action`, `direction`, `ticker`, `made_at` are all already
captured) — they are new SQL rollups/views and, for benchmark-relative
scoring, one more `yfinance` call per scored prediction. They're listed here
in the rough order of "most bang for the least new code."

---

## Part 3 — Multi-provider extraction with versioned storage

### Why

Any single LLM can misread a source (wrong ticker, invented direction, missed
hedge language). Rather than trust one provider's reading, the pipeline can
now run the **same article through several LLM providers** and keep every
version, so you can (a) sanity-check extraction quality by eye, and (b),
once enough predictions have resolved, see empirically **which provider is
the most reliable reader** of a given source/channel — the
`provider_model_leaderboard` table.

### Providers

`src/kb/llm.py` dispatches `chat_json(system, user, schema, provider, model)`
to one of four backends, selected by name (`llm.PROVIDERS = ("openai",
"github", "anthropic", "zai")`):

| provider    | how it's called | notes |
|-------------|------------------|-------|
| `openai`    | OpenAI Python SDK, `chat.completions.create(..., response_format={"type":"json_schema", "strict": True})` | also works for any OpenAI-wire-compatible endpoint via `LLM_BASE_URL` (Azure OpenAI, local Ollama, etc.) |
| `github`    | shells out to the local **`copilot` CLI** in non-interactive mode: `copilot -p "<prompt>" --silent --allow-all-tools --available-tools= --no-ask-user --no-color [--model X]` | there is no public raw completions API for GitHub Copilot, so this drives the actual CLI binary; `--available-tools=` disables all tool use so it behaves like a plain chat call; output is parsed leniently (`_extract_json_object`) since the CLI has no native JSON-schema mode |
| `anthropic` | Anthropic Python SDK, `messages.create(..., tools=[...], tool_choice={"type":"tool", "name": "emit_structured_result"})` | Anthropic has no native strict-JSON mode, so structured output is forced via a single required tool call whose `input_schema` is the extraction schema |
| `zai`       | same OpenAI-wire code path as `openai`, pointed at `https://api.z.ai/api/paas/v4` | Z.ai (Zhipu GLM) speaks the OpenAI chat.completions format, so no separate client code is needed |

`embed()` only supports `openai` and `zai` (both OpenAI-wire-compatible);
calling it with `github`/`anthropic` raises immediately (`ValueError`, not
retried) since neither has an embeddings endpoint usable here.

Each provider/model is configured independently in `.env` — see
`.env.example` for the full list (`LLM_PROVIDER`, `LLM_BASE_URL`,
`LLM_API_KEY`, `LLM_MODEL`; `GITHUB_CLI_PATH`, `GITHUB_MODEL`,
`GITHUB_CLI_TIMEOUT_SEC`; `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`,
`ANTHROPIC_MODEL`; `ZAI_API_KEY`, `ZAI_BASE_URL`, `ZAI_MODEL`,
`ZAI_EMBEDDING_MODEL`). `LLM_PROVIDER` picks the default used by plain
`kb extract run` (no `--provider` flag); the other three providers are
available on demand via `--provider`/`kb extract compare` without changing
the default.

> **Testing note**: only the `github` provider has been exercised with real,
> live API calls in this environment (the `copilot` CLI was already
> authenticated). The `openai`/`anthropic`/`zai` code paths follow each
> vendor's documented SDK/API conventions and are covered by unit tests with
> mocked clients, but have not been called against a live key here — no
> `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`ZAI_API_KEY` was configured in this
> sandbox. Verify with a small `kb extract compare <item_id> --providers
> openai` once you add real keys to `.env`.

### `extraction_run` — one row per (item, provider, model, prompt_version)

```sql
extraction_run(
  id, item_id, provider, model, prompt_version,
  status,        -- running | done | error
  error, summary, raw_response JSONB,
  started_at, finished_at, duration_ms
)
UNIQUE (item_id, provider, model, prompt_version)
```

Every extraction attempt — whether from plain `kb extract run` or an
exploratory `kb extract compare` — creates or updates one of these rows.
`view_market.extraction_run_id` and `prediction.extraction_run_id` now tag
*which run* produced each row (both `NOT NULL`), so an item can have
`view_market`/`prediction` rows from several providers simultaneously without
one overwriting another (re-running the *same* provider/model/prompt_version
combo is still idempotent: `_persist()` deletes/re-inserts only that run's
rows).

`item.primary_extraction_run_id` points at the run considered canonical for
that item — the one the frontend, `/api/items/<id>`, and the per-channel
`leaderboard_weekly` rollup use by default. Plain `kb extract run` always
promotes its result to primary (preserving the old single-version
behavior); `kb extract compare` never does, so exploratory runs can't
disturb the item's canonical reading. `PROMPT_VERSION` (currently `"v1"`,
in `extract.py`) should be bumped whenever `SYSTEM`/`SCHEMA` change
materially, so old and new prompt outputs for the same provider/model are
kept as distinct, comparable rows instead of one being silently discarded.

**Bug fix included in this change**: a failed extraction now sets
`item.extraction_status='error'` and `item.extraction_error` — previously
(see Part 1) a failure just logged and left the item `'pending'` forever,
indistinguishable from "not processed yet."

### `provider_model_leaderboard` — cross-model accuracy

```sql
provider_model_leaderboard(
  provider, model, channel_id,   -- channel_id NULL = "overall, all channels"
  n_calls, n_scored, avg_score, hit_rate, updated_at
)
UNIQUE NULLS NOT DISTINCT (provider, model, channel_id)
```

Populated by `leaderboard.rebuild_provider_model_leaderboard()` (called from
`leaderboard.rebuild()`), joining `prediction → extraction_run → item` and
computing the *same* `avg_score`/`hit_rate` formula as
`leaderboard_weekly`, just grouped by `(provider, model[, channel])` instead
of `(channel, week)`. Once enough predictions from multiple providers have
resolved, this answers "does `anthropic/claude-...` produce more accurate
calls than `openai/gpt-4o-mini` when reading the same author?" — a
model-accuracy leaderboard, not just an author leaderboard.

### CLI

```pwsh
# Extract with a specific provider/model instead of the .env default:
uv run kb extract run --limit 50 --provider anthropic --model claude-sonnet-4-5

# Run one item through several providers side by side, without touching
# its existing primary/canonical extraction:
uv run kb extract compare 133703 --providers openai,github,anthropic,zai

# List every extraction attempt recorded for an item (provider, model,
# status, view/prediction counts, timing, and which one is primary):
uv run kb extract runs 133703

# Cross-model rollup (also refreshed by the normal leaderboard rebuild):
uv run kb leaderboard rebuild
```

### API

```text
GET /api/items/<id>              # ?run_id=<n> to view a specific (non-primary)
                                  # run's market_views/predictions instead of
                                  # the item's canonical one; also now returns
                                  # an `extraction_runs` summary array.
                                  # `predictions` here are consolidated per
                                  # ticker (see "Within-article consolidation"
                                  # in Part 1) — one entry with quotes[] and a
                                  # conflict flag, not flat rows.
GET /api/items/<id>/runs         # every extraction_run for an item, with is_primary
GET /api/models/leaderboard      # provider/model accuracy, overall + per channel
```

### Practical cross-referencing workflow

1. Run normal extraction with your default provider:
   `uv run kb extract run --limit 200`.
2. Periodically spot-check a sample of items across providers:
   `uv run kb extract compare <item_id> --providers openai,github,anthropic`.
3. Once predictions have had time to resolve (see the horizon logic in
   Part 1), run `uv run kb leaderboard rebuild` and query
   `provider_model_leaderboard` (or `GET /api/models/leaderboard`) to see
   which provider's readings of the same articles produced better-scoring
   calls — then set `LLM_PROVIDER` in `.env` to whichever model is winning.
