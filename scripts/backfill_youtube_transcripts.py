#!/usr/bin/env python3
"""Backfill YouTube subtitles for items whose markdown has no transcript.

Targets the scar left by the 2026-07/08 bulk scrapes: YouTube's caption
endpoint (timedtext) rate-limits hard, so thousands of videos — overwhelmingly
the Cantonese-first Dr Ng channel — were saved with the
``_(no transcript available)_`` marker even though YouTube auto-captions
exist for them (the original track is ``yue-orig``; zh-Hant/zh-Hans/en are
only auto-translations of it, which is why the old ``zh``-only sub-langs
filter couldn't save them).

For each candidate item (``has_transcript = false``, marker still present in
the file) the script:

1. Runs yt-dlp through the SSH SOCKS5 tunnel fan-out when configured
   (``--proxy-hosts`` or ``YT_DLP_PROXY_HOSTS`` from .env) — spreading the
   caption requests across egress IPs, since the single residential IP is
   persistently throttled on timedtext — falling back to direct when no
   tunnel is live. Bot-walled egresses (the oc*/Oracle IPs answer cookie-less
   yt-dlp with "Sign in to confirm you're not a bot") are benched for the
   rest of the run automatically. Requests are sub-only and originals-only
   (``yue-orig,yue,en`` — every extra language is one more timedtext hit,
   and translations are worse than the original anyway); nothing else about
   the item is touched.
2. Falls back to the youtube-transcript-api path (also direct, yue-preferred)
   on a clean miss.
3. Replaces the marker with the caption text, sets front-matter
   ``has_transcript: true`` + ``transcript_source: youtube-captions``, and
   re-ingests the file so the DB row flips (which also drops the item from
   the Whisper queue).

Politeness is the whole point — this is designed to be run daily over weeks,
never to brute-force the endpoint:

* per-item sleep ``--interval`` s + 0..``--jitter`` s on top of the scraper
  limiter's own direct interval;
* on any 429/block signature: exponential backoff (5 min → doubling, capped
  60 min) and the SAME item is retried after the cooldown;
* 3 consecutive blocked items abort the run (exit code 2) — resumable as-is,
  successful items are simply skipped next time via ``has_transcript``;
* ``--limit`` caps attempts per run and ``--max-minutes`` caps wall time.

Videos confirmed to have no captions on any surface are remembered in
``scripts/.backfill_youtube_transcripts_state.json`` and skipped on later
runs; ``--retry-no-cc`` clears that list. ``--dry-run`` lists candidates
without fetching.

Usage::

    uv run python scripts/backfill_youtube_transcripts.py --dry-run
    uv run python scripts/backfill_youtube_transcripts.py --limit 300
    uv run python scripts/backfill_youtube_transcripts.py --channel "Ng Ming" --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import text

from kb.db import engine
from kb.ingest import ingest_file
from kb.io_md import load_md
from kb.scrapers.youtube import NO_TRANSCRIPT_MARKER, YouTubeScraper

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / ".backfill_youtube_transcripts_state.json"
LOCK_PATH = SCRIPT_DIR / ".backfill_youtube_transcripts.lock"

# Substrings (case-insensitive) in yt-dlp's stderr, split by meaning:
# THROTTLE means YouTube is rate-limiting that egress IP — cool down and
# retry; BOTWALL means the egress is a flagged cloud IP (the oc*/Oracle
# tunnels get "Sign in to confirm you're not a bot" cookie-less) — bench
# that tunnel for the rest of the run and rotate to the next one.
THROTTLE_SIGNATURES = (
    "429",
    "too many requests",
    "ip has been blocked",
    "requestblocked",
)
BOTWALL_SIGNATURES = (
    "sign in to confirm",
    "protect our community",
    "not a bot",
)

EXIT_OK = 0
EXIT_BLOCKED = 2


class Egress:
    """Round-robin over the SSH SOCKS5 tunnel pool with per-host benching.

    Bot-walled hosts (Oracle egresses, see AGENTS.md) are skipped for the
    rest of the run instead of burning an attempt each rotation. Once every
    tunnel is benched or dead, ``next()`` returns ``(None, None)`` and the
    caller goes direct."""

    def __init__(self, hosts: list[str]):
        from kb.scrapers.proxy import ProxyPool
        self.pool: ProxyPool | None = ProxyPool(hosts) if hosts else None
        self.benched: set[str] = set()
        self.port_to_host: dict[int, str] = {}
        if self.pool:
            self.pool.start()
            for host, port, _proc in self.pool._procs:
                self.port_to_host[port] = host

    def next(self) -> tuple[str | None, str | None]:
        """Return ``(proxy_url, host)`` for the next live, unbenched tunnel,
        or ``(None, None)`` to go direct."""
        if self.pool is None:
            return None, None
        n = len(self.pool.urls)
        for _ in range(n + 1):
            url = self.pool.next()
            if url is None:
                return None, None
            port = int(url.rsplit(":", 1)[1])
            host = self.port_to_host.get(port)
            if host in self.benched:
                continue
            return url, host
        return None, None  # everything benched — direct

    def bench(self, host: str | None) -> None:
        if host:
            self.benched.add(host)

    def stop(self) -> None:
        if self.pool is not None:
            self.pool.stop()


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — corrupt state = start a new one
            pass
    return {"no_captions": []}


def save_state(state: dict) -> None:
    state["no_captions"] = sorted(set(state.get("no_captions", [])))
    STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def acquire_lock(max_minutes: int) -> bool:
    """Best-effort single-run lock; a lock older than 2× the time budget is
    considered stale (crashed run) and overridden."""
    if LOCK_PATH.exists():
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age < max_minutes * 120:
            return False
        LOCK_PATH.unlink()
    LOCK_PATH.write_text(f"pid={os.getpid()} started={time.ctime()}\n")
    return True


def candidates(channel: str | None, no_cc: set[str],
               min_duration: int = 0,
               external_ids: list[str] | None = None,
               year_from: int | None = None) -> list[dict]:
    """Items still missing a transcript, shortest first (matches the Whisper
    queue's quick-wins-first ordering). The marker check on the file keeps us
    honest against rows whose front-matter and file have drifted apart.
    ``min_duration`` skips promo shorts, which YouTube mostly doesn't
    auto-caption at all. ``year_from`` restricts to videos published in that
    year or later — sampling showed the Dr Ng backlog's caption availability
    is concentrated in 2025+ uploads (older years mostly have no CC at all),
    so the high-yield slice is best exhausted first."""
    with engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
            SELECT i.id, i.external_id, i.title, i.md_path, i.duration_sec,
                   ch.handle AS channel_handle, ch.name AS channel_name
            FROM item i
            JOIN source s ON s.id = i.source_id
            LEFT JOIN channel ch ON ch.id = i.channel_id
            WHERE s.code = 'youtube'
              AND i.has_transcript = false
              AND i.md_path IS NOT NULL
              AND (i.duration_sec IS NULL OR i.duration_sec >= :mindur)
              AND (CAST(:yfrom AS int) IS NULL
                   OR i.published_at >= make_date(CAST(:yfrom AS int), 1, 1))
            ORDER BY COALESCE(i.duration_sec, 999999999) ASC, i.id
            LIMIT 20000
        """), {"mindur": min_duration, "yfrom": year_from}).mappings().all()]
    out: list[dict] = []
    for r in rows:
        if r["external_id"] in no_cc:
            continue
        if external_ids is not None and r["external_id"] not in external_ids:
            continue
        if channel:
            needle = channel.lower()
            hay = " ".join((r.get("channel_handle") or "",
                            r.get("channel_name") or "")).lower()
            if needle not in hay:
                continue
        p = Path(r["md_path"])
        if not p.exists():
            continue
        try:
            if NO_TRANSCRIPT_MARKER not in p.read_text(encoding="utf-8"):
                continue  # already fixed outside the DB's knowledge
        except OSError:
            continue
        out.append(r)
    return out


