"""Market-data pipeline: bulk-fetch daily prices into the `asset_price` store.

`sync()` discovers every ticker referenced by an extracted prediction, fetches
daily OHLCV from Yahoo Finance in batched `yf.download()` calls (politeness
pause between batches), and upserts into `asset_price`. Per-ticker sync state
lives in `asset_ticker` so incremental runs only top up the recent tail and
no-data tickers (LLM-hallucinated symbols like `DCGL`) are retried at most
once a week instead of every run.

Scoring (`leaderboard.py`) reads prices through `load_price_table()`, which
pulls the (small) store into memory once — no per-prediction network calls.
"""
from __future__ import annotations

import time
from bisect import bisect_left
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

import pandas as pd
from sqlalchemy import bindparam, text

from .db import engine
from .logging_setup import get_logger

log = get_logger("marketdata")

BATCH_SIZE = 40            # tickers per yf.download call
BATCH_PAUSE_SEC = 2.0      # politeness pause between batches
RETRY_HOURS = 24 * 7       # min hours between retries of no_data/error tickers
DAYS_BEFORE_FIRST_CALL = 7  # price history margin before a ticker's first call


def needed_tickers() -> dict[str, date]:
    """Distinct prediction tickers -> earliest day a price is needed
    (first call date minus a margin). Covers predictions from ALL extraction
    runs — provider-comparison runs are scored too, just never double-counted
    in rollups."""
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT p.ticker, MIN(p.made_at)::date - :margin AS first_day
            FROM prediction p
            WHERE p.ticker IS NOT NULL AND p.ticker <> ''
              AND p.made_at IS NOT NULL
            GROUP BY p.ticker
        """), {"margin": DAYS_BEFORE_FIRST_CALL}).all()
    return {t: d for t, d in rows if d}


def _f(v: Any) -> Optional[float]:
    """NaN-safe float conversion for pandas cell values."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _rows_for(sub: Any) -> list[dict[str, Any]]:
    """Extract (day, o, h, l, c, v) row dicts from one ticker's DataFrame,
    dropping days without a close."""
    if sub is None or (hasattr(sub, "empty") and sub.empty):
        return []
    sub = sub.dropna(subset=["Close"])
    out: list[dict[str, Any]] = []
    for idx, r in sub.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        out.append({
            "day": d,
            "open": _f(r.get("Open")),
            "high": _f(r.get("High")),
            "low": _f(r.get("Low")),
            "close": _f(r.get("Close")),
            "volume": int(v) if (v := _f(r.get("Volume"))) is not None else None,
        })
    return out


def _download_batch(tickers: Sequence[str], start: date, end: date) -> dict[str, Any]:
    """One yf.download call for a batch; returns {ticker: DataFrame} for the
    tickers that came back with data. Handles both the MultiIndex column
    layout (multiple tickers) and the flat one (single ticker)."""
    import yfinance as yf

    df = yf.download(
        tickers=list(tickers), start=start, end=end, interval="1d",
        group_by="ticker", auto_adjust=False, progress=False, threads=True,
    )
    out: dict[str, Any] = {}
    if df is None or (hasattr(df, "empty") and df.empty):
        return out
    if isinstance(df.columns, pd.MultiIndex):
        top = set(df.columns.get_level_values(0))
        for t in tickers:
            if t in top:
                sub = df[t]
                if sub is not None and not sub.empty and sub["Close"].notna().any():
                    out[t] = sub
    elif len(tickers) == 1:
        if df["Close"].notna().any():
            out[tickers[0]] = df
    return out


def _sync_state(tickers: Sequence[str]) -> dict[str, dict[str, Any]]:
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT ticker, status, first_day, last_day, n_days,
                   last_synced_at, last_error
            FROM asset_ticker WHERE ticker IN :ts
        """).bindparams(bindparam("ts", expanding=True)),
            {"ts": list(tickers)}).mappings().all()
    return {r["ticker"]: dict(r) for r in rows}


def _upsert_prices(ticker: str, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    params = [{"ticker": ticker, **r} for r in rows]
    with engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO asset_price (ticker, day, open, high, low, close, volume, fetched_at)
            VALUES (:ticker, :day, :open, :high, :low, :close, :volume, now())
            ON CONFLICT (ticker, day) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, volume = EXCLUDED.volume,
                fetched_at = now()
        """), params)


