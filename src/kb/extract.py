"""LLM-based extraction of structured views and predictions from items.

Each extraction attempt is recorded as an `extraction_run` row (one per
item/provider/model/prompt_version). This lets the same article be extracted
by several LLM providers so their `view_market`/`prediction` output — and
later, prediction accuracy scores — can be cross-referenced per model instead
of one provider's result silently overwriting another's. See
`doc/llm-extraction.md` for the full pipeline write-up.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import text

from . import llm
from .config import settings
from .db import engine
from .llm import chat_json, embed
from .logging_setup import get_logger

log = get_logger("extract")

# Bump this when SYSTEM/SCHEMA change materially, so old and new extractions
# of the same item/provider/model are tracked as distinct extraction_run rows
# instead of one silently overwriting the other.
PROMPT_VERSION = "v1"


SYSTEM = """You are a careful financial analyst. From a transcript or article,
extract: (1) the speaker/author's broad market views, (2) any specific
predictions about tickers / assets, (3) any tradable buy/sell calls. Use the
exact words from the source as 'quote'. Do NOT invent. If something is not
in the text, leave the field empty / null. Tickers should be Yahoo-Finance
style (e.g. AAPL, ES=F, GC=F, ^GSPC, BTC-USD). Output strict JSON per the
schema."""


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "speakers", "market_views", "predictions", "entities"],
    "properties": {
        "summary": {"type": "string"},
        "speakers": {"type": "array", "items": {"type": "string"}},
        "market_views": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["speaker", "asset_class", "direction", "horizon",
                             "rationale", "quote"],
                "properties": {
                    "speaker": {"type": "string"},
                    "asset_class": {"type": "string"},
                    "region": {"type": "string"},
                    "direction": {"type": "string",
                                  "enum": ["bullish", "bearish", "neutral", "mixed"]},
                    "horizon": {"type": "string",
                                "enum": ["short", "medium", "long", "unspecified"]},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        },
        "predictions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["speaker", "ticker", "asset_name", "action",
                             "direction", "timeframe", "quote"],
                "properties": {
                    "speaker": {"type": "string"},
                    "ticker": {"type": "string"},
                    "asset_name": {"type": "string"},
                    "action": {"type": "string",
                               "enum": ["buy", "sell", "short", "hold", "watch",
                                       "long", "cover", "avoid", "none"]},
                    "direction": {"type": "string",
                                  "enum": ["up", "down", "flat", "unspecified"]},
                    "target_price": {"type": ["number", "null"]},
                    "stop_price": {"type": ["number", "null"]},
                    "timeframe": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        },
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "name"],
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["person", "company", "country", "theme"]},
                    "name": {"type": "string"},
                    "ticker": {"type": "string"},
                },
            },
        },
    },
}


def _chunks(text_in: str, max_chars: int = 14000) -> list[str]:
    """Split text into pieces of at most ``max_chars``, breaking on blank
    lines (paragraphs) where possible.

    Guarantees every returned chunk is <= ``max_chars``: a naive split on
    blank lines alone can still yield an oversized chunk when the source has
    one giant unbroken paragraph (e.g. a YouTube transcript with no blank
    lines at all) -- that's not just a cost/context-window hygiene issue, it
    can also blow past the OS's command-line length limit for the `github`
    provider, which passes the chunk as a CLI argument (see ``llm.py``).
    """
    text_in = text_in.strip()
    if len(text_in) <= max_chars:
        return [text_in]
    paras = re.split(r"\n{2,}", text_in)
    out, buf = [], ""
    for p in paras:
        if len(p) > max_chars:
            # This single paragraph is already too big on its own -- flush
            # anything buffered, then wrap it independently so it can never
            # itself become (or blow up) a chunk larger than max_chars.
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_wrap(p, max_chars))
            continue
        if len(buf) + len(p) + 2 > max_chars:
            if buf:
                out.append(buf)
            buf = p
        else:
            buf = (buf + "\n\n" + p).strip()
    if buf:
        out.append(buf)
    return out


def _wrap(text_in: str, max_chars: int) -> list[str]:
    """Split ``text_in`` into pieces no longer than ``max_chars``, preferring
    to break on whitespace. Falls back to a hard character slice for a
    single "word" longer than ``max_chars`` on its own (e.g. CJK text, which
    has no spaces between words -- common in this project's HKEJ/Yahoo
    HK/Master Insight/YouTube-transcript content)."""
    words = text_in.split(" ")
    out, buf = [], ""
    for w in words:
        if len(w) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(w[i:i + max_chars] for i in range(0, len(w), max_chars))
            continue
        if len(buf) + len(w) + 1 > max_chars:
            if buf:
                out.append(buf)
            buf = w
        else:
            buf = (buf + " " + w).strip()
    if buf:
        out.append(buf)
    return out


def extract_item(item_id: int, provider: str | None = None, model: str | None = None,
                  prompt_version: str | None = None, make_primary: bool | None = None) -> dict | None:
    """Run one extraction attempt for an item and persist the result.

    ``provider``/``model`` default to the configured LLM_PROVIDER and its
    default model. ``make_primary`` decides whether this run becomes the
    item's canonical extraction (the one the API/frontend/leaderboard use by
    default); it defaults to True only when the run uses the configured
    default provider, so ad-hoc comparison runs (see ``compare_item``) don't
    disturb the existing canonical view unless asked to.
    """
    s = settings()
    provider = provider or s.llm_provider
    if provider not in llm.PROVIDERS:
        raise ValueError(f"unknown LLM provider {provider!r}; choose one of {llm.PROVIDERS}")
    model = model or llm.default_model(provider)
    prompt_version = prompt_version or PROMPT_VERSION
    if make_primary is None:
        make_primary = provider == s.llm_provider

    with engine().begin() as conn:
        row = conn.execute(text(
            "SELECT id, title, content, language, published_at, channel_id "
            "FROM item WHERE id=:i"), {"i": item_id}).mappings().first()
    if not row or not row["content"]:
        log.info("skip empty item %s", item_id)
        return None
    if not llm.has_credentials(provider):
        log.warning("no credentials for provider %s; skipping extract for %s", provider, item_id)
        return None

    run_id = _start_run(item_id, provider, model, prompt_version)
    started = time.monotonic()
    aggregate = {"summary": "", "speakers": [], "market_views": [],
                 "predictions": [], "entities": []}
    try:
        for i, chunk in enumerate(_chunks(row["content"])):
            prompt = (f"TITLE: {row['title']}\nDATE: {row['published_at']}\n"
                      f"LANGUAGE: {row['language']}\n\nTEXT:\n{chunk}")
            out = chat_json(SYSTEM, prompt, SCHEMA, provider=provider, model=model)
            if i == 0:
                aggregate["summary"] = out.get("summary", "")
            for k in ("speakers", "market_views", "predictions", "entities"):
                aggregate[k].extend(out.get(k, []) or [])
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.monotonic() - started) * 1000)
        err = str(exc)[:2000]
        log.exception("LLM error on item %s via %s/%s: %s", item_id, provider, model, exc)
        _finish_run(run_id, status="error", error=err, duration_ms=duration_ms)
        if make_primary:
            # Previously a failed extraction left the item silently 'pending'
            # forever with no record of why. Now it's surfaced as 'error'.
            with engine().begin() as conn:
                conn.execute(text(
                    "UPDATE item SET extraction_status='error', extraction_error=:e WHERE id=:i"),
                    {"e": err[:500], "i": item_id})
        return None

    duration_ms = int((time.monotonic() - started) * 1000)
    _finish_run(run_id, status="done", summary=aggregate.get("summary", ""),
                raw_response=aggregate, duration_ms=duration_ms)
    _persist(run_id, item_id, row, aggregate)
    if make_primary:
        _promote_primary(item_id, run_id, aggregate)
    return aggregate


def compare_item(item_id: int, providers: list[str],
                  models: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Extract the same item with several providers without touching the
    item's existing canonical (primary) extraction. Returns per-provider
    stats for a quick side-by-side comparison; the underlying rows remain in
    the DB (tagged by extraction_run) for deeper querying/leaderboard use.
    """
    models = models or {}
    out: dict[str, dict[str, Any]] = {}
    for p in providers:
        m = models.get(p) or llm.default_model(p)
        extract_item(item_id, provider=p, model=m, make_primary=False)
        out[p] = _run_stats(item_id, p, m)
    return out


