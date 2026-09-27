"""LLM-based extraction of structured views and predictions from items.

Each extraction attempt is recorded as an `extraction_run` row (one per
item/provider/model/prompt_version). This lets the same article be extracted
by several LLM providers so their `view_market`/`prediction` output — and
later, prediction accuracy scores — can be cross-referenced per model instead
of one provider's result silently overwriting another's. See
`doc/llm-extraction.md` for the full pipeline write-up.

The system prompt and JSON schema are NOT defined here: they live as
versioned file pairs under `src/kb/prompts/extraction/<version>/`
(system.md + schema.json), loaded by `kb.prompts`. The directory name is the
prompt_version recorded in `extraction_run`, so editing a prompt or schema
means adding a new version directory — never editing this module.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import NullPool

from . import llm
from . import prompts
from .config import settings
from .db import engine
from .llm import chat_json, embed
from .logging_setup import get_logger

log = get_logger("extract")


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
    default model. ``prompt_version`` defaults to the registry default
    (EXTRACTION_PROMPT_VERSION or the highest version under
    ``src/kb/prompts/extraction/``). ``make_primary`` decides whether this
    run becomes the item's canonical extraction (the one the
    API/frontend/leaderboard use by default); it defaults to True only when
    the run uses the configured default provider, so ad-hoc comparison runs
    (see ``compare_item``) don't disturb the existing canonical view unless
    asked to.
    """
    s = settings()
    provider = provider or s.llm_provider
    if provider == "jev":
        return _extract_jev(item_id, model=model)
    if provider not in llm.PROVIDERS:
        raise ValueError(f"unknown LLM provider {provider!r}; choose one of {llm.PROVIDERS}")
    model = model or llm.default_model(provider)
    pair = prompts.load(prompt_version)
    prompt_version = pair.version
    if make_primary is None:
        make_primary = provider == s.llm_provider

    with engine().begin() as conn:
        row = conn.execute(text(
            "SELECT id, title, content, language, published_at, channel_id, source_id "
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
                 "predictions": [], "entities": [],
                 "is_marketing": [], "media_mentions": [],
                 "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0}
    try:
        for i, chunk in enumerate(_chunks(row["content"])):
            prompt = (f"TITLE: {row['title']}\nDATE: {row['published_at']}\n"
                      f"LANGUAGE: {row['language']}\n\nTEXT:\n{chunk}")
            out, usage = chat_json(pair.system, prompt, pair.schema,
                                   provider=provider, model=model)
            if usage:
                for k in ("prompt_tokens", "cached_tokens", "completion_tokens"):
                    aggregate[k] += int(usage.get(k) or 0)
            if i == 0:
                aggregate["summary"] = out.get("summary", "")
            for k in ("speakers", "market_views", "predictions", "entities",
                      "media_mentions"):
                aggregate[k].extend(out.get(k, []) or [])
            # Whole-item flag: collect per-chunk votes; the item-level value
            # is decided by majority at promote time (a long video with one
            # sponsor-read chunk stays false).
            if isinstance(out.get("is_marketing"), bool):
                aggregate["is_marketing"].append(out["is_marketing"])
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
                    "UPDATE item SET extraction_status='error', extraction_error=:e "
                    "WHERE id=:i"),
                    {"e": err[:500], "i": item_id})
            try:
                from . import progress
                progress.mark_extracted(row["source_id"], "error")
            except Exception:  # noqa: BLE001
                log.debug("progress.mark_extracted(error) failed", exc_info=True)
        return None

    duration_ms = int((time.monotonic() - started) * 1000)
    _finish_run(run_id, status="done", summary=aggregate.get("summary", ""),
                raw_response=aggregate, duration_ms=duration_ms,
                prompt_tokens=aggregate["prompt_tokens"],
                cached_tokens=aggregate["cached_tokens"],
                completion_tokens=aggregate["completion_tokens"])
    _persist(run_id, item_id, row, aggregate)
    if make_primary:
        _promote_primary(item_id, run_id, aggregate)
        # Stamp the item's extraction timestamp and bump the per-source
        # progress counter. Best-effort: never abort a successful extract.
        try:
            from . import progress
            with engine().begin() as conn:
                conn.execute(text("UPDATE item SET extracted_at=now() WHERE id=:i"),
                             {"i": item_id})
            progress.mark_extracted(row["source_id"], "done")
        except Exception:  # noqa: BLE001
            log.debug("progress.mark_extracted(done) failed", exc_info=True)
    return aggregate


