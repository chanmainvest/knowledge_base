"""YouTube scraper using yt-dlp (no API key required).

Strategy: for each channel, list latest videos via yt-dlp's metadata-only
extraction, download auto/uploaded subtitles (vtt), convert to plain text
markdown. Falls back to youtube-transcript-api if yt-dlp can't fetch subs.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import text as sa_text

from ..config import DATA_DIR, settings
from ..io_md import MdDoc, load_md, slugify
from .base import BaseScraper, ScrapedItem
from .proxy import ProxyPool, parse_hosts
from ..ratelimit import HostRateLimiter

# Written into the markdown body (and detected by ingest/backfill) when neither
# yt-dlp subtitles nor the youtube-transcript-api fallback produced any text.
NO_TRANSCRIPT_MARKER = "_(no transcript available)_"


def _find_deno() -> str | None:
    """Locate the deno binary if installed. YouTube now requires a JS runtime
    for full extraction (subtitle downloads fail without it). Checks PATH then
    the default install location on Windows."""
    p = shutil.which("deno")
    if p:
        return p
    # Windows default install path from the official installer.
    import sys
    if sys.platform == "win32":
        candidate = Path.home() / ".deno" / "bin" / "deno.exe"
        if candidate.exists():
            return str(candidate)
    return None


# Default channel list — used to seed the DB on first run.
# After seeding, channels are managed exclusively in the DB via
# `kb youtube add-channel` / `kb youtube list-channels`.
_DEFAULT_CHANNELS: list[tuple[str, str]] = [
    # (handle_or_url, display_name)
    ("@Fedguy12", "Fed Guy"),
    ("@maggielake-talkingmarkets", "Maggie Lake — Talking Markets"),
    ("@CPMGroup", "CPM Group"),
    ("@Monetary-Matters", "Monetary Matters"),
    ("@PBoyle", "Patrick Boyle"),
    ("@RealVisionFinance", "Real Vision Finance"),
    ("@eurodollaruniversity", "Eurodollar University"),
    ("@oaktreecapital", "Oaktree Capital"),
    ("@MacroDirtCast", "Macro Dirt"),
    ("@RaoulPalTJM", "Raoul Pal — The Journey Man"),
    ("@GeorgeGammon", "George Gammon"),
    ("@RuleInvestmentMedia", "Rule Investment Media"),
    ("@ThePlainBagel", "The Plain Bagel"),
    ("@ForwardGuidanceBW", "Forward Guidance"),
    ("@SimplifyAssetManagement", "Simplify Asset Management"),
    ("@LibraryofMistakes", "Library of Mistakes"),
    ("@LATP", "LATP"),
    ("@Money-Tab", "Money Tab"),
    ("@ivankcho", "Ivan K. Cho"),
    ("@紅磡索螺絲", "紅磡索螺絲"),
]


def _load_channels() -> list[tuple[str, str]]:
    """Return (handle, name) pairs from the DB channel table.

    On first call (no youtube channels in DB), the default list is seeded
    automatically so subsequent runs are DB-driven.
    """
    try:
        from ..db import engine as db_engine
        with db_engine().connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT c.handle, c.name FROM channel c "
                "JOIN source s ON c.source_id = s.id WHERE s.code = 'youtube' "
                "ORDER BY c.name"
            )).fetchall()
        if rows:
            return [(r[0], r[1]) for r in rows]
        # DB has no youtube channels yet — seed from defaults.
        _seed_default_channels()
    except Exception:
        pass
    return _DEFAULT_CHANNELS


def _seed_default_channels() -> None:
    """Insert _DEFAULT_CHANNELS into the channel table (idempotent)."""
    try:
        from ..db import engine as db_engine
        with db_engine().begin() as conn:
            sid = conn.execute(
                sa_text("SELECT id FROM source WHERE code='youtube'")
            ).scalar_one_or_none()
            if sid is None:
                return
            for handle, name in _DEFAULT_CHANNELS:
                conn.execute(sa_text(
                    "INSERT INTO channel(source_id, handle, name) VALUES (:s,:h,:n) "
                    "ON CONFLICT (source_id, handle) DO NOTHING"
                ), {"s": sid, "h": handle, "n": name})
    except Exception:
        pass


def _channel_videos_url(handle: str) -> str:
    handle = handle.strip()
    if handle.startswith("http"):
        return handle if handle.rstrip("/").endswith("/videos") else f"{handle.rstrip('/')}/videos"
    return f"https://www.youtube.com/{handle}/videos"


def _parse_channel_display_name(stdout: str) -> str | None:
    """Extract uploader/channel title from yt-dlp playlist JSON output."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in ("uploader", "channel"):
            val = j.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def channel_dir_slug(channel_name: str) -> str:
    """Filesystem directory slug for a YouTube channel display name."""
    return slugify(channel_name)


def _channel_dir_slugs() -> dict[str, str]:
    """handle → pinned storage folder slug (``channel.metadata['dir_slug']``).

    The slug is pinned so a later display-name change (e.g.
    ``MacroVoices`` → ``Macro Voices``) cannot fork the channel's files into
    a second folder. Channels without a pin fall back to the slug of their
    current display name (the pre-pinning behaviour); ``discover()`` pins
    that value on first sight.
    """
    from ..db import engine as db_engine
    try:
        with db_engine().connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT c.handle, c.name, c.metadata FROM channel c "
                "JOIN source s ON c.source_id = s.id WHERE s.code = 'youtube'"
            )).fetchall()
    except Exception:
        return {}
    out: dict[str, str] = {}
    for handle, name, meta in rows:
        slug = meta.get("dir_slug") if isinstance(meta, dict) else None
        out[handle] = slug if isinstance(slug, str) and slug else channel_dir_slug(name)
    return out


def _pin_channel_dir(handle: str, dir_slug: str) -> None:
    """Persist the channel's storage folder slug so it stays stable."""
    from ..db import engine as db_engine
    try:
        with db_engine().begin() as conn:
            conn.execute(sa_text(
                "UPDATE channel SET metadata = COALESCE(metadata, '{}'::jsonb) "
                "|| CAST(:m AS jsonb) "
                "WHERE source_id = (SELECT id FROM source WHERE code = 'youtube') "
                "AND handle = :h"
            ), {"m": json.dumps({"dir_slug": dir_slug}), "h": handle})
    except Exception:
        pass


def normalize_youtube_handle(handle: str) -> str:
    """Canonical stored handle: @Name, extracted from URLs when given.

    A full channel URL (https://www.youtube.com/@X[/videos]) is reduced to
    ``@X`` so it can't fork the channel into a second ``channel`` row beside
    an existing @-form handle. Non-@ URL shapes (/channel/UC…/, /c/Name/,
    /user/Name/) are kept verbatim — there's no @handle to extract.
    """
    handle = handle.strip()
    if handle.startswith(("http://", "https://")):
        from urllib.parse import urlparse

        path = urlparse(handle).path
        parts = [p for p in path.split("/") if p]
        if parts and parts[0].startswith("@"):
            return parts[0]
        return handle.rstrip("/")
    if handle.startswith("@"):
        return handle
    return f"@{handle}"


def _youtube_md_path(
    channel_slug: str,
    *,
    upload_date: str | None,
    title: str,
    external_id: str,
) -> Path:
    date = upload_date or "undated"
    date_fmt = (f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else "undated")
    year = date[:4] if len(date) == 8 else "undated"
    stem = f"{date_fmt}-{slugify(title, 80)}"
    return DATA_DIR / "youtube" / channel_slug / year / f"{stem}.md"


def _merge_dir_into(src: Path, dst: Path) -> None:
    """Move *src* tree into *dst*, merging when subpaths already exist."""
    import shutil
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                _merge_dir_into(item, target)
                item.rmdir()
            else:
                shutil.move(str(item), str(target))
        elif target.exists():
            continue
        else:
            shutil.move(str(item), str(target))
    if src.exists() and not any(src.iterdir()):
        src.rmdir()