def _lang_of(vtt: Path, video_id: str) -> str:
    stem = vtt.name[: -len(".vtt")]
    return stem[len(video_id):].lstrip(".").lower() or "unknown"


async def fetch_one(sc: YouTubeScraper, egress: Egress,
                    video_id: str) -> tuple[str, str | None, str]:
    """Fetch captions for one video. Returns ``(text, lang, kind)`` where
    kind is:

    * ``ok`` — captions retrieved;
    * ``miss`` — no captions on any surface (a genuine clean miss);
    * ``throttle`` — YouTube rate-limited the egress: cool down and retry
      the same item;
    * ``egress_dead`` — the tunnel egress is bot-walled; it has been
      benched and the item should be retried on the next egress right away.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    proxy, host = egress.next()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if proxy is not None:
            await sc.limiter.wait(url)
        else:
            await sc.limiter.wait(url, min_interval=sc.direct_interval)
        cp = sc._ytdlp(
            "--skip-download",
            "--write-auto-subs", "--write-subs",
            # Originals only: every extra language is one more timedtext
            # hit, and a 3rd consecutive hit reliably 429s. Translations
            # are strictly worse than the yue-orig original anyway.
            "--sub-langs", "yue-orig,yue,en",
            "--convert-subs", "vtt",
            "--ignore-errors", "--no-abort-on-error",
            "--no-warnings",
            "-o", str(tmp / "%(id)s.%(ext)s"),
            url,
            proxy=proxy,
        )
        vtt = sc._pick_vtt(tmp.glob(f"{video_id}*.vtt"))
        if vtt:
            return sc._vtt_to_text(vtt.read_text("utf-8")), _lang_of(vtt, video_id), "ok"
        blob = ((cp.stderr or "") + (cp.stdout or "")).lower()
        if any(sig in blob for sig in BOTWALL_SIGNATURES):
            if proxy is not None:
                egress.bench(host)
                return "", None, "egress_dead"
            return "", None, "throttle"  # residential IP flagged — worst case
        if any(sig in blob for sig in THROTTLE_SIGNATURES):
            return "", None, "throttle"
    # Clean miss: the youtube-transcript-api path (direct, yue-first now that
    # the preferred list includes it). Returns "" both for "no captions" and
    # for a swallowed block — the run-level block seen so far absorbs that.
    await sc.limiter.wait(url, min_interval=sc.direct_interval)
    try:
        text = sc._fetch_transcript_api(video_id)
    except Exception:  # noqa: BLE001
        text = ""
    return text, None, ("ok" if text.strip() else "miss")


def update_md(md_path: Path, transcript: str, lang: str | None) -> bool:
    """Surgically replace the marker; returns False if the file already has
    transcript content (leave it alone)."""
    doc = load_md(md_path)
    body = doc.body
    if NO_TRANSCRIPT_MARKER in body:
        body = body.replace(NO_TRANSCRIPT_MARKER, transcript.strip())
    else:
        return False
    doc.body = body
    doc.front["has_transcript"] = True
    if lang:
        doc.front["transcript_language"] = lang
    doc.front["transcript_source"] = "youtube-captions"
    md_path.write_text(doc.dump(), encoding="utf-8")
    return True


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=300,
                    help="max items to attempt this run (default 300)")
    ap.add_argument("--interval", type=float, default=10.0,
                    help="politeness sleep between items, seconds (default 10)")
    ap.add_argument("--jitter", type=float, default=8.0,
                    help="extra random 0..N sleep between items (default 8)")
    ap.add_argument("--max-minutes", type=int, default=90,
                    help="stop attempting new items after N minutes (default 90)")
    ap.add_argument("--channel", default=None,
                    help="substring filter on channel name/handle (e.g. 'Ng Ming')")
    ap.add_argument("--min-duration", type=int, default=60,
                    help="skip videos shorter than N seconds — promo shorts "
                         "mostly have no auto-CC (default 60)")
    ap.add_argument("--external-id", action="append", default=None,
                    metavar="VIDEO_ID",
                    help="restrict to specific video id(s); repeatable")
    ap.add_argument("--year-from", type=int, default=None,
                    help="only videos published in this year or later "
                         "(e.g. 2025 for the caption-bearing Dr Ng slice)")
    ap.add_argument("--proxy-hosts", default=None,
                    metavar="HOST,HOST",
                    help="SSH SOCKS5 tunnel hosts for yt-dlp fan-out "
                         "(default: YT_DLP_PROXY_HOSTS from .env; bot-walled "
                         "egresses are benched automatically)")
    ap.add_argument("--no-proxy", action="store_true",
                    help="never use tunnels; direct only")
    ap.add_argument("--retry-no-cc", action="store_true",
                    help="forget the recorded no-captions list and retry those too")
    ap.add_argument("--dry-run", action="store_true",
                    help="list candidates and exit without fetching")
    args = ap.parse_args()

    state = load_state()
    if args.retry_no_cc:
        state["no_captions"] = []
        save_state(state)
    no_cc: set[str] = set(state.get("no_captions", []))

    rows = candidates(args.channel, no_cc,
                      min_duration=args.min_duration,
                      external_ids=args.external_id,
                      year_from=args.year_from)
    log(f"{len(rows)} candidate(s) missing transcripts"
        + (f" (channel~{args.channel!r})" if args.channel else "")
        + f"; {len(no_cc)} recorded as no-captions")
    if args.dry_run:
        for r in rows[:40]:
            dur = r.get("duration_sec")
            print(f"  {r['external_id']}  {int(dur) if dur else '?':>5}s  "
                  f"{(r['title'] or '')[:60]}")
        if len(rows) > 40:
            print(f"  … and {len(rows) - 40} more")
        return EXIT_OK
    if not rows:
        return EXIT_OK

    if not acquire_lock(args.max_minutes):
        log(f"another run appears active ({LOCK_PATH}); aborting")
        return EXIT_OK

    sc = YouTubeScraper()
    if args.no_proxy:
        hosts: list[str] = []
    elif args.proxy_hosts is not None:
        from kb.scrapers.proxy import parse_hosts
        hosts = parse_hosts(args.proxy_hosts)
    else:
        from kb.config import settings
        from kb.scrapers.proxy import parse_hosts
        hosts = parse_hosts(settings().yt_dlp_proxy_hosts)
    egress = Egress(hosts)
    if hosts:
        live = len(egress.pool.urls) if egress.pool else 0
        log(f"egress fan-out: {len(hosts)} host(s) requested, {live} tunnel(s) up"
            + (f" {live} → {egress.pool.urls}" if live else ""))
    started = time.monotonic()
    deadline_s = args.max_minutes * 60
    attempted = success = no_caption = skipped = 0
    lang_counts: dict[str, int] = {}
    consec_blocked = 0
    backoff_s = 300.0
    aborted_blocked = False
    try:
        i = 0
        while i < len(rows) and attempted < args.limit:
            if time.monotonic() - started > deadline_s:
                log(f"time budget of {args.max_minutes} min reached; stopping")
                break
            r = rows[i]
            vid = r["external_id"]
            try:
                text, lang, kind = await fetch_one(sc, egress, vid)
            except Exception as exc:  # noqa: BLE001 — one bad item must not
                log(f"  ✗ {vid} error: {exc}")  # kill the whole run
                text, lang, kind = "", None, "miss"
            if kind == "egress_dead":
                log(f"  ⏭ egress benched for {vid}; rotating")
                await asyncio.sleep(2)
                continue  # retry the same item on the next egress
            if kind == "throttle":
                consec_blocked += 1
                if consec_blocked >= 3:
                    log("3 consecutive blocked items — YouTube is throttling "
                        "every egress; aborting for today (rerun resumes as-is)")
                    aborted_blocked = True
                    break
                log(f"  ⏸ {vid} blocked ({consec_blocked}/3); "
                    f"cooling down {backoff_s:.0f}s")
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, 3600.0)
                continue  # retry the same item after cooldown
            consec_blocked = 0
            backoff_s = 300.0
            attempted += 1
            if text and text.strip():
                p = Path(r["md_path"])
                if update_md(p, text, lang):
                    ingest_file(p)
                    success += 1
                    key = lang or "api-fallback"
                    lang_counts[key] = lang_counts.get(key, 0) + 1
                    dur = r.get("duration_sec")
                    log(f"  ✓ {vid} lang={key} {len(text)} chars "
                        f"({int(dur) if dur else '?'}s video)")
                else:
                    skipped += 1
                    log(f"  - {vid} marker gone; skipped")
            else:
                no_caption += 1
                no_cc.add(vid)
                log(f"  · {vid} no captions found")
            if attempted % 25 == 0:
                save_state({**state, "no_captions": sorted(no_cc)})
            i += 1
            await asyncio.sleep(args.interval + random.uniform(0, args.jitter))
    finally:
        save_state({**state, "no_captions": sorted(no_cc)})
        egress.stop()
        LOCK_PATH.unlink(missing_ok=True)

    elapsed = time.monotonic() - started
    log(f"done in {elapsed / 60:.1f} min — attempted {attempted}, "
        f"backfilled {success} ({lang_counts}), no-captions {no_caption}, "
        f"skipped {skipped}"
        + (f", benched egresses {sorted(egress.benched)}" if egress.benched else ""))
    remaining = len(rows) - success - (1 if aborted_blocked else 0)
    log(f"~{max(remaining, 0)} candidate(s) remaining in this slice")
    return EXIT_BLOCKED if aborted_blocked else EXIT_OK


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