def _extract_jev(item_id: int, model: str | None = None) -> dict | None:
    """Record a Jev classification without replacing full LLM extraction."""
    from . import jev

    model = model or settings().jev_model
    with engine().begin() as conn:
        row = conn.execute(text("SELECT title, content FROM item WHERE id=:i"),
                           {"i": item_id}).mappings().first()
    if not row or not row["content"]:
        return None
    if not settings().jev_api_key:
        log.warning("JEV_API_KEY missing; skipping item %s", item_id)
        return None
    run_id = _start_run(item_id, "jev", model, jev.VERSION)
    started = time.monotonic()
    try:
        result, usage = jev.classify(f"TITLE: {row['title']}\n\n{row['content']}", model=model)
        _finish_run(run_id, "done", raw_response=result,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    prompt_tokens=usage["input_tokens"],
                    completion_tokens=usage["output_tokens"])
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("Jev error on item %s", item_id)
        _finish_run(run_id, "error", error=str(exc)[:2000],
                    duration_ms=int((time.monotonic() - started) * 1000))
        return None


def compare_item(item_id: int, combos: list[tuple[str, str | None]],
                  prompt_version: str | None = None) -> dict[str, dict[str, Any]]:
    """Extract the same item with several provider/model combos without
    touching the item's existing canonical (primary) extraction. ``combos``
    is a list of ``(provider, model_or_None)`` pairs — the same provider may
    appear multiple times with different models (a dict keyed by provider
    would silently collapse those). Returns per-combo stats keyed
    "provider/model"; the underlying rows remain in the DB (tagged by
    extraction_run) for deeper querying/leaderboard use.
    """
    pair = prompts.load(prompt_version)
    out: dict[str, dict[str, Any]] = {}
    for p, m in combos:
        if p == "jev":
            from .jev import VERSION
            m = m or settings().jev_model
            extract_item(item_id, provider=p, model=m, make_primary=False)
            out[f"{p}/{m}"] = _run_stats(item_id, p, m, VERSION)
            continue
        m = m or llm.default_model(p)
        extract_item(item_id, provider=p, model=m, make_primary=False,
                     prompt_version=pair.version)
        out[f"{p}/{m}"] = _run_stats(item_id, p, m, pair.version)
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


def _run_stats(item_id: int, provider: str, model: str,
               prompt_version: str) -> dict[str, Any]:
    with engine().connect() as conn:
        row = conn.execute(text("""
            SELECT id, status, error, summary, duration_ms,
                   prompt_tokens, cached_tokens, completion_tokens
            FROM extraction_run
            WHERE item_id=:i AND provider=:p AND model=:m AND prompt_version=:v
            ORDER BY id DESC LIMIT 1
        """), {"i": item_id, "p": provider, "m": model, "v": prompt_version}).mappings().first()
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
                 duration_ms: int | None = None, prompt_tokens: int | None = None,
                 cached_tokens: int | None = None,
                 completion_tokens: int | None = None) -> None:
    with engine().begin() as conn:
        conn.execute(text("""
            UPDATE extraction_run
            SET status=:st, summary=:sm, raw_response=CAST(:rr AS jsonb), error=:er,
                finished_at=now(), duration_ms=:d,
                prompt_tokens=:pt, cached_tokens=:ct, completion_tokens=:ot
            WHERE id=:r
        """), {"st": status, "sm": (summary or "")[:8000] if summary is not None else None,
               "rr": json.dumps(raw_response, ensure_ascii=False) if raw_response is not None else None,
               "er": error, "d": duration_ms, "r": run_id,
               "pt": prompt_tokens, "ct": cached_tokens, "ot": completion_tokens})