def _record_state(ticker: str, status: str, rows: Sequence[dict[str, Any]],
                  error: str | None = None) -> None:
    with engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO asset_ticker (ticker, status, first_day, last_day, n_days,
                                      last_synced_at, last_error)
            VALUES (:t, :st, :fd, :ld, :n, now(), :err)
            ON CONFLICT (ticker) DO UPDATE SET
                status = EXCLUDED.status,
                first_day = (SELECT MIN(day) FROM asset_price ap WHERE ap.ticker = EXCLUDED.ticker),
                last_day  = (SELECT MAX(day) FROM asset_price ap WHERE ap.ticker = EXCLUDED.ticker),
                n_days    = (SELECT COUNT(*) FROM asset_price ap WHERE ap.ticker = EXCLUDED.ticker),
                last_synced_at = now(),
                last_error = EXCLUDED.last_error
        """), {
            "t": ticker, "st": status,
            "fd": min((r["day"] for r in rows), default=None),
            "ld": max((r["day"] for r in rows), default=None),
            "n": len(rows), "err": error,
        })


def _batched(seq: Sequence[Any], n: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def sync(tickers: Sequence[str] | None = None, full: bool = False) -> dict[str, Any]:
    """Top up the price store. With `tickers` omitted, syncs every ticker
    referenced by predictions; with `full`, re-downloads complete history and
    retries no_data/error tickers regardless of back-off. Returns a stats dict
    for logging/CLI."""
    today = date.today()
    if tickers:
        need: dict[str, date] = {t: date(2020, 1, 1) for t in tickers}
    else:
        need = needed_tickers()
    if not need:
        return {"tickers": 0, "fetched": 0, "rows": 0, "skipped": 0,
                "no_data": [], "errors": []}

    state = _sync_state(list(need))
    cutoff = datetime.now().astimezone() - timedelta(hours=RETRY_HOURS)

    # Build (ticker, start_day) jobs: fresh tickers fetch from their first
    # needed day; previously-ok tickers only top up from their last day minus
    # a small overlap; failed tickers are skipped unless stale or --full.
    jobs: list[tuple[str, date]] = []
    skipped = 0
    for t, first in sorted(need.items()):
        st = state.get(t)
        if st and not full:
            fresh_fail = (
                st["status"] != "ok" and st["last_synced_at"]
                and st["last_synced_at"] > cutoff
            )
            fresh_ok = (
                st["status"] == "ok" and st["last_day"]
                and st["last_day"] >= today - timedelta(days=1)
            )
            if fresh_fail or fresh_ok:
                skipped += 1
                continue
        start = first
        if st and st["status"] == "ok" and st["last_day"]:
            start = min(first, st["last_day"] - timedelta(days=4))
        jobs.append((t, start))

    stats = {"tickers": len(need), "fetched": 0, "rows": 0, "skipped": skipped,
             "no_data": [], "errors": []}
    if not jobs:
        return stats

    # Group jobs into batches; within a batch the earliest start wins (the
    # range is small, so a little extra history costs nothing).
    jobs.sort(key=lambda j: j[1])
    for batch in _batched(jobs, BATCH_SIZE):
        names = [t for t, _ in batch]
        start = min(d for _, d in batch)
        try:
            data = _download_batch(names, start, today + timedelta(days=1))
        except Exception as exc:  # noqa: BLE001
            log.warning("batch failed (%s…): %s", names[0], exc)
            try:
                time.sleep(3.0)
                data = _download_batch(names, start, today + timedelta(days=1))
            except Exception as exc2:  # noqa: BLE001
                log.error("batch retry failed: %s", exc2)
                for t in names:
                    _record_state(t, "error", [], error=str(exc2)[:300])
                stats["errors"].extend(names)
                continue
        for t in names:
            rows = _rows_for(data.get(t))
            if rows:
                _upsert_prices(t, rows)
                _record_state(t, "ok", rows)
                stats["fetched"] += 1
                stats["rows"] += len(rows)
            else:
                _record_state(t, "no_data", [], error="no rows from Yahoo")
                stats["no_data"].append(t)
        time.sleep(BATCH_PAUSE_SEC)
    log.info("market sync: %d/%d tickers fetched, %d rows, %d skipped, "
             "%d no_data, %d errors",
             stats["fetched"], stats["tickers"], stats["rows"], stats["skipped"],
             len(stats["no_data"]), len(stats["errors"]))
    return stats


class PriceTable:
    """In-memory read of the price store for scoring: {ticker: ([days], [closes])}."""

    def __init__(self, data: dict[str, tuple[list[date], list[float]]]) -> None:
        self.data = data

    def on(self, ticker: str, dt: datetime) -> Optional[float]:
        """Close on the first trading day at/after `dt`; if none exists yet
        (e.g. today's close hasn't landed), the last close before it."""
        entry = self.data.get(ticker)
        if not entry:
            return None
        days, closes = entry
        d = dt.date() if isinstance(dt, datetime) else dt
        i = bisect_left(days, d)
        if i < len(days):
            return closes[i]
        return closes[-1] if days else None


def load_price_table() -> PriceTable:
    """Load every (ticker, day, close) into memory — the store is small
    (tickers × trading days since their first prediction)."""
    data: dict[str, tuple[list[date], list[float]]] = {}
    with engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT ticker, day, close FROM asset_price "
            "WHERE close IS NOT NULL ORDER BY ticker, day"
        )).all()
    for t, d, c in rows:
        entry = data.setdefault(t, ([], []))
        entry[0].append(d)
        entry[1].append(float(c))
    return PriceTable(data)


def series(ticker: str, date_from: str | None, date_to: str | None,
           max_points: int = 800) -> list[dict[str, Any]]:
    """Price history for the API (sparklines): [{day, close, volume}]."""
    clauses = ["ticker = :t"]
    params: dict[str, Any] = {"t": ticker}
    if date_from:
        clauses.append("day >= CAST(:df AS date)")
        params["df"] = date_from
    if date_to:
        clauses.append("day <= CAST(:dt AS date)")
        params["dt"] = date_to
    where = " AND ".join(clauses)
    with engine().connect() as conn:
        n = conn.execute(text(f"SELECT COUNT(*) FROM asset_price WHERE {where}"),
                         params).scalar() or 0
        step = max(1, (n + max_points - 1) // max_points) if n > max_points else 1
        rows = conn.execute(text(f"""
            SELECT day, close, volume FROM (
                SELECT day, close, volume,
                       row_number() OVER (ORDER BY day) AS rn
                FROM asset_price WHERE {where}
            ) x WHERE x.rn % :step = 0
            ORDER BY day
        """), {**params, "step": step}).all()
    return [{"day": str(d), "close": float(c) if c is not None else None,
             "volume": v} for d, c, v in rows]


def ticker_stats() -> list[dict[str, Any]]:
    """Ticker directory for the API: prediction counts + scores + sync state."""
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT st.ticker, st.asset_name, st.n_calls, st.n_scored,
                   st.avg_score, st.hit_rate, st.last_call_at,
                   t.status AS sync_status, t.n_days, t.first_day, t.last_day
            FROM (
                SELECT p.ticker,
                       (ARRAY_REMOVE(ARRAY_AGG(p.asset_name ORDER BY p.id), NULL))[1] AS asset_name,
                       COUNT(*) AS n_calls,
                       COUNT(p.score) AS n_scored,
                       AVG(p.score) AS avg_score,
                       AVG(CASE WHEN p.score>0 THEN 1.0 WHEN p.score<0 THEN 0.0 END) AS hit_rate,
                       MAX(p.made_at) AS last_call_at
                FROM prediction p
                WHERE p.ticker IS NOT NULL AND p.ticker <> ''
                GROUP BY p.ticker
            ) st
            LEFT JOIN asset_ticker t ON t.ticker = st.ticker
            ORDER BY st.n_calls DESC, st.ticker
        """)).mappings().all()
    return [dict(r) for r in rows]