def plan_youtube_folder_renames() -> list[tuple[Path, Path]]:
    """Return (old_dir, new_dir) pairs to align folders with channel display names."""
    yt_root = DATA_DIR / "youtube"
    if not yt_root.is_dir():
        return []

    planned: dict[Path, Path] = {}
    target_names: set[str] = set()
    dir_slugs = _channel_dir_slugs()

    for handle, name in _load_channels():
        old = yt_root / channel_dir_slug(handle)
        new = yt_root / (dir_slugs.get(handle) or channel_dir_slug(name))
        target_names.add(new.name)
        if old != new:
            planned[old] = new

    for folder in sorted(yt_root.iterdir()):
        if not folder.is_dir() or folder in planned or folder.name in target_names:
            continue
        sample = next(folder.rglob("*.md"), None)
        if sample is None:
            continue
        name = load_md(sample).front.get("channel_name")
        if not isinstance(name, str) or not name.strip():
            continue
        new = yt_root / channel_dir_slug(name)
        if folder != new:
            planned[folder] = new

    return sorted(planned.items())


def migrate_youtube_folders(*, dry_run: bool = False) -> list[tuple[Path, Path]]:
    """Rename data/youtube/<handle-slug>/ dirs to slugified channel display names."""
    done: list[tuple[Path, Path]] = []
    for old, new in plan_youtube_folder_renames():
        if not old.is_dir():
            continue
        if dry_run:
            done.append((old, new))
            continue
        if new.exists():
            _merge_dir_into(old, new)
        else:
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
        done.append((old, new))
    return done


# --- Date backfill ------------------------------------------------------------
#
# When yt-dlp's metadata fetch fails (dead tunnel / 429) the video is saved
# with published_at=None. Two scars remain in the data:
#   * files under <channel>/undated/undated-<title>.md (never dated), and
#   * files that fix_youtube_dup_folders.py later *promoted* from undated/
#     into a dated loser's path — a dated filename whose front-matter (and
#     DB row) still says null.
# `kb youtube backfill-dates` repairs both: first offline from the dated
# filenames, then (optional) via a per-video direct yt-dlp lookup for what's
# left under undated/.

_DATE_STEM_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

# Title shapes seen in the wild: "…| 15May2022", "6 Nov 2020", "15th May, 2022",
# "May 15, 2022", "2022-05-15". YouTube era guard (2005..now) rejects
# incidental numbers like "…of 1984".
_TITLE_DMY_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]{3,9})\s*,?\s*(\d{4})\b", re.I)
_TITLE_MDY_RE = re.compile(
    r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_TITLE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_UPLOAD_DATE_HTML_RE = re.compile(
    r'"(?:uploadDate|publishDate)"\s*:\s*"(\d{4})-(\d{2})-(\d{2})')


def _month_num(tok: str) -> int | None:
    t = tok.lower()
    if t in _MONTHS:
        return _MONTHS[t]
    for k, v in _MONTHS.items():
        if t.startswith(k):   # january, september, …
            return v
    return None


def _valid_ymd(y: int, m: int, d: int) -> str | None:
    """Validate (y, m, d) and return YYYYMMDD, or None if impossible or
    outside YouTube's existence (2005-04-23 .. now)."""
    try:
        dt = datetime(y, m, d, tzinfo=timezone.utc)
    except ValueError:
        return None
    if dt < datetime(2005, 4, 23, tzinfo=timezone.utc):
        return None
    if dt > datetime.now(timezone.utc):
        return None
    return dt.strftime("%Y%m%d")


def _date_from_title(title: str | None) -> str | None:
    """Best-effort upload date (YYYYMMDD) parsed from a video title — the
    last-resort fallback for videos whose metadata can't be fetched at all
    (deleted/gated videos that nonetheless carry the date in their title,
    e.g. "…| 15May2022")."""
    if not title:
        return None
    m = _TITLE_ISO_RE.search(title)
    if m:
        r = _valid_ymd(int(m[1]), int(m[2]), int(m[3]))
        if r:
            return r
    m = _TITLE_DMY_RE.search(title)
    if m:
        mon = _month_num(m[2])
        if mon:
            r = _valid_ymd(int(m[3]), mon, int(m[1]))
            if r:
                return r
    m = _TITLE_MDY_RE.search(title)
    if m:
        mon = _month_num(m[1])
        if mon:
            r = _valid_ymd(int(m[3]), mon, int(m[2]))
            if r:
                return r
    return None


def _extract_upload_date_from_html(html: str) -> str | None:
    """Pull the upload/publish date out of a YouTube /watch page's initial
    player response (``"uploadDate":"2022-05-15T…"``)."""
    m = _UPLOAD_DATE_HTML_RE.search(html)
    if not m:
        return None
    return _valid_ymd(int(m[1]), int(m[2]), int(m[3]))


def _published_from_stem(p: Path) -> datetime | None:
    m = _DATE_STEM_RE.match(p.name)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        tzinfo=timezone.utc)
    except ValueError:
        return None


# --- Transcript formatting ----------------------------------------------------
#
# VTT captions are 5-8-word display lines. Stored verbatim (one \n per cue
# line) they render as a single giant paragraph — markdown joins single
# newlines — which is unreadable in the GUI and gives the LLM chunker no
# paragraph boundaries. Paragraphing therefore happens at scrape time, from
# the signals only the original VTT carries (cue timing gaps, >> speaker
# markers); the markdown stays the canonical readable form. The same
# paragrapher re-formats already-stored transcripts (`kb youtube
# reformat-transcripts`) with the weaker text-only heuristics.

_VTT_PAUSE_SEC = 2.5          # cue gap that starts a new paragraph
_MAX_PARAGRAPH_CHARS = 600    # cap; split at sentence boundaries above this

_VTT_TAG_RES = [
    re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>"),
    re.compile(r"</?c[^>]*>"),
    re.compile(r"<[^>]+>"),
]
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _clean_vtt_line(s: str) -> str:
    for rx in _VTT_TAG_RES:
        s = rx.sub("", s)
    return s.replace("&nbsp;", " ").replace("&amp;", "&") \
            .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").strip()


def _cue_time(ts: str) -> float | None:
    """'00:01:23.500' → 83.5 (hours optional)."""
    m = re.match(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})", ts.strip())
    if not m:
        return None
    h = int(m.group(1)) if m.group(1) else 0
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000


def _parse_vtt_cues(vtt: str) -> list[tuple[float, float, str]]:
    """(start, end, text) per cue. Cue text lines are joined with spaces;
    a cue identical to its predecessor (rollup-caption artefact) is dropped."""
    cues: list[tuple[float, float, str]] = []
    lines = vtt.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if "-->" not in s:
            i += 1
            continue
        left, _, right = s.partition("-->")
        start = _cue_time(left)
        end = _cue_time(right.split()[0]) if right.split() else None
        texts: list[str] = []
        i += 1
        while i < len(lines):
            t = lines[i].strip()
            if not t or "-->" in t:
                break
            c = _clean_vtt_line(t)
            if c:
                texts.append(c)
            i += 1
        if start is None or not texts:
            continue
        text = " ".join(texts)
        if cues and cues[-1][2] == text:
            continue   # rollup window repeated the previous cue
        cues.append((start, end if end is not None else start, text))
    return cues


def _split_long_paragraph(par: str, max_chars: int = _MAX_PARAGRAPH_CHARS) -> list[str]:
    """Split a paragraph longer than max_chars at sentence boundaries."""
    par = par.strip()
    if len(par) <= max_chars:
        return [par] if par else []
    out: list[str] = []
    buf = ""
    for sent in _SENTENCE_SPLIT_RE.split(par):
        if buf and len(buf) + 1 + len(sent) > max_chars:
            out.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        out.append(buf)
    return out


