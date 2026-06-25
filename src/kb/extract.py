"""LLM-based extraction of structured views and predictions from items."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text

from .config import settings
from .db import engine
from .llm import chat_json, embed
from .logging_setup import get_logger

log = get_logger("extract")


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
    text_in = text_in.strip()
    if len(text_in) <= max_chars:
        return [text_in]
    paras = re.split(r"\n{2,}", text_in)
    out, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 2 > max_chars:
            if buf:
                out.append(buf)
            buf = p
        else:
            buf = (buf + "\n\n" + p).strip()
    if buf:
        out.append(buf)
    return out


def extract_item(item_id: int) -> dict | None:
    with engine().begin() as conn:
        row = conn.execute(text(
            "SELECT id, title, content, language, published_at, channel_id "
            "FROM item WHERE id=:i"), {"i": item_id}).mappings().first()
    if not row or not row["content"]:
        log.info("skip empty item %s", item_id)
        return None
    if not settings().llm_api_key:
        log.warning("no LLM_API_KEY; skipping extract for %s", item_id)
        return None

    aggregate = {"summary": "", "speakers": [], "market_views": [],
                 "predictions": [], "entities": []}
    for i, chunk in enumerate(_chunks(row["content"])):
        prompt = (f"TITLE: {row['title']}\nDATE: {row['published_at']}\n"
                  f"LANGUAGE: {row['language']}\n\nTEXT:\n{chunk}")
        try:
            out = chat_json(SYSTEM, prompt, SCHEMA)
        except Exception as exc:  # noqa: BLE001
            log.exception("LLM error on item %s chunk %s: %s", item_id, i, exc)
            return None
        if i == 0:
            aggregate["summary"] = out.get("summary", "")
        for k in ("speakers", "market_views", "predictions", "entities"):
            aggregate[k].extend(out.get(k, []) or [])

    _persist(item_id, row, aggregate)
    return aggregate


def _persist(item_id: int, item_row, agg: dict) -> None:
    with engine().begin() as conn:
        conn.execute(text("UPDATE item SET summary=:s, extraction_status='done', "
                          "extraction_error=NULL WHERE id=:i"),
                     {"s": agg.get("summary", "")[:8000], "i": item_id})
        conn.execute(text("DELETE FROM view_market WHERE item_id=:i"), {"i": item_id})
        conn.execute(text("DELETE FROM prediction  WHERE item_id=:i"), {"i": item_id})
        for v in agg.get("market_views", []):
            conn.execute(text("""
              INSERT INTO view_market(item_id, speaker, asset_class, region,
                                      direction, horizon, confidence, rationale, quote)
              VALUES (:i,:sp,:ac,:re,:di,:ho,:co,:ra,:qu)
            """), {"i": item_id,
                   "sp": v.get("speaker"), "ac": v.get("asset_class"),
                   "re": v.get("region"), "di": v.get("direction"),
                   "ho": v.get("horizon"), "co": v.get("confidence"),
                   "ra": v.get("rationale"), "qu": v.get("quote")})
        for p in agg.get("predictions", []):
            tk = (p.get("ticker") or "").strip().upper() or None
            conn.execute(text("""
              INSERT INTO prediction(item_id, speaker, ticker, asset_name, action,
                                     direction, target_price, stop_price, timeframe,
                                     quote, made_at)
              VALUES (:i,:sp,:tk,:an,:ac,:di,:tp,:st,:tf,:qu,:ma)
            """), {"i": item_id, "sp": p.get("speaker"), "tk": tk,
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
    if not settings().llm_api_key:
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


def run(limit: int = 50) -> int:
    n = 0
    with engine().connect() as conn:
        ids = [r[0] for r in conn.execute(text(
            "SELECT id FROM item WHERE extraction_status='pending' "
            "ORDER BY published_at DESC NULLS LAST LIMIT :l"), {"l": limit})]
    for iid in ids:
        try:
            res = extract_item(iid)
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