def _promote_primary(item_id: int, run_id: int, agg: dict[str, Any]) -> None:
    # Majority vote across chunk flags; no votes (v1-era schema) leaves the
    # item unclassified (NULL) rather than guessing false.
    flags = [f for f in (agg.get("is_marketing") or []) if isinstance(f, bool)]
    is_marketing = (sum(flags) * 2 > len(flags)) if flags else None
    with engine().begin() as conn:
        conn.execute(text("""
            UPDATE item SET summary=:s, extraction_status='done', extraction_error=NULL,
                            primary_extraction_run_id=:r,
                            is_marketing=CAST(:mk AS boolean)
            WHERE id=:i
        """), {"s": agg.get("summary", "")[:8000], "i": item_id, "r": run_id,
               "mk": is_marketing})


def _norm_title(title: str) -> str:
    """Canonical dedup key for a media work: drop parenthetical
    subtitle/translation suffixes BEFORE punctuation stripping ("The Big
    Short (華爾街大沽空)" -> "big short"), then lowercase, remove leading
    articles/punctuation, collapse whitespace — so localized/variant titles
    meet in one media_work row."""
    raw = re.split(r"\s*[(（【\[]", title.strip(), maxsplit=1)[0]
    if not raw:
        raw = title
    t = re.sub(r"[^\w\s]", " ", raw.lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(the|a|an) ", "", t)
    return t


def _persist(run_id: int, item_id: int, item_row, agg: dict) -> None:
    with engine().begin() as conn:
        # Scoped to this run only, so re-running the same (item, provider,
        # model, prompt_version) combo is idempotent without touching rows
        # from other providers/models extracted for the same item.
        conn.execute(text("""
            DELETE FROM view_market WHERE extraction_run_id=:r
        """), {"r": run_id})
        conn.execute(text("""
            DELETE FROM prediction WHERE extraction_run_id=:r
        """), {"r": run_id})
        conn.execute(text("""
            DELETE FROM media_mention WHERE extraction_run_id=:r
        """), {"r": run_id})
        for v in agg.get("market_views", []):
            # Free/smaller models occasionally emit a bare string instead of an
            # object in the array; skip malformed entries rather than aborting
            # the whole item's extraction.
            if not isinstance(v, dict):
                continue
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
            if not isinstance(p, dict):
                continue
            tk = (p.get("ticker") or "").strip().upper() or None
            if tk and not re.fullmatch(r"[A-Z0-9^.=:\-]{1,12}(\.[A-Z]{1,4})?", tk):
                # Not a Yahoo-style symbol — an asset/company-name fragment
                # the LLM put in the ticker field (e.g. "PAN MINE"). Keep the
                # text as the asset name; don't pollute the ticker column.
                if not (p.get("asset_name") or "").strip():
                    p["asset_name"] = tk.title()
                tk = None
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
            if not isinstance(e, dict):
                continue
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
        for m in agg.get("media_mentions", []):
            if not isinstance(m, dict):
                continue
            # glm-flash does not strictly honour the json_schema: it has been
            # seen renaming `kind` to `type` and emitting `creators` as a
            # list — normalize both before the enum guard drops the mention.
            kind = m.get("kind") or m.get("type")
            title = (m.get("title") or "").strip().strip("《》\"'“”‘’").strip()
            if kind not in ("book", "movie", "paper") or not title:
                continue
            creators = m.get("creators")
            if isinstance(creators, list):
                creators = ", ".join(str(c) for c in creators if c)
            norm = _norm_title(title)
            if not norm:
                continue
            # Upsert the canonical work: first-seen title/creators/year win,
            # later mentions only fill fields that are still empty.
            work_id = conn.execute(text("""
              INSERT INTO media_work(kind, title, title_norm, creators, year)
              VALUES (:k,:t,:n,:c,:y)
              ON CONFLICT (kind, title_norm) DO UPDATE SET
                    creators=COALESCE(NULLIF(media_work.creators,''), EXCLUDED.creators),
                    year=COALESCE(media_work.year, EXCLUDED.year)
              RETURNING id
            """), {"k": kind, "t": title[:500], "n": norm,
                   "c": (creators or "").strip() or None,
                   "y": m.get("year") if isinstance(m.get("year"), int) else None}
            ).scalar_one()
            conn.execute(text("""
              INSERT INTO media_mention(media_work_id, item_id, extraction_run_id,
                                        speaker, quote)
              VALUES (:w,:i,:r,:sp,:qu)
              ON CONFLICT (extraction_run_id, media_work_id) DO NOTHING
            """), {"w": work_id, "i": item_id, "r": run_id,
                   "sp": (m.get("speaker") or "").strip() or None,
                   "qu": (m.get("quote") or "").strip() or None})


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


def _jsonable(row: dict) -> dict:
    """Copy a DB row dict with datetime/Decimal values made JSON-safe."""
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def export_json(limit: int | None = None, source: str | None = None) -> int:
    """Write one JSON file per extracted item, next to its markdown file.

    Layout mirrors the markdown tree: ``data/<source>/<channel>/<year>/
    extracted_<md-stem>.json`` (the md stem keeps a 1:1 pairing even when two
    articles in a channel/year share a title). Idempotent: a file is
    rewritten only when the item's primary extraction run has changed — the
    file records its ``run_id``. Items whose md file is missing on disk are
    skipped (counted separately in the log). Note there is no GC: deleting an
    item in the DB leaves a stale JSON behind (the data/ tree is
    human-committed, so automated deletes could surprise).
    """
    from .config import ROOT

    # Static SQL (no dynamic filters); inlined into execute() rather than
    # bound to a variable. Source filtering and the limit are applied in
    # Python below — the row set is "every done item with an md path", small
    # relative to the corpus.
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT i.id, i.external_id, i.title, i.url, i.published_at, i.language,
                   i.duration_sec, i.md_path, i.summary, i.is_marketing,
                   s.code AS source_code, c.name AS channel_name,
                   er.id AS run_id, er.provider, er.model, er.prompt_version,
                   er.finished_at, er.duration_ms,
                   er.prompt_tokens, er.cached_tokens, er.completion_tokens
            FROM item i
            JOIN extraction_run er ON er.id = i.primary_extraction_run_id
            JOIN source s ON s.id = i.source_id
            JOIN channel c ON c.id = i.channel_id
            WHERE i.extraction_status = 'done'
              AND i.md_path IS NOT NULL
            ORDER BY i.published_at DESC NULLS LAST
        """)).mappings().all()
    if source:
        rows = [r for r in rows if r["source_code"] == source]
    # A few source quirks (macrovoices) have two item rows sharing one md
    # file; disambiguate those exports with the item id so neither silently
    # overwrites the other.
    path_counts = Counter(
        str((ROOT / r["md_path"]).with_name(f"extracted_{(ROOT / r['md_path']).stem}.json"))
        for r in rows)
    if limit:
        rows = rows[:limit]

    written = skipped = missing = 0
    for r in rows:
        md_path = ROOT / r["md_path"]
        if not md_path.exists():
            missing += 1
            continue
        out_path = md_path.with_name(f"extracted_{md_path.stem}.json")
        if path_counts[str(out_path)] > 1:
            out_path = md_path.with_name(f"extracted_{md_path.stem}-{r['id']}.json")
        if out_path.exists():
            try:
                with open(out_path, encoding="utf-8") as f:
                    prev = json.load(f)
                if prev.get("extraction", {}).get("run_id") == r["run_id"]:
                    skipped += 1
                    continue
            except (OSError, ValueError):
                pass  # unreadable/corrupt — rewrite it
        with engine().connect() as conn:
            views = [_jsonable(v) for v in conn.execute(text("""
                SELECT speaker, asset_class, region, direction, horizon,
                       confidence, rationale, quote
                FROM view_market WHERE extraction_run_id = :r
            """), {"r": r["run_id"]}).mappings().all()]
            preds = [_jsonable(p) for p in conn.execute(text("""
                SELECT speaker, ticker, asset_name, action, direction,
                       target_price, stop_price, timeframe, quote, made_at
                FROM prediction WHERE extraction_run_id = :r
            """), {"r": r["run_id"]}).mappings().all()]
            ents = [_jsonable(e) for e in conn.execute(text("""
                SELECT e.kind, e.name, e.ticker
                FROM item_entity ie JOIN entity e ON e.id = ie.entity_id
                WHERE ie.item_id = :i
            """), {"i": r["id"]}).mappings().all()]
            mentions = [_jsonable(m) for m in conn.execute(text("""
                SELECT w.kind, w.title, w.creators, w.year,
                       mm.speaker, mm.quote
                FROM media_mention mm JOIN media_work w ON w.id = mm.media_work_id
                WHERE mm.extraction_run_id = :r
            """), {"r": r["run_id"]}).mappings().all()]
        doc = {
            "schema": "kb-extract-export/v1",
            "item": {
                "id": r["id"], "source": r["source_code"],
                "channel": r["channel_name"], "external_id": r["external_id"],
                "title": r["title"], "url": r["url"],
                "published_at": r["published_at"].isoformat() if r["published_at"] else None,
                "language": r["language"], "duration_sec": r["duration_sec"],
                "md_path": r["md_path"],
            },
            "extraction": {
                "run_id": r["run_id"], "provider": r["provider"],
                "model": r["model"], "prompt_version": r["prompt_version"],
                "extracted_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                "duration_ms": r["duration_ms"],
                "prompt_tokens": r["prompt_tokens"],
                "cached_tokens": r["cached_tokens"],
                "completion_tokens": r["completion_tokens"],
                "is_marketing": r["is_marketing"],
                "summary": r["summary"],
            },
            "market_views": views,
            "predictions": preds,
            "entities": ents,
            "media_mentions": mentions,
        }
        out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        written += 1
    log.info("export: %d written, %d up-to-date, %d md-missing (of %d done items)",
             written, skipped, missing, len(rows))
    return written


def run(limit: int = 50, provider: str | None = None, model: str | None = None,
        prompt_version: str | None = None) -> int:
    provider = provider or settings().llm_provider
    n = 0
    # Single-flight guard: a local batch overlapping the Jenkins nightly would
    # otherwise upsert the same (item, provider, model, prompt_version)
    # extraction_run row and interleave the two batches' _persist
    # deletes/inserts, corrupting the predictions for any item both were
    # processing. The advisory lock is session-scoped and held on a dedicated
    # NullPool connection — closing it drops the session, which is what
    # releases the lock (also covers a crashed batch).
    lock_engine = create_engine(engine().url, poolclass=NullPool)
    lock_conn = lock_engine.connect()
    if not lock_conn.execute(
            select(func.pg_try_advisory_lock(7261001))).scalar_one():
        lock_conn.close()
        lock_engine.dispose()
        log.warning("another extraction batch is already running; exiting")
        return 0
    try:
        with engine().connect() as conn:
            if provider == "jev":
                from .jev import VERSION
                ids = [r[0] for r in conn.execute(text("""
                    SELECT i.id FROM item i
                    WHERE i.content IS NOT NULL AND i.content <> ''
                      AND NOT EXISTS (
                        SELECT 1 FROM extraction_run er
                        WHERE er.item_id=i.id AND er.provider='jev'
                          AND er.model=:m AND er.prompt_version=:v
                          AND er.status='done')
                    ORDER BY i.published_at DESC NULLS LAST LIMIT :l
                """), {"m": model or settings().jev_model, "v": VERSION, "l": limit})]
            else:
                ids = [r[0] for r in conn.execute(text(
                    "SELECT id FROM item WHERE extraction_status='pending' "
                    "ORDER BY published_at DESC NULLS LAST LIMIT :l"), {"l": limit})]
        for iid in ids:
            try:
                res = extract_item(iid, provider=provider, model=model,
                                   prompt_version=prompt_version)
                if res:
                    if provider != "jev":
                        try:
                            embed_chunks(iid)
                        except Exception as exc:
                            log.warning("embed failed for %s: %s", iid, exc)
                    n += 1
            except Exception as exc:  # noqa: BLE001
                log.exception("extract failed for %s: %s", iid, exc)
                if provider != "jev":
                    with engine().begin() as conn:
                        conn.execute(text("UPDATE item SET extraction_status='error', "
                                          "extraction_error=:e WHERE id=:i"),
                                     {"e": str(exc)[:500], "i": iid})
        log.info("extracted %d items", n)
        # Reconcile per-source progress counters from the item table as a safety
        # net against any increment drift during the batch. Cheap (one query per
        # source) and authoritative.
        if provider != "jev":
            try:
                from . import progress
                progress.recompute()
            except Exception:  # noqa: BLE001
                log.debug("progress.recompute after batch failed", exc_info=True)
    finally:
        lock_conn.close()
        lock_engine.dispose()
    return n

