"""Score predictions against the cached price store (`asset_price`).

Scoring model: a prediction's score is `sign × return × 5` clamped to
[-1, +1] — a 20% move in the called direction over the horizon earns the
full ±1. The evaluation window runs from `made_at` to `min(now, made_at +
horizon)`, so calls whose horizon hasn't elapsed yet carry an as-of-now
score that keeps refreshing on later rebuilds until the horizon passes and
the score freezes.

Prices come from the in-memory `marketdata.PriceTable` (bulk-fetched into
Postgres by `kb market sync`); scoring itself never touches the network.
Rollups (`leaderboard_weekly`, `leaderboard_speaker`,
`provider_model_leaderboard`) are rebuilt in full from the prediction table.
Speaker/channel rollups count **primary extraction runs only** so the same
call extracted by several provider-comparison runs isn't double-counted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from . import marketdata
from .db import engine
from .logging_setup import get_logger

log = get_logger("leaderboard")

# Same stance sets as the API's `_stance()` (api/main.py) so scoring,
# conflict badges and hit rates can't disagree about what a quote means.
BULLISH_ACTIONS = {"buy", "long", "cover"}
BEARISH_ACTIONS = {"sell", "short", "avoid"}


def _horizon_days(timeframe: str | None) -> int:
    if not timeframe:
        return 90
    s = timeframe.lower()
    if "day" in s or s.endswith("d"):
        return 7
    if "week" in s or "wk" in s:
        return 14
    if "month" in s or s.endswith("m"):
        return 90
    if "quarter" in s or "q" in s:
        return 90
    if "year" in s or s.endswith("y"):
        return 365
    return 90


def stance(direction: str | None, action: str | None) -> int:
    """+1 bullish, -1 bearish, 0 neutral/no-call."""
    d = (direction or "").strip().lower()
    a = (action or "").strip().lower()
    if d == "up" or a in BULLISH_ACTIONS:
        return 1
    if d == "down" or a in BEARISH_ACTIONS:
        return -1
    return 0


def compute_score(direction: str | None, action: str | None,
                  p_call: float | None, p_eval: float | None) -> float | None:
    """Pure scoring math (unit-tested): None = unscorable (no prices), 0.0 =
    scorable but a neutral stance (hold/watch)."""
    if p_call is None or p_eval is None or p_call == 0:
        return None
    sign = stance(direction, action)
    if sign == 0:
        return 0.0
    ret = (p_eval - p_call) / p_call
    return max(-1.0, min(1.0, sign * ret * 5.0))


def _final(row: Any) -> bool:
    """True when the prediction's score is frozen: it has been evaluated and
    its eval date has reached the end of the horizon."""
    if row["score"] is None or row["eval_at"] is None:
        return False
    return row["eval_at"] >= row["made_at"] + timedelta(
        days=_horizon_days(row["timeframe"]))


def score_all(rescore: bool = False,
              prices: marketdata.PriceTable | None = None) -> dict[str, int]:
    """Score every dated prediction with a ticker. By default only rows that
    are unscored or still inside their horizon (scores move with the market
    until the horizon passes); `rescore=True` rewrites everything. Scores
    predictions from ALL extraction runs — provider-comparison runs get their
    own scores for the model leaderboard; rollups de-duplicate to primary."""
    prices = prices or marketdata.load_price_table()
    if rescore:
        # Full wipe first: re-scoring must also clear rows that no longer
        # qualify (e.g. neutral calls after a scoring-model change).
        with engine().begin() as conn:
            conn.execute(text("""
                UPDATE prediction SET score=NULL, price_at_call=NULL,
                                      price_at_eval=NULL, eval_at=NULL
            """))
    with engine().connect() as conn:
        rows = list(conn.execute(text("""
            SELECT p.id, p.ticker, p.action, p.direction, p.timeframe,
                   p.made_at, p.score, p.eval_at
            FROM prediction p
            WHERE p.made_at IS NOT NULL
              AND p.ticker IS NOT NULL AND p.ticker <> ''
        """)).mappings())
    now = datetime.now(timezone.utc)
    updates: list[dict[str, Any]] = []
    no_price = 0
    neutral = 0
    for r in rows:
        if not rescore and _final(r):
            continue
        if stance(r["direction"], r["action"]) == 0:
            # Non-directional quotes (hold/watch/unspecified) aren't
            # forecasts — leave them unscored instead of writing 0.0, which
            # would drag every average toward zero and inflate n_scored.
            neutral += 1
            continue
        horizon = _horizon_days(r["timeframe"])
        eval_at = min(now, r["made_at"] + timedelta(days=horizon))
        p_call = prices.on(r["ticker"], r["made_at"])
        p_eval = prices.on(r["ticker"], eval_at) if p_call is not None else None
        score = compute_score(r["direction"], r["action"], p_call, p_eval)
        if score is None:
            no_price += 1
            continue
        updates.append({
            "id": r["id"], "pc": p_call, "pe": p_eval,
            "ev": eval_at, "sc": score,
        })
    if updates:
        with engine().begin() as conn:
            conn.execute(text("""
                UPDATE prediction SET price_at_call=:pc, price_at_eval=:pe,
                                      eval_at=:ev, score=:sc WHERE id=:id
            """), updates)
    log.info("scored %d/%d predictions (%d unscorable — no price data, "
             "%d neutral skipped)",
             len(updates), len(rows), no_price, neutral)
    return {"candidates": len(rows), "scored": len(updates),
            "no_price": no_price, "neutral": neutral}


def _rebuild_weekly() -> None:
    with engine().begin() as conn:
        conn.execute(text("""
            DELETE FROM leaderboard_weekly
        """))
        conn.execute(text("""
          INSERT INTO leaderboard_weekly (channel_id, week_start, n_calls,
                                          n_scored, avg_score, hit_rate)
          SELECT i.channel_id,
                 date_trunc('week', p.made_at)::date AS wk,
                 COUNT(*) AS n_calls,
                 COUNT(p.score) AS n_scored,
                 AVG(p.score) AS avg_score,
                 AVG(CASE WHEN p.score > 0 THEN 1.0
                          WHEN p.score < 0 THEN 0.0 END) AS hit_rate
          FROM prediction p
          JOIN item i ON i.id = p.item_id
          WHERE i.channel_id IS NOT NULL AND p.made_at IS NOT NULL
            AND p.extraction_run_id = i.primary_extraction_run_id
          GROUP BY i.channel_id, wk
        """))


def _rebuild_speakers() -> None:
    """Roll scores up to the person making the call (interviewee/author),
    across every channel they appear on; `main_channel_id` is where they
    appear most often."""
    with engine().begin() as conn:
        conn.execute(text("""
            DELETE FROM leaderboard_speaker
        """))
        conn.execute(text("""
          WITH per AS (
              SELECT p.speaker, p.score, p.made_at, i.channel_id
              FROM prediction p
              JOIN item i ON i.id = p.item_id
              WHERE p.speaker IS NOT NULL AND p.speaker <> ''
                AND p.made_at IS NOT NULL
                AND p.extraction_run_id = i.primary_extraction_run_id
          ), agg AS (
              SELECT speaker, COUNT(*) AS n_calls,
                     COUNT(score) AS n_scored,
                     AVG(score) AS avg_score,
                     AVG(CASE WHEN score > 0 THEN 1.0
                              WHEN score < 0 THEN 0.0 END) AS hit_rate,
                     MAX(made_at) AS last_call_at
              FROM per GROUP BY speaker
          ), mainch AS (
              SELECT speaker, channel_id FROM (
                  SELECT speaker, channel_id,
                         ROW_NUMBER() OVER (
                             PARTITION BY speaker
                             ORDER BY COUNT(*) DESC, MAX(made_at) DESC) AS rn
                  FROM per WHERE channel_id IS NOT NULL
                  GROUP BY speaker, channel_id
              ) x WHERE rn = 1
          )
          INSERT INTO leaderboard_speaker (speaker, main_channel_id, n_calls,
                                           n_scored, avg_score, hit_rate,
                                           last_call_at, updated_at)
          SELECT agg.speaker, mainch.channel_id, agg.n_calls, agg.n_scored,
                 agg.avg_score, agg.hit_rate, agg.last_call_at, now()
          FROM agg LEFT JOIN mainch ON mainch.speaker = agg.speaker
        """))


def rebuild_provider_model_leaderboard() -> None:
    """Roll up prediction scores by (provider, model) — and, separately, by
    (provider, model, channel) — so accuracy can be cross-referenced across
    the LLMs used to extract the same underlying articles. Deliberately uses
    predictions from EVERY run (that's the point: comparing extractions of
    the same article), unlike the channel/speaker rollups. See
    `doc/llm-extraction.md` for how to read this."""
    with engine().begin() as conn:
        conn.execute(text("""
            DELETE FROM provider_model_leaderboard
        """))
        # Per channel: "which model is most accurate at reading *this* author?"
        conn.execute(text("""
          INSERT INTO provider_model_leaderboard (provider, model, channel_id,
                                                   n_calls, n_scored, avg_score, hit_rate)
          SELECT er.provider, er.model, i.channel_id,
                 COUNT(*) AS n_calls,
                 COUNT(p.score) AS n_scored,
                 AVG(p.score) AS avg_score,
                 AVG(CASE WHEN p.score > 0 THEN 1.0
                          WHEN p.score < 0 THEN 0.0 END) AS hit_rate
          FROM prediction p
          JOIN extraction_run er ON er.id = p.extraction_run_id
          JOIN item i ON i.id = p.item_id
          WHERE p.made_at IS NOT NULL AND i.channel_id IS NOT NULL
          GROUP BY er.provider, er.model, i.channel_id
        """))
        # Overall (channel_id NULL): "which model is most accurate overall?"
        conn.execute(text("""
          INSERT INTO provider_model_leaderboard (provider, model, channel_id,
                                                   n_calls, n_scored, avg_score, hit_rate)
          SELECT er.provider, er.model, NULL,
                 COUNT(*) AS n_calls,
                 COUNT(p.score) AS n_scored,
                 AVG(p.score) AS avg_score,
                 AVG(CASE WHEN p.score > 0 THEN 1.0
                          WHEN p.score < 0 THEN 0.0 END) AS hit_rate
          FROM prediction p
          JOIN extraction_run er ON er.id = p.extraction_run_id
          WHERE p.made_at IS NOT NULL
          GROUP BY er.provider, er.model
        """))


def rebuild(rescore: bool = False, sync_prices: bool = True) -> dict[str, Any]:
    """Sync prices, score predictions, rebuild all rollup tables. Nightly
    stage (Jenkins); safe to run any time."""
    sync_stats: dict[str, Any] = {}
    if sync_prices:
        sync_stats = marketdata.sync()
    score_stats = score_all(rescore=rescore)
    _rebuild_weekly()
    _rebuild_speakers()
    rebuild_provider_model_leaderboard()
    stats = {"sync": sync_stats, "scoring": score_stats}
    log.info("leaderboard rebuild: %s", stats)
    return stats