def _assemble_paragraphs(chunks: list[str]) -> str:
    """Whitespace-normalize each chunk (caption line breaks become spaces)
    and cap paragraph length; chunks become markdown paragraphs (\n\n)."""
    pars: list[str] = []
    for chunk in chunks:
        frag = re.sub(r"\s+", " ", chunk).strip()
        if not frag:
            continue
        pars.extend(_split_long_paragraph(frag))
    return "\n\n".join(pars)


def _vtt_to_paragraphs(vtt: str) -> str:
    """Transcript text with paragraphs from the VTT's own signals: >>
    speaker changes, cue timing gaps, and a sentence-boundary length cap."""
    chunks: list[str] = []
    prev_end: float | None = None
    for start, end, text in _parse_vtt_cues(vtt):
        speaker_break = text.startswith(">>")
        t = text.lstrip(">").strip()
        if not t:
            continue
        pause_break = prev_end is not None and (start - prev_end) > _VTT_PAUSE_SEC
        if chunks and (speaker_break or pause_break):
            chunks.append("\n\n" + t)
        elif chunks:
            chunks.append(" " + t)
        else:
            chunks.append(t)
        prev_end = end
    return _assemble_paragraphs("".join(chunks).split("\n\n"))


def _format_transcript_text(text: str) -> str:
    """Re-paragraph a *stored* caption-line transcript (no timing data):
    join the 5-8-word lines into flowing text, break at >> speaker
    markers, cap paragraph length at sentence boundaries. Idempotent —
    existing paragraph breaks (\n\n) are preserved, so already-paragraphed
    text passes through unchanged."""
    pars: list[str] = []
    for par in re.split(r"\n\s*\n", text):
        for chunk in re.split(r"\s*>>\s*", par):
            frag = re.sub(r"\s+", " ", chunk).strip()
            if frag:
                pars.extend(_split_long_paragraph(frag))
    return "\n\n".join(pars)


def _stamp_item_dates(item_id: int, dt: datetime, md_path: str) -> None:
    """Write published_at into the DB row and recover any predictions that
    were left unscoreable by the null made_at (extract copies made_at from
    published_at at extraction time)."""
    from ..db import engine as db_engine
    with db_engine().begin() as conn:
        conn.execute(sa_text("""
            UPDATE item SET published_at = :d WHERE id = :i
        """), {"d": dt, "i": item_id})
        conn.execute(sa_text("""
            UPDATE prediction SET made_at = :d
            WHERE item_id = :i AND made_at IS NULL
        """), {"d": dt, "i": item_id})
        conn.execute(sa_text("""
            UPDATE item SET md_path = :mp WHERE id = :i
        """), {"mp": md_path, "i": item_id})


def backfill_dates_from_filenames(*, dry_run: bool = False) -> int:
    """Stamp published_at (front-matter + DB) on NULL-dated youtube items
    whose markdown filename already carries a YYYY-MM-DD- prefix — the
    promoted-by-dedup case. The front-matter must be fixed too, or the next
    `kb ingest` re-nulls the DB row from the file."""
    from ..db import engine as db_engine
    with db_engine().connect() as conn:
        rows = conn.execute(sa_text("""
            SELECT i.id, i.md_path
            FROM item i JOIN source s ON s.id = i.source_id
            WHERE s.code = 'youtube' AND i.published_at IS NULL
              AND i.md_path IS NOT NULL
              AND i.md_path NOT LIKE '%undated%'
        """)).fetchall()
    n = 0
    for item_id, mp in rows:
        p = Path(mp)
        if not p.is_file():
            continue
        dt = _published_from_stem(p)
        if dt is None:
            continue
        if dry_run:
            n += 1
            continue
        doc = load_md(p)
        doc.front["published_at"] = dt
        doc.write(p)
        _stamp_item_dates(item_id, dt, str(p))
        n += 1
    return n


async def backfill_undated_metadata(
    *, limit: int = 0, dry_run: bool = False,
) -> dict[str, int]:
    """Look up upload dates online for youtube items still filed under
    ``undated/`` and move them to their dated paths.

    One video at a time, direct connection (no proxy — see
    `_polite_ytdlp_direct`), so the run is polite and resumable: items that
    already have a date are skipped, and videos whose date can't be resolved
    (deleted/private) are simply counted as unknown.
    """
    from ..db import engine as db_engine
    with db_engine().connect() as conn:
        rows = conn.execute(sa_text("""
            SELECT i.id, i.external_id, i.md_path
            FROM item i JOIN source s ON s.id = i.source_id
            WHERE s.code = 'youtube' AND i.published_at IS NULL
              AND i.md_path IS NOT NULL AND i.md_path LIKE '%undated%'
            ORDER BY i.id
        """)).fetchall()
    if limit and limit > 0:
        rows = rows[:limit]

    sc = YouTubeScraper()
    stats = {"candidates": len(rows), "dated": 0, "unknown": 0, "missing_file": 0}
    for item_id, vid, mp in rows:
        p = Path(mp)
        if not p.is_file():
            stats["missing_file"] += 1
            continue
        doc = load_md(p)
        title = doc.front.get("title") or vid
        url = f"https://www.youtube.com/watch?v={vid}"
        cp = await sc._polite_ytdlp_direct(
            url, "--skip-download", "--dump-json", "--no-warnings", url)
        upload_date = None
        if cp.returncode == 0 and cp.stdout.strip():
            try:
                upload_date = json.loads(cp.stdout.splitlines()[0]).get("upload_date")
            except Exception:
                pass
        if not upload_date or len(upload_date) != 8:
            # yt-dlp couldn't resolve it (deleted/gated video, or extraction
            # blocked) — same two extra sources the scraper's fetch() uses.
            upload_date = await sc._lookup_upload_date_direct(vid)
        if not upload_date:
            upload_date = _date_from_title(title)
        if not upload_date or len(upload_date) != 8:
            stats["unknown"] += 1
            continue
        try:
            dt = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            stats["unknown"] += 1
            continue

        # Target path mirrors the scraper layout: <channel>/<YYYY>/<date>-<slug>
        channel_dir = p.parents[1].name
        new_path = _youtube_md_path(
            channel_dir, upload_date=dt.strftime("%Y%m%d"),
            title=title, external_id=vid)
        if dry_run:
            stats["dated"] += 1
            continue
        if new_path.exists() and new_path.stat().st_size > 200:
            # A dated copy already survived the folder dedup — keep it as
            # the canonical file (its front-matter already has the date)
            # and drop the undated duplicate instead of clobbering it.
            p.unlink()
        else:
            doc.front["published_at"] = dt
            doc.write(new_path)
            if p.exists():
                p.unlink()
        # Drop the undated/ dir when this was its last file.
        try:
            if p.parent.is_dir() and not any(p.parent.iterdir()):
                p.parent.rmdir()
        except OSError:
            pass
        _stamp_item_dates(item_id, dt, str(new_path))
        stats["dated"] += 1
    return stats


def reformat_transcripts(*, dry_run: bool = False, limit: int = 0) -> dict[str, int]:
    """Re-paragraph the transcript section of every stored YouTube markdown
    file (caption-line walls → flowing paragraphs; see
    `_format_transcript_text`), rewriting the file and re-ingesting it so
    the DB content, FTS index and LLM chunking all see the same readable
    form. Offline and idempotent — files whose transcript formats to
    identical text are skipped."""
    from ..db import engine as db_engine
    from .. import ingest as ingest_mod
    with db_engine().connect() as conn:
        rows = conn.execute(sa_text("""
            SELECT i.md_path
            FROM item i JOIN source s ON s.id = i.source_id
            WHERE s.code = 'youtube' AND i.md_path IS NOT NULL
            ORDER BY i.id
        """)).fetchall()
    if limit and limit > 0:
        rows = rows[:limit]

    stats = {"files": len(rows), "reformatted": 0, "unchanged": 0,
             "no_transcript": 0, "missing_file": 0}
    for (mp,) in rows:
        p = Path(mp)
        if not p.is_file():
            stats["missing_file"] += 1
            continue
        doc = load_md(p)
        body = doc.body
        marker = "## Transcript"
        idx = body.find(marker)
        if idx < 0:
            stats["no_transcript"] += 1
            continue
        head = body[:idx]
        tail = body[idx + len(marker):].strip()
        if not tail or tail == NO_TRANSCRIPT_MARKER.strip():
            stats["no_transcript"] += 1
            continue
        new_tail = _format_transcript_text(tail)
        if new_tail == tail:
            stats["unchanged"] += 1
            continue
        stats["reformatted"] += 1
        if dry_run:
            continue
        doc.body = head + marker + "\n\n" + new_tail + "\n"
        doc.write(p)
        try:
            ingest_mod.ingest_file(p)
        except Exception:  # noqa: BLE001
            # Ingest failing must not abort the sweep — the file is fixed;
            # a later `kb ingest` pass will pick it up.
            pass
    return stats


