"""Score predictions against subsequent market prices using yfinance."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from .db import engine
from .logging_setup import get_logger

log = get_logger("leaderboard")


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


def _price_on(ticker: str, dt: datetime) -> Optional[float]:
    import yfinance as yf
    end = dt + timedelta(days=5)
    start = dt - timedelta(days=5)
    try:
        df = yf.download(ticker, start=start.date(), end=end.date(),
                         progress=False, auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        log.info("yf err %s %s: %s", ticker, dt, exc)
        return None
    if df is None or df.empty:
        return None
    # find first row on/after dt
    df = df.sort_index()
    after = df[df.index >= pd.Timestamp(dt.date())]
    pick = after.iloc[0] if not after.empty else df.iloc[-1]
    val = pick["Close"]
    if hasattr(val, "iloc"):
        val = val.iloc[0]
    try:
        return float(val)
    except Exception:
        return None


def score_prediction(p) -> float | None:
    if not p["ticker"] or not p["made_at"]:
        return None
    horizon = _horizon_days(p["timeframe"])
    eval_at = datetime.utcnow()
    target_eval = p["made_at"] + timedelta(days=horizon)
    if target_eval < eval_at:
        eval_at = target_eval
    p_call = _price_on(p["ticker"], p["made_at"])
    p_eval = _price_on(p["ticker"], eval_at)
    if p_call is None or p_eval is None or p_call == 0:
        return None
    ret = (p_eval - p_call) / p_call
    direction = (p["direction"] or "").lower()
    action = (p["action"] or "").lower()
    sign = 0
    if direction == "up" or action in {"buy", "long"}:
        sign = +1
    elif direction == "down" or action in {"short", "sell"}:
        sign = -1
    if sign == 0:
        score = 0.0
    else:
        score = max(-1.0, min(1.0, sign * ret * 5))   # 20% move = full score
    with engine().begin() as conn:
        conn.execute(text("""
          UPDATE prediction SET price_at_call=:pc, price_at_eval=:pe,
                                eval_at=:ev, score=:sc WHERE id=:id
        """), {"pc": p_call, "pe": p_eval, "ev": eval_at, "sc": score, "id": p["id"]})
    return score


def rebuild() -> None:
    with engine().connect() as conn:
        rows = list(conn.execute(text(
            "SELECT id, ticker, action, direction, timeframe, made_at "
            "FROM prediction WHERE made_at IS NOT NULL")).mappings())
    log.info("scoring %d predictions", len(rows))
    for r in rows:
        try:
            score_prediction(r)
        except Exception as exc:  # noqa: BLE001
            log.warning("score failed for %s: %s", r["id"], exc)

    # roll up weekly per channel
    with engine().begin() as conn:
        conn.execute(text("DELETE FROM leaderboard_weekly"))
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
          GROUP BY i.channel_id, wk
        """))

    rebuild_provider_model_leaderboard()


def rebuild_provider_model_leaderboard() -> None:
    """Roll up prediction scores by (provider, model) — and, separately, by
    (provider, model, channel) — so accuracy can be cross-referenced across
    the LLMs used to extract the same underlying articles. See
    `doc/llm-extraction.md` for how to read this.
    """
    with engine().begin() as conn:
        conn.execute(text("DELETE FROM provider_model_leaderboard"))
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