def list_runs(item_id: int) -> list[dict[str, Any]]:
    """All extraction_run rows for an item, most recent first."""
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT er.*, (er.id = i.primary_extraction_run_id) AS is_primary,
                   (SELECT COUNT(*) FROM view_market WHERE extraction_run_id = er.id) AS n_market_views,
                   (SELECT COUNT(*) FROM prediction WHERE extraction_run_id = er.id) AS n_predictions
            FROM extraction_run er
            JOIN item i ON i.id = er.item_id
            WHERE er.item_id = :i
            ORDER BY er.id DESC
        """), {"i": item_id}).mappings().all()
    return [dict(r) for r in rows]


def _run_stats(item_id: int, provider: str, model: str) -> dict[str, Any]:
    with engine().connect() as conn:
        row = conn.execute(text("""
            SELECT id, status, error, summary, duration_ms
            FROM extraction_run WHERE item_id=:i AND provider=:p AND model=:m
            ORDER BY id DESC LIMIT 1
        """), {"i": item_id, "p": provider, "m": model}).mappings().first()
        if not row:
            return {"status": "error", "error": "extraction did not run (no credentials / empty item?)"}
        n_views = conn.execute(text(
            "SELECT COUNT(*) FROM view_market WHERE extraction_run_id=:r"), {"r": row["id"]}).scalar_one()
        n_preds = conn.execute(text(
            "SELECT COUNT(*) FROM prediction WHERE extraction_run_id=:r"), {"r": row["id"]}).scalar_one()
    return {**dict(row), "n_market_views": n_views, "n_predictions": n_preds}


def _start_run(item_id: int, provider: str, model: str, prompt_version: str) -> int:
    with engine().begin() as conn:
        return conn.execute(text("""
            INSERT INTO extraction_run (item_id, provider, model, prompt_version, status)
            VALUES (:i, :p, :m, :v, 'running')
            ON CONFLICT (item_id, provider, model, prompt_version)
            DO UPDATE SET status='running', error=NULL, finished_at=NULL, started_at=now()
            RETURNING id
        """), {"i": item_id, "p": provider, "m": model, "v": prompt_version}).scalar_one()


def _finish_run(run_id: int, status: str, summary: str | None = None,
                 raw_response: dict[str, Any] | None = None, error: str | None = None,
                 duration_ms: int | None = None) -> None:
    with engine().begin() as conn:
        conn.execute(text("""
            UPDATE extraction_run
            SET status=:st, summary=:sm, raw_response=CAST(:rr AS jsonb), error=:er,
                finished_at=now(), duration_ms=:d
            WHERE id=:r
        """), {"st": status, "sm": (summary or "")[:8000] if summary is not None else None,
               "rr": json.dumps(raw_response, ensure_ascii=False) if raw_response is not None else None,
               "er": error, "d": duration_ms, "r": run_id})


def _promote_primary(item_id: int, run_id: int, agg: dict[str, Any]) -> None:
    with engine().begin() as conn:
        conn.execute(text("""
            UPDATE item SET summary=:s, extraction_status='done', extraction_error=NULL,
                            primary_extraction_run_id=:r
            WHERE id=:i
        """), {"s": agg.get("summary", "")[:8000], "i": item_id, "r": run_id})


def _persist(run_id: int, item_id: int, item_row, agg: dict) -> None:
    with engine().begin() as conn:
        # Scoped to this run only, so re-running the same (item, provider,
        # model, prompt_version) combo is idempotent without touching rows
        # from other providers/models extracted for the same item.
        conn.execute(text("DELETE FROM view_market WHERE extraction_run_id=:r"), {"r": run_id})
        conn.execute(text("DELETE FROM prediction  WHERE extraction_run_id=:r"), {"r": run_id})
        for v in agg.get("market_views", []):
            conn.execute(text("""
              INSERT INTO view_market(item_id, extraction_run_id, speaker, asset_class, region,
                                      direction, horizon, confidence, rationale, quote)
              VALUES (:i,:r,:sp,:ac,:re,:di,:ho,:co,:ra,:qu)
            """), {"i": item_id, "r": run_id,
                   "sp": v.get("speaker"), "ac": v.get("asset_class"),
                   "re": v.get("region"), "di": v.get("direction"),
                   "ho": v.get("horizon"), "co": v.get("confidence"),
                   "ra": v.get("rationale"), "qu": v.get("quote")})
        for p in agg.get("predictions", []):
            tk = (p.get("ticker") or "").strip().upper() or None
            conn.execute(text("""
              INSERT INTO prediction(item_id, extraction_run_id, speaker, ticker, asset_name, action,
                                     direction, target_price, stop_price, timeframe,
                                     quote, made_at)
              VALUES (:i,:r,:sp,:tk,:an,:ac,:di,:tp,:st,:tf,:qu,:ma)
            """), {"i": item_id, "r": run_id, "sp": p.get("speaker"), "tk": tk,
                   "an": p.get("asset_name"), "ac": p.get("action"),
                   "di": p.get("direction"),
                   "tp": p.get("target_price"), "st": p.get("stop_price"),
                   "tf": p.get("timeframe"), "qu": p.get("quote"),
                   "ma": item_row["published_at"]})
        for e in agg.get("entities", []):
            kind = e.get("kind") or "theme"
            name = (e.get("name") or "").strip()
            if not name:
                continue
            ent_id = conn.execute(text("""
              INSERT INTO entity(kind,name,ticker) VALUES (:k,:n,:t)
              ON CONFLICT (kind,name) DO UPDATE SET ticker=COALESCE(EXCLUDED.ticker, entity.ticker)
              RETURNING id
            """), {"k": kind, "n": name, "t": e.get("ticker")}).scalar_one()
            conn.execute(text("""
              INSERT INTO item_entity(item_id,entity_id,weight) VALUES (:i,:e,1.0)
              ON CONFLICT DO NOTHING
            """), {"i": item_id, "e": ent_id})


def embed_chunks(item_id: int, max_chars: int = 1800) -> int:
    with engine().begin() as conn:
        row = conn.execute(text("SELECT content FROM item WHERE id=:i"),
                           {"i": item_id}).first()
    if not row or not row[0]:
        return 0
    chunks = _chunks(row[0], max_chars=max_chars)
    if not llm.has_credentials(settings().llm_embedding_provider):
        return 0
    vecs = embed(chunks)
    with engine().begin() as conn:
        conn.execute(text("DELETE FROM chunk WHERE item_id=:i"), {"i": item_id})
        for i, (t, v) in enumerate(zip(chunks, vecs)):
            conn.execute(text("INSERT INTO chunk(item_id, idx, text, embedding) "
                              "VALUES (:i,:idx,:t,:e)"),
                         {"i": item_id, "idx": i, "t": t,
                          "e": "[" + ",".join(f"{x:.6f}" for x in v) + "]"})
    return len(chunks)


def run(limit: int = 50, provider: str | None = None, model: str | None = None) -> int:
    n = 0
    with engine().connect() as conn:
        ids = [r[0] for r in conn.execute(text(
            "SELECT id FROM item WHERE extraction_status='pending' "
            "ORDER BY published_at DESC NULLS LAST LIMIT :l"), {"l": limit})]
    for iid in ids:
        try:
            res = extract_item(iid, provider=provider, model=model)
            if res:
                try:
                    embed_chunks(iid)
                except Exception as exc:
                    log.warning("embed failed for %s: %s", iid, exc)
                n += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("extract failed for %s: %s", iid, exc)
            with engine().begin() as conn:
                conn.execute(text("UPDATE item SET extraction_status='error', "
                                  "extraction_error=:e WHERE id=:i"),
                             {"e": str(exc)[:500], "i": iid})
    log.info("extracted %d items", n)
    return n