# An empty `## Description` section: the heading followed by nothing but
# blank lines up to the next section. The fingerprint of the stub-info files
# written when yt-dlp's metadata fetch failed mid-scrape.
_EMPTY_DESC_RE = re.compile(r"^## Description[ \t]*\n\s*(?=^## |\Z)", re.M)

# stderr substrings (lowercased) meaning "YouTube is throttling this IP" —
# the same list scripts/backfill_youtube_transcripts.py has proven in daily
# runs. Note "sign in to confirm" must stay the full phrase: yt-dlp's
# *private video* error says "Sign in if you've been granted access", which
# is a dead video, not a block.
_BLOCK_SIGNATURES = (
    "429", "too many requests", "sign in to confirm",
    "protect our community", "ip has been blocked",
)
_GONE_SIGNATURES = (
    "private", "unavailable", "removed", "deleted",
    "members-only", "terminated",
)


def _err_class(blob: str) -> str | None:
    """Classify a failed yt-dlp run from its stderr/stdout text:
    ``"blocked"`` (IP throttled — cool down, retry the same video),
    ``"gone"`` (private/deleted video — skip forever), or ``None``
    (unknown/transient)."""
    low = blob.lower()
    if any(sig in low for sig in _BLOCK_SIGNATURES):
        return "blocked"
    if any(sig in low for sig in _GONE_SIGNATURES):
        return "gone"
    return None


def _extract_json_object(text: str, start_marker: str) -> str | None:
    """Extract the JSON object following ``start_marker`` in *text* by
    brace-matching (string-aware), so nested braces inside string values
    can't truncate it the way a lazy regex would."""
    idx = text.find(start_marker)
    if idx < 0:
        return None
    i = text.find("{", idx + len(start_marker))
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return None


def _watch_page_info(
    video_id: str, proxy: str | None = None,
) -> tuple[dict | None, str | None]:
    """Fetch the ``/watch`` HTML and pull ``ytInitialPlayerResponse``'s
    ``videoDetails`` + ``microformat`` — the same metadata ``--dump-json``
    returns, as one plain GET. The watch page is served to egress IPs that
    YouTube bot-challenges on the innertube API, which is what makes
    pooled/tunnel backfill viable at all. Returns ``(info_dict, err_class)``
    — err_class uses the same vocabulary as ``_err_class``."""
    import requests
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        return None, "gone"
    try:
        r = requests.get(
            "https://www.youtube.com/watch", params={"v": video_id},
            headers={"User-Agent": settings().scrape_user_agent,
                     "Accept-Language": "en-US,en;q=0.9"},
            timeout=20, allow_redirects=False,
            proxies=({"http": proxy, "https": proxy} if proxy else None))
    except requests.RequestException:
        return None, None
    if r.status_code == 429:
        return None, "blocked"
    if r.status_code != 200:
        return None, None
    blob = _extract_json_object(r.text, "ytInitialPlayerResponse")
    if blob is None:
        # No embedded player response: the bot-wall shell page (its title
        # is literally " - YouTube"). Treat as blocked — cool down.
        return None, "blocked"
    try:
        pr = json.loads(blob)
    except Exception:
        return None, None
    vd = pr.get("videoDetails") or {}
    if not vd.get("title"):
        return None, "gone"
    mf = (pr.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    ud = (mf.get("uploadDate") or mf.get("publishDate") or "")[:10]
    info: dict = {
        "id": video_id,
        "title": vd.get("title"),
        "description": vd.get("shortDescription") or "",
        "duration": int(vd["lengthSeconds"]) if vd.get("lengthSeconds") else None,
        "uploader": vd.get("author"),
        "uploader_id": None,
        "view_count": (int(vd["viewCount"])
                       if str(vd.get("viewCount") or "").isdigit() else None),
        "tags": vd.get("keywords") or None,
        "categories": None,
        "upload_date": ud.replace("-", "") or None,
    }
    return info, None


def _apply_metadata_to_md(doc: MdDoc, info: dict) -> bool:
    """Apply a fresh yt-dlp info dict to a stored YouTube markdown doc in
    place: fill an empty `## Description` section, fix the `- Duration:` and
    `- Published: unknown` body lines, and refresh the duration/language/
    extra (uploader, view count, tags…) front-matter. Always stamps
    `metadata_synced_at` so backfill runs are resumable. Returns True when
    any visible content changed."""
    front, body = doc.front, doc.body
    changed = False

    duration = info.get("duration")
    if duration and front.get("duration_sec") != duration:
        front["duration_sec"] = duration
        changed = True
    lang = info.get("language")
    if lang and front.get("language") != lang:
        front["language"] = lang
        changed = True
    extra = front.get("extra") or {}
    front["extra"] = extra
    for key in ("uploader", "uploader_id", "view_count", "tags", "categories"):
        val = info.get(key)
        if val and extra.get(key) != val:
            extra[key] = val
            changed = True

    if duration:
        body = re.sub(r"^- Duration: .*$", f"- Duration: {duration} sec",
                      body, count=1, flags=re.M)

    # The body Published line: prefer the fresh upload_date, else a date the
    # offline passes already stamped into front-matter (backfill-dates fixed
    # front-matter + DB but left the body line saying "unknown").
    pub: str | None = None
    ud = info.get("upload_date")
    if ud and len(ud) == 8:
        pub = f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}"
    else:
        fpa = front.get("published_at")
        if isinstance(fpa, datetime):
            pub = fpa.date().isoformat()
        elif isinstance(fpa, str) and re.match(r"\d{4}-\d{2}-\d{2}", fpa):
            pub = fpa[:10]
    if pub:
        body = re.sub(r"^- Published: .*$", f"- Published: {pub}",
                      body, count=1, flags=re.M)

    desc = (info.get("description") or "").strip()
    if desc:
        body = re.sub(
            r"(^## Description[ \t]*\n)\s*(?=^## |\Z)",
            lambda mm: mm.group(1) + "\n" + desc + "\n\n",
            body, count=1, flags=re.M)

    if body != doc.body:
        doc.body = body
        changed = True
    front["metadata_synced_at"] = datetime.now(timezone.utc).isoformat()
    return changed


async def backfill_metadata(
    *, limit: int = 0, dry_run: bool = False, proxy_hosts: str = "",
) -> dict[str, int]:
    """Re-fetch yt-dlp metadata for YouTube items whose scrape lost it.

    Videos scraped while yt-dlp's metadata fetch was failing (dead SOCKS
    tunnel / HTTP 429 during the 2026-07/08 bulk scrapes) were saved from the
    ``{id, title, upload_date}`` stub in ``fetch()``: empty ``## Description``
    section and null duration/uploader/view-count front-matter. This finds
    those files, re-fetches metadata per video, rewrites and re-ingests each.

    With ``proxy_hosts`` (or the ``YT_DLP_PROXY_HOSTS`` env default) the run
    opens the SSH SOCKS5 pool and processes candidates in parallel — one
    worker per tunnel, each paced at the standard proxied scrape interval so
    every egress IP sees the same request rate the nightly scrape produces.
    Without hosts it goes direct, one video at a time at the (larger) direct
    interval. Resumable: processed files carry ``metadata_synced_at`` in
    front-matter. Rate-limit-aware like the transcript backfill: a blocked
    fetch triggers an exponential cooldown (5 min → doubling, capped 1 h) and
    the same video is retried after it; blocks retire a worker (or abort the
    direct run) after 3 consecutive hits."""
    from ..db import engine as db_engine
    with db_engine().connect() as conn:
        rows = conn.execute(sa_text("""
            SELECT i.external_id, i.md_path
            FROM item i JOIN source s ON s.id = i.source_id
            WHERE s.code = 'youtube' AND i.md_path IS NOT NULL
            ORDER BY i.id
        """)).fetchall()

    candidates: list[tuple[str, Path]] = []
    for vid, mp in rows:
        p = Path(mp)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "metadata_synced_at" in text:
            continue
        if not _EMPTY_DESC_RE.search(text):
            continue
        candidates.append((vid, p))
        if limit and limit > 0 and len(candidates) >= limit:
            break

    sc = YouTubeScraper()
    stats = {"candidates": len(candidates), "updated": 0, "empty": 0,
             "unavailable": 0, "blocked": 0, "failed": 0,
             "missing_file": 0, "aborted": 0}

    hosts = parse_hosts(proxy_hosts or settings().yt_dlp_proxy_hosts)
    pool = None
    urls: list[str] = []
    if hosts and not dry_run:
        pool = ProxyPool(hosts)
        urls = pool.start()
        if urls:
            sc.log.info("metadata backfill: %d SOCKS tunnel(s) up (%s); "
                        "one parallel worker each", len(urls), ", ".join(hosts))
        else:
            sc.log.warning("no tunnels came up; falling back to direct")
    try:
        if urls:
            await _pooled_metadata_loop(sc, urls, candidates,
                                        dry_run=dry_run, stats=stats)
            return stats

        # Direct sequential mode — the single residential IP gets the
        # larger direct interval, one video at a time.
        from .. import ingest as ingest_mod
        consecutive_blocked = 0
        backoff_s = 300.0
        i = 0
        while i < len(candidates):
            vid, p = candidates[i]
            url = f"https://www.youtube.com/watch?v={vid}"
            cp = await sc._polite_ytdlp_direct(
                url, "--skip-download", "--dump-json", "--no-warnings", url)
            info = None
            if cp.returncode == 0 and cp.stdout.strip():
                try:
                    info = json.loads(cp.stdout.splitlines()[0])
                except Exception:
                    info = None
            if info is None:
                err_class = _err_class((cp.stderr or "") + (cp.stdout or ""))
                if err_class == "blocked":
                    # YouTube is throttling this IP: exponential cooldown
                    # (5 min → doubling, capped 1 h), then retry the SAME
                    # video so a blip doesn't strand it. Abort after 3
                    # consecutive — the run is resumable.
                    sc.limiter.report_429(url)
                    stats["blocked"] += 1
                    consecutive_blocked += 1
                    if consecutive_blocked >= 3:
                        sc.log.warning(
                            "metadata backfill: %s blocked (%d/3) — aborting "
                            "the sweep to protect the IP; rerun resumes as-is",
                            vid, consecutive_blocked)
                        stats["aborted"] = 1
                        break
                    sc.log.warning(
                        "metadata backfill: %s blocked (%d/3); cooling down "
                        "%.0fs then retrying", vid, consecutive_blocked, backoff_s)
                    await asyncio.sleep(backoff_s)
                    backoff_s = min(backoff_s * 2, 3600.0)
                    continue
                consecutive_blocked = 0
                backoff_s = 300.0
                if err_class == "gone":
                    stats["unavailable"] += 1
                else:
                    stats["failed"] += 1
                i += 1
                continue
            consecutive_blocked = 0
            backoff_s = 300.0
            if p.is_file():
                _apply_metadata_save(p, info, dry_run, stats, ingest_mod)
            else:
                stats["missing_file"] += 1
            i += 1
        return stats
    finally:
        if pool is not None:
            pool.stop()


def _apply_metadata_save(
    p: Path, info: dict, dry_run: bool,
    stats: dict[str, int], ingest_mod,
) -> None:
    """Rewrite one file with a fresh info dict, count the outcome, and
    re-ingest (skipped in dry-run)."""
    doc = load_md(p)
    changed = _apply_metadata_to_md(doc, info)
    if changed:
        stats["updated"] += 1
    else:
        stats["empty"] += 1  # video genuinely has no description
    if dry_run:
        return
    doc.write(p)
    if changed:
        try:
            ingest_mod.ingest_file(p)
        except Exception:  # noqa: BLE001
            # Ingest failing must not abort the sweep — a later
            # `kb ingest` pass picks the file up.
            pass


async def _pooled_metadata_loop(
    sc: YouTubeScraper, urls: list[str],
    candidates: list[tuple[str, Path]],
    *, dry_run: bool, stats: dict[str, int],
) -> None:
    """Parallel metadata backfill, one asyncio worker per SOCKS tunnel.

    Each worker fetches via ``_watch_page_info`` (one plain GET of the /watch
    HTML — the innertube API yt-dlp uses is bot-challenged on cloud egress
    IPs, the watch page is not) with a yt-dlp attempt as fallback, both in
    threads (`asyncio.to_thread`) so the workers genuinely overlap. Workers
    pace themselves at the standard proxied-scrape interval (each egress IP
    sees the same rate the nightly scrape produces), classify failures via
    ``_err_class``, retry the same video after an exponential cooldown on
    blocks, and retire after 3 consecutive blocks (flagged IP) or 5
    consecutive unknown failures (dead tunnel) — the unfinished item goes
    back on the queue for the other workers. The run ends when the queue
    empties or every worker has retired."""
    from .. import ingest as ingest_mod

    stats["workers"] = len(urls)
    interval = max(settings().youtube_rate_limit_sec,
                   settings().scrape_rate_limit_sec)
    queue: asyncio.Queue[tuple[str, Path]] = asyncio.Queue()
    for c in candidates:
        queue.put_nowait(c)

    async def worker(proxy_url: str) -> None:
        consecutive_blocked = 0
        consecutive_unknown = 0
        backoff_s = 300.0
        current: tuple[str, Path] | None = None
        while True:
            if current is None:
                try:
                    current = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
            vid, p = current
            url = f"https://www.youtube.com/watch?v={vid}"
            await asyncio.sleep(interval + random.uniform(0, 1.5))
            # Fast path: one plain GET of the /watch HTML carries the same
            # metadata dump-json returns, and — unlike the innertube API
            # yt-dlp uses — the page is served to egress IPs that
            # bot-challenge yt-dlp.
            info, err_class = await asyncio.to_thread(
                _watch_page_info, vid, proxy_url)
            if info is None and err_class is None:
                # Unknown GET outcome — one yt-dlp attempt through the same
                # tunnel before classifying the item.
                cp = await asyncio.to_thread(
                    sc._ytdlp,
                    "--skip-download", "--dump-json", "--no-warnings", url,
                    proxy=proxy_url)
                if cp.returncode == 0 and cp.stdout.strip():
                    try:
                        info = json.loads(cp.stdout.splitlines()[0])
                    except Exception:
                        info = None
                if info is None:
                    err_class = _err_class((cp.stderr or "") + (cp.stdout or ""))
            if info is not None:
                consecutive_blocked = 0
                backoff_s = 300.0
                consecutive_unknown = 0
                if p.is_file():
                    _apply_metadata_save(p, info, dry_run, stats, ingest_mod)
                else:
                    stats["missing_file"] += 1
                current = None
                continue
            if err_class == "blocked":
                stats["blocked"] += 1
                consecutive_blocked += 1
                consecutive_unknown = 0
                if consecutive_blocked >= 3:
                    sc.log.warning(
                        "worker %s: 3 consecutive blocks — retiring, "
                        "item %s re-queued", proxy_url, vid)
                    queue.put_nowait(current)
                    current = None
                    return
                sc.log.warning(
                    "worker %s: %s blocked (%d/3); cooling down %.0fs "
                    "then retrying", proxy_url, vid,
                    consecutive_blocked, backoff_s)
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, 3600.0)
                continue  # retry the same video after cooldown
            consecutive_blocked = 0
            backoff_s = 300.0
            if err_class == "gone":
                stats["unavailable"] += 1
                current = None
                continue
            stats["failed"] += 1
            consecutive_unknown += 1
            if consecutive_unknown >= 5:
                # Repeated unclassifiable failures = the tunnel is dead
                # (SOCKS EOF / connection resets), not the video.
                sc.log.warning(
                    "worker %s: repeated unknown failures — tunnel likely "
                    "dead, retiring; item %s re-queued", proxy_url, vid)
                queue.put_nowait(current)
                current = None
                return
            current = None

    await asyncio.gather(*(worker(u) for u in urls))
    if not queue.empty():
        stats["aborted"] = 1


def _update_channel_name(handle: str, name: str) -> None:
    """Persist a YouTube channel display name resolved from yt-dlp."""
    try:
        from ..db import engine as db_engine
        with db_engine().begin() as conn:
            sid = conn.execute(
                sa_text("SELECT id FROM source WHERE code='youtube'")
            ).scalar_one_or_none()
            if sid is None:
                return
            conn.execute(sa_text(
                "UPDATE channel SET name = :n "
                "WHERE source_id = :s AND handle = :h"
            ), {"s": sid, "h": handle, "n": name})
    except Exception:
        pass


class YouTubeScraper(BaseScraper):
    code = "youtube"
    name = "YouTube"

    def __init__(self) -> None:
        super().__init__()
        if not shutil.which("yt-dlp"):
            self.log.warning("yt-dlp not on PATH; will try via 'python -m yt_dlp'")
        # YouTube's timedtext/subtitle endpoint rate-limits hard (HTTP 429),
        # so use a more generous interval than the global default.
        s = settings()
        interval = max(s.youtube_rate_limit_sec, s.scrape_rate_limit_sec)
        self.limiter = HostRateLimiter(interval, jitter=1.5,
                                       max_backoff=180.0, backoff_step=15.0)
        # When the SOCKS5 proxy pool is dead and yt-dlp falls back to a direct
        # connection, YouTube throttles the single residential IP much harder
        # than a round-robined egress pool. This larger base interval is applied
        # proactively (per-call, before any 429) so direct scrapes slow down
        # enough to avoid being rate-limited in the first place.
        self.direct_interval = max(s.youtube_direct_rate_limit_sec,
                                   s.scrape_rate_limit_sec)
        # Optional round-robin proxy pool (SSH SOCKS5 tunnels). Set by the CLI
        # before run(); None = direct connection.
        self.proxy_pool = None  # type: ignore[assignment]
        # Cache for _known_video_ids(): external_ids of already-ingested
        # videos whose md file exists. None = not loaded yet.
        self._known_ids: set[str] | None = None

    # ---- helpers ---------------------------------------------------------
    def _next_proxy(self) -> str | None:
        """The proxy URL for the next yt-dlp call: round-robin across the
        pool if set, else the static ``YT_DLP_PROXY`` env setting, else None.

        Single source of truth for proxied-vs-direct. Call once per
        ``_polite_ytdlp`` (each call advances the round-robin), and pass the
        result into ``_ytdlp`` so it isn't resolved twice."""
        if self.proxy_pool is not None:
            return self.proxy_pool.next()
        p = settings().yt_dlp_proxy
        return p or None

    def _ytdlp(self, *args: str, proxy: str | None = None, **kw) -> subprocess.CompletedProcess:
        import sys
        cmd = (["yt-dlp"] if shutil.which("yt-dlp")
               else [sys.executable, "-m", "yt_dlp"]) + list(args)
        cb = settings().yt_dlp_cookies_from_browser
        if cb:
            cmd += ["--cookies-from-browser", cb]
        # Deno JS runtime: YouTube now requires JavaScript execution for proper
        # extraction; without it yt-dlp warns "extraction deprecated, formats
        # may be missing" and subtitle downloads fail. Auto-detected if present.
        deno = _find_deno()
        if deno:
            cmd += ["--js-runtimes", f"deno:{deno}"]
        if proxy:
            # --force-ipv4 avoids SOCKS5 "4 bytes missing" chunking errors.
            # Retries + socket timeout let yt-dlp recover when a tunnel drops
            # mid-transfer (it will reconnect over a fresh proxy connection).
            cmd += ["--proxy", proxy, "--force-ipv4",
                    "--retries", "8", "--fragment-retries", "8",
                    "--socket-timeout", "30"]
        else:
            # Direct (no proxy / pool dead). Retries + socket timeout still help
            # recover from the timedtext endpoint's intermittent 429s; the
            # proactive rate limit in _polite_ytdlp keeps the request rate low
            # enough that YouTube rarely throttles a direct residential IP.
            cmd += ["--retries", "8", "--fragment-retries", "8",
                    "--socket-timeout", "30"]
        cmd += ["--user-agent", settings().scrape_user_agent]
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", **kw)

    async def _polite_ytdlp(
        self,
        url: str,
        *args: str,
        **kw,
    ) -> subprocess.CompletedProcess:
        # Resolve proxied-vs-direct once: this both advances the round-robin a
        # single step and tells us which base interval to apply. A direct call
        # (pool dead / not configured) uses the larger direct_interval so the
        # single residential IP isn't hammered into a 429 cascade.
        proxy = self._next_proxy()
        if proxy is None:
            await self.limiter.wait(url, min_interval=self.direct_interval)
        else:
            await self.limiter.wait(url)
        return self._ytdlp(*args, proxy=proxy, **kw)

    async def _polite_ytdlp_direct(
        self,
        url: str,
        *args: str,
        **kw,
    ) -> subprocess.CompletedProcess:
        """Rate-limited **direct** (no proxy) yt-dlp call. Used for
        single-video metadata recovery when the normal (proxied) path came
        back empty: a residential IP fetching one /watch page is exactly the
        case the proxy isn't needed for, and it's what salvages the
        upload date that the undated fallback would otherwise lose."""
        await self.limiter.wait(url, min_interval=self.direct_interval)
        return self._ytdlp(*args, **kw)

    async def _lookup_upload_date_direct(self, video_id: str) -> str | None:
        """Fetch the ``/watch`` page HTML directly (residential IP — same
        design as ``_fetch_transcript_api``) and pull the upload/publish date
        YouTube embeds in its initial player response. Returns YYYYMMDD or
        None. Third metadata source after yt-dlp's info-json and dump-json:
        works even when yt-dlp's extraction is blocked, since it's just the
        plain HTML page."""
        import requests
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
            return None
        # Pinned https + fixed host, redirects disabled: the request can
        # never leave YouTube regardless of the video id.
        url = "https://www.youtube.com/watch"
        await self.limiter.wait(url, min_interval=self.direct_interval)

        def _get():
            try:
                return requests.get(
                    url, params={"v": video_id},
                    headers={"User-Agent": settings().scrape_user_agent,
                             "Accept-Language": "en-US,en;q=0.9"},
                    timeout=15, allow_redirects=False)
            except requests.RequestException:
                return None

        r = await asyncio.to_thread(_get)
        if r is None or r.status_code != 200:
            return None
        return _extract_upload_date_from_html(r.text)

    def _fetch_transcript_api(self, video_id: str) -> str:
        """Fetch a transcript via the youtube-transcript-api library, going
        **direct** (residential IP) and carrying the local browser's YouTube
        cookies — the same cookies yt-dlp gets via ``--cookies-from-browser``.
        Returns the transcript text, or empty string if unavailable.

        Note on proxies: yt-dlp (the primary fetcher) routes through the SOCKS5
        pool because it hits the innertube API, which tolerates cloud IPs. But
        youtube-transcript-api scrapes the ``/watch`` page, which YouTube
        blocks from cloud-provider IP ranges (Oracle/AWS/GCP/Azure). So this
        fallback deliberately does NOT use the proxy — a residential IP works
        where every proxy egress gets ``RequestBlocked``."""
        import requests
        session = requests.Session()
        # Cookies: mirror yt-dlp's --cookies-from-browser so the in-process
        # library is authenticated the same way the subprocess is.
        cb = settings().yt_dlp_cookies_from_browser
        if cb:
            try:
                import browser_cookie3
                # browser_cookie3 exposes chrome/firefox/edge/... loaders by name.
                loader = getattr(browser_cookie3, cb, None)
                if loader is None:
                    # Support "chrome:Profile" style specs like substack does.
                    loader = getattr(browser_cookie3, cb.split(":")[0], None)
                if loader is not None:
                    session.cookies.update(loader(domain_name=".youtube.com"))
            except Exception:  # noqa: BLE001
                self.log.debug("browser cookie load failed for transcript-api",
                               exc_info=True)
        from youtube_transcript_api import YouTubeTranscriptApi
        # Try preferred languages first (Cantonese original + English +
        # Chinese), then fall back to whatever the video actually has. Many
        # videos have auto-captions only in their original language (e.g. a
        # Dutch video has Dutch captions, a Korean video has Korean, a
        # Cantonese video has `yue`), so a narrow filter would miss them.
        # `yue` first: the original ASR track beats its zh-Hant/en machine
        # translations for fidelity.
        preferred = ["yue", "en", "zh-Hant", "zh-Hans", "zh"]
        # New API (v1.0+): constructor takes http_client; .fetch() per video.
        # Old API: classmethod .get_transcript(video_id, languages=...).
        old_api = hasattr(YouTubeTranscriptApi, "get_transcript")

        # Attempt 1: preferred languages.
        try:
            if old_api:
                tx = YouTubeTranscriptApi.get_transcript(video_id,
                                                          languages=preferred,
                                                          cookies=session.cookies)
                return "\n".join(seg["text"] for seg in tx)
            api = YouTubeTranscriptApi(http_client=session)
            fetched = api.fetch(video_id, languages=preferred)
            return "\n".join(s.text for s in fetched)
        except Exception:
            pass  # preferred langs not available — discover what IS available

        # Attempt 2: list available transcripts and fetch whichever exists
        # (manual > auto-generated), in any language. This catches the Korean-
        # only / Dutch-only / etc. videos that the preferred-language filter
        # would otherwise skip.
        try:
            if old_api:
                # Old API has no list(); just call without language filter.
                tx = YouTubeTranscriptApi.get_transcript(video_id,
                                                          cookies=session.cookies)
                return "\n".join(seg["text"] for seg in tx)
            api = YouTubeTranscriptApi(http_client=session)
            transcript_list = api.list(video_id)
            # Find the best available: manually-created first, then generated.
            best = next(iter(transcript_list), None)
            if best is not None:
                fetched = best.fetch()
                return "\n".join(s.text for s in fetched)
        except Exception:
            pass
        return ""

    def resolve_channel_display_name(self, handle: str) -> str | None:
        """Return the channel title YouTube reports for *handle* (via yt-dlp)."""
        return asyncio.run(self.resolve_channel_display_name_async(handle))

    async def resolve_channel_display_name_async(self, handle: str) -> str | None:
        """Return the channel title YouTube reports for *handle* (via yt-dlp)."""
        url = _channel_videos_url(handle)
        cp = await self._polite_ytdlp(
            url,
            "--no-update",
            "--dump-single-json",
            "--flat-playlist",
            "--playlist-end", "1",
            "--ignore-errors",
            url,
        )
        if cp.returncode != 0 and not cp.stdout.strip():
            self.log.warning(
                "resolve channel name failed for %s :: %s",
                handle, cp.stderr[-400:],
            )
            return None
        return _parse_channel_display_name(cp.stdout)

    async def run(self, limit: int | None = None) -> list[Path]:
        out: list[Path] = []
        async for d in self._recording_discover(limit=limit):
            if self.already_scraped(d):
                self.log.info("skip (cached) %s", d.get("url") or d.get("external_id"))
                continue
            try:
                item = await self.fetch(d)
            except Exception as exc:  # noqa: BLE001
                self.log.exception("fetch failed: %s :: %s", d, exc)
                continue
            if item is None:
                continue
            out.append(self.write_md(item))
            # A just-written file counts as known for the rest of this run.
            if self._known_ids is not None:
                self._known_ids.add(item.external_id)
        return out

    @staticmethod
    def _vtt_to_text(vtt: str) -> str:
        return _vtt_to_paragraphs(vtt)

    # Language preference when several subtitle tracks were downloaded for
    # one video: the original Cantonese ASR track (`yue-orig`) first — an
    # original always beats its machine translations — then written Chinese,
    # then English, then whatever else yt-dlp saved.
    _VTT_LANG_PREF = ["yue-orig", "yue", "zh-hant", "zh-hans", "zh", "en"]

    @classmethod
    def _pick_vtt(cls, paths) -> Path | None:
        best: Path | None = None
        best_key: tuple[int, str] | None = None
        for p in paths:
            stem = p.stem
            lang = stem.split(".", 1)[1].lower() if "." in stem else ""
            rank = next(
                (i for i, pref in enumerate(cls._VTT_LANG_PREF)
                 if lang == pref or lang.startswith(pref + "-")),
                len(cls._VTT_LANG_PREF),
            )
            key = (rank, lang)
            if best_key is None or key < best_key:
                best, best_key = p, key
        return best

    # ---- discover --------------------------------------------------------
    async def discover(self, limit: int | None = None) -> AsyncIterator[dict]:
        dir_slugs = _channel_dir_slugs()
        for handle, display in _load_channels():
            resolved = await self.resolve_channel_display_name_async(handle)
            if resolved:
                if resolved != display:
                    _update_channel_name(handle, resolved)
                display = resolved
            # Pin the storage folder slug so display-name changes can't fork
            # the channel's files into a second folder later.
            dir_slug = dir_slugs.get(handle) or channel_dir_slug(display)
            _pin_channel_dir(handle, dir_slug)
            url = _channel_videos_url(handle)
            self.log.info("discovering %s", url)
            args = ["--flat-playlist", "--dump-json", "--ignore-errors", url]
            # Only cap per-channel when caller explicitly passed a limit.
            if limit:
                args = ["--flat-playlist", "--dump-json",
                        "--playlist-end", str(limit),
                        "--ignore-errors", url]
            cp = await self._polite_ytdlp(url, *args)
            if cp.returncode != 0:
                self.log.warning("discover failed for %s :: %s",
                                 handle, cp.stderr[-400:])
            for line in cp.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                vid = j.get("id")
                if not vid:
                    continue
                yield {
                    "external_id": vid,
                    "url": j.get("url") or f"https://www.youtube.com/watch?v={vid}",
                    "title": j.get("title") or vid,
                    "channel_handle": handle,
                    "channel_name": display,
                    "channel_dir_slug": dir_slug,
                    "duration": j.get("duration"),
                    "upload_date": j.get("upload_date"),
                }
                await asyncio.sleep(0)

    def _known_video_ids(self) -> set[str]:
        """external_ids of YouTube items whose markdown still exists on disk.

        Loaded once per run. The file-existence filter matters: an item whose
        md file has vanished (e.g. scraped inside the Jenkins container with
        a non-persistent mount) must be re-fetched, not skipped.
        """
        if self._known_ids is None:
            ids: set[str] = set()
            try:
                from ..db import engine as db_engine
                with db_engine().connect() as conn:
                    rows = conn.execute(sa_text(
                        "SELECT i.external_id, i.md_path FROM item i "
                        "JOIN source s ON i.source_id = s.id "
                        "WHERE s.code = 'youtube'"
                    )).fetchall()
                ids = {eid for eid, mp in rows if mp and Path(mp).exists()}
            except Exception:
                self.log.debug("known-video-ids load failed", exc_info=True)
            self._known_ids = ids
        return self._known_ids

    def already_scraped(self, d: dict) -> bool:
        # Authoritative cross-name dedup: if the item table already has this
        # video AND its markdown file still exists, it is scraped. The disk
        # check below only sees folders derivable from the *current* channel
        # name/handle — display-name drift (MacroVoices → Macro Voices)
        # historically blinded it and re-downloaded whole channels.
        if d.get("external_id") in self._known_video_ids():
            return True
        slugs = dict.fromkeys([
            channel_dir_slug(d["channel_name"]),
            channel_dir_slug(d["channel_handle"]),
            d.get("channel_dir_slug") or channel_dir_slug(d["channel_name"]),
        ])
        for ch in slugs:
            md_path = _youtube_md_path(
                ch,
                upload_date=d.get("upload_date"),
                title=d.get("title") or d["external_id"],
                external_id=d["external_id"],
            )
            if not md_path.exists() or md_path.stat().st_size <= 200:
                continue
            # A non-trivial md file means the video was fetched. Do NOT require
            # published_at here: videos with no upload_date in their metadata
            # are saved as undated-*.md with published_at=None and can never
            # gain a date, so requiring one re-fetches them on every run —
            # burning the per-channel nightly budget before new videos.
            return True
        return False

    # ---- fetch -----------------------------------------------------------
    async def fetch(self, d: dict) -> ScrapedItem | None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cp = await self._polite_ytdlp(
                d["url"],
                "--skip-download",
                "--write-info-json",
                "--write-auto-subs", "--write-subs",
                # Cantonese auto-CC is published under `yue` — the original
                # ASR track is `yue-orig`, and zh-Hans/zh-Hant/en exist only
                # as auto-translations of it — so a zh-only filter comes home
                # empty for Cantonese-first channels (e.g. Dr Ng's LATP).
                "--sub-langs", "yue.*,en.*,zh.*,yue,en,zh",
                "--convert-subs", "vtt",
                "--ignore-errors", "--no-abort-on-error",
                "--no-warnings",
                "-o", str(tmp / "%(id)s.%(ext)s"),
                d["url"],
            )
            if cp.returncode != 0:
                self.log.warning("yt-dlp fetch err %s :: %s",
                                 d["external_id"], cp.stderr[-300:])
                # Detect 429 in yt-dlp's stderr and back off so the next
                # request waits longer (exponential, set in HostRateLimiter).
                if "429" in cp.stderr:
                    new_int = self.limiter.report_429(d["url"])
                    self.log.info("429 detected for %s; backing off → %.0fs interval",
                                  d["external_id"], new_int)
            info_path = tmp / f"{d['external_id']}.info.json"
            if not info_path.exists():
                # fallback: just dump metadata
                cp2 = await self._polite_ytdlp(
                    d["url"], "--skip-download", "--dump-json",
                    "--no-warnings", d["url"])
                info = None
                if cp2.returncode == 0 and cp2.stdout.strip():
                    try:
                        info = json.loads(cp2.stdout.splitlines()[0])
                    except Exception:
                        pass
                if info is None:
                    # Metadata recovery: the proxied dump failed (a dead
                    # tunnel or a 429 yields exactly the all-null metadata
                    # that strands videos as undated). One direct attempt —
                    # a residential IP fetching a single /watch page works
                    # where the proxy egress failed.
                    cp3 = await self._polite_ytdlp_direct(
                        d["url"], "--skip-download", "--dump-json",
                        "--no-warnings", d["url"])
                    if cp3.returncode == 0 and cp3.stdout.strip():
                        try:
                            info = json.loads(cp3.stdout.splitlines()[0])
                        except Exception:
                            pass
                if info is None:
                    info = {"id": d["external_id"], "title": d["title"],
                            "upload_date": d.get("upload_date")}
            else:
                info = json.loads(info_path.read_text("utf-8"))

            if not info.get("upload_date"):
                # Metadata came back empty everywhere yt-dlp tried. Two more
                # sources, in order: the /watch page's embedded uploadDate
                # (direct, same design as the transcript fallback) and a
                # date parsed from the title ("…| 15May2022").
                ud = await self._lookup_upload_date_direct(d["external_id"])
                src = "watch-page"
                if not ud:
                    ud = _date_from_title(d.get("title"))
                    src = "title"
                if ud:
                    self.log.info("upload date for %s recovered via %s fallback",
                                  d["external_id"], src)
                    info["upload_date"] = ud

            # Pick the best vtt by language preference (original Cantonese
            # ASR > its translations) instead of glob's arbitrary order.
            vtt = self._pick_vtt(tmp.glob(f"{d['external_id']}*.vtt"))
            transcript_text = self._vtt_to_text(vtt.read_text("utf-8")) if vtt else ""

        if not transcript_text:
            # Last-ditch: youtube-transcript-api. This library uses requests
            # in-process, so it does NOT pick up yt-dlp's --proxy flag or its
            # --cookies-from-browser. Build a requests.Session carrying both
            # the proxy (round-robin from the pool, or the static env setting)
            # and the YouTube cookie jar from the local browser, then pass it
            # as the library's http_client.
            # _fetch_transcript_api goes *direct* by design (residential IP
            # works where proxy egress is blocked), so use the direct interval.
            await self.limiter.wait(d["url"], min_interval=self.direct_interval)
            try:
                transcript_text = self._fetch_transcript_api(d["external_id"])
            except Exception as exc:  # noqa: BLE001
                self.log.info("no transcript for %s :: %s", d["external_id"], exc)
                # If the transcript-api hit a rate-limit or IP block, back off
                # so subsequent videos aren't hammered.
                estr = str(exc).lower()
                if "429" in estr or "rate" in estr or "blocked" in estr:
                    new_int = self.limiter.report_429(d["url"])
                    self.log.info("transcript-api rate-limited for %s; "
                                  "backing off → %.0fs interval",
                                  d["external_id"], new_int)

        upload_date = info.get("upload_date")
        published_at = (datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                        if upload_date else None)

        title = info.get("title") or d["title"]
        date_part = published_at.strftime("%Y-%m-%d") if published_at else "undated"
        folder_name = f"{date_part}-{slugify(title, 80)}"
        body = (
            f"# {title}\n\n"
            f"- Channel: {d['channel_name']} ({d['channel_handle']})\n"
            f"- URL: {d['url']}\n"
            f"- Published: {published_at.date() if published_at else 'unknown'}\n"
            f"- Duration: {info.get('duration')} sec\n\n"
            f"## Description\n\n{(info.get('description') or '').strip()}\n\n"
            f"## Transcript\n\n{transcript_text or NO_TRANSCRIPT_MARKER}\n"
        )

        return ScrapedItem(
            source="youtube",
            channel=d["channel_handle"],
            channel_name=d["channel_name"],
            channel_dir=d.get("channel_dir_slug") or d["channel_name"],
            external_id=d["external_id"],
            title=title,
            url=d["url"],
            published_at=published_at,
            duration_sec=info.get("duration"),
            language=info.get("language") or "en",
            body_md=body,
            folder_name=folder_name,
            flat_layout=True,
            has_transcript=bool(transcript_text and transcript_text.strip()),
            extra={
                "uploader": info.get("uploader"),
                "uploader_id": info.get("uploader_id"),
                "view_count": info.get("view_count"),
                "tags": info.get("tags"),
                "categories": info.get("categories"),
            },
        )
