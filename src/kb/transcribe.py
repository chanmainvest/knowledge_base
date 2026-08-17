"""Whisper ASR transcription for YouTube items without subtitles.

For YouTube items where no subtitle/transcript could be fetched
(``has_transcript=false``), this module:

1. Downloads the audio track (m4a) via yt-dlp to a transient audio dir under
   ``data/raw/youtube/tmp/`` (same ``data/raw/<source>/`` layout as the other
   raw artefacts; gitignored, deleted after each item).
2. Runs faster-whisper on GPU (one video at a time — no parallel GPU load).
3. Writes the generated transcript into the existing ``.md`` file (replacing
   the ``_(no transcript available)_`` placeholder) and re-ingests to update
   the DB.
4. Tracks the full lifecycle in the ``item.transcription_status`` column:
   ``pending → audio_downloaded → transcribing → done`` (or ``failed``).

Transcription is **disabled by default** everywhere: ``kb youtube scrape``
only transcribes when passed ``--transcribe`` (or when ``WHISPER_ENABLED``
is set in ``.env``), and the dedicated command is ``kb youtube transcribe``.

Usage::

    # Transcribe all pending (one at a time)
    kb youtube transcribe

    # Test with one Cantonese video from latp channel
    kb youtube transcribe --channel latp --limit 1

    # List candidates without transcribing
    kb youtube transcribe --list

    # Reset items stuck in 'transcribing' (e.g. after a crash) back to 'pending'
    kb youtube transcribe --reset-stuck

    # Re-attempt items that previously failed
    kb youtube transcribe --retry-failed
"""
from __future__ import annotations

import re as _re
import shutil
import subprocess
import time
from pathlib import Path

from sqlalchemy import text

from .config import DATA_DIR, settings
from .db import engine
from .ingest import ingest_file
from .io_md import load_md
from .logging_setup import get_logger
from .scrapers.youtube import NO_TRANSCRIPT_MARKER, _find_deno

log = get_logger("transcribe")

_AUDIO_EXTS = {".m4a", ".webm", ".mp3", ".opus", ".wav", ".mp4"}
# YouTube video ids: 11 chars of [A-Za-z0-9_-]. Anything else never reaches
# yt-dlp's command line.
_VIDEO_ID_RE = _re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def audio_tmp_dir() -> Path:
    """Resolve the transient audio dir (``WHISPER_TMP_DIR``).

    Relative paths resolve against ``DATA_DIR`` (default
    ``data/raw/youtube/tmp`` — same ``data/raw/<source>/`` layout as the
    other raw artefacts); absolute paths are used as-is. Files are deleted
    after each item; the directory is empty in steady state.
    """
    raw = settings().whisper_tmp_dir
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (DATA_DIR / p)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def gather_candidates(
    limit: int,
    channel: str | None = None,
    retry_failed: bool = False,
    external_ids: list[str] | None = None,
) -> list[dict]:
    """Return YouTube item rows queued for transcription.

    By default selects items with ``transcription_status = 'pending'`` (or
    NULL). With ``retry_failed=True``, also includes ``'failed'`` items.
    ``external_ids`` restricts to a known set of videos (used by
    ``kb youtube scrape --transcribe`` to transcribe only what it just
    fetched). ``channel`` is a substring match on the channel handle/name,
    applied in Python so no user-supplied pattern reaches the SQL.
    """
    statuses = ["pending"] if not retry_failed else ["pending", "failed"]
    params = {
        "statuses": statuses,
        "vids": list(external_ids) if external_ids is not None else None,
        # LIMIT NULL in Postgres means no limit. When a channel filter is
        # given it is applied in Python, so the SQL limit must be deferred —
        # otherwise LIMIT would slice the wrong rows before the filter runs.
        "lim": limit if (limit and not channel) else None,
    }
    with engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
            SELECT i.id, i.external_id, i.title, i.md_path, i.duration_sec,
                   i.transcription_status,
                   ch.handle AS channel_handle, ch.name AS channel_name
            FROM item i
            JOIN source s ON s.id = i.source_id
            LEFT JOIN channel ch ON ch.id = i.channel_id
            WHERE s.code = 'youtube'
              AND i.has_transcript = false
              AND (i.transcription_status = ANY(CAST(:statuses AS text[]))
                   OR i.transcription_status IS NULL)
              AND (CAST(:vids AS text[]) IS NULL
                   OR i.external_id = ANY(CAST(:vids AS text[])))
            ORDER BY COALESCE(i.duration_sec, 999999999) ASC, i.id
            LIMIT CAST(:lim AS bigint)
        """), params).mappings().all()]
    if channel:
        needle = channel.lower()
        rows = [r for r in rows
                if needle in (r.get("channel_handle") or "").lower()
                or needle in (r.get("channel_name") or "").lower()]
        if limit:
            rows = rows[:limit]
    return rows


def reset_stuck() -> int:
    """Re-queue items stuck mid-flight back to 'pending'. Returns count.

    Covers both 'transcribing' and 'audio_downloaded' — a run killed between
    the two leaves rows in the latter, and the audio file is transient anyway
    (re-downloaded on the next attempt).
    """
    with engine().begin() as conn:
        result = conn.execute(text("""
            UPDATE item SET transcription_status = 'pending'
            WHERE transcription_status IN ('transcribing', 'audio_downloaded')
            RETURNING id
        """))
        ids = result.scalars().all()
    return len(ids)


def update_status(
    item_id: int,
    status: str,
    error: str | None = None,
    language: str | None = None,
) -> None:
    """Update the transcription_status (and related fields) for an item.

    ``error``/``language`` are left untouched when None; a ``status`` of
    ``'done'`` also stamps ``transcribed_at`` and flips ``has_transcript``.
    """
    with engine().begin() as conn:
        conn.execute(text("""
            UPDATE item SET
              transcription_status = :st,
              transcription_error = COALESCE(:err, transcription_error),
              transcription_language = COALESCE(:lang, transcription_language)
            WHERE id = :id
        """), {
            "st": status,
            "err": error[:1000] if error else None,
            "lang": language,
            "id": item_id,
        })
    if status == "done":
        with engine().begin() as conn:
            conn.execute(text("""
                UPDATE item SET transcribed_at = now(), has_transcript = true
                WHERE id = :id
            """), {"id": item_id})


def count_by_status() -> dict[str, int]:
    """Return a summary of transcription_status counts for YouTube items."""
    with engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT COALESCE(transcription_status, 'NULL') AS st, COUNT(*) AS n
            FROM item i
            JOIN source s ON s.id = i.source_id
            WHERE s.code = 'youtube' AND i.has_transcript = false
            GROUP BY st ORDER BY st
        """)).fetchall()
    return {r.st: r.n for r in rows}


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------

def _ytdlp_cmd() -> list[str]:
    """Build the yt-dlp base command (same cookies/JS-runtime as the scraper).

    Goes direct (no SOCKS5 pool): audio download hits yt-dlp's innertube API,
    which works fine from the residential IP, and keeping it standalone means
    transcription never depends on proxy tunnels being up.
    """
    import sys
    cmd = (["yt-dlp"] if shutil.which("yt-dlp")
           else [sys.executable, "-m", "yt_dlp"])
    cb = settings().yt_dlp_cookies_from_browser
    if cb:
        cmd += ["--cookies-from-browser", cb]
    deno = _find_deno()
    if deno:
        cmd += ["--js-runtimes", "deno:" + deno]
    cmd += ["--retries", "8", "--fragment-retries", "8", "--socket-timeout", "30",
            "--user-agent", settings().scrape_user_agent]
    return cmd


def download_audio(video_id: str, dest_dir: Path) -> Path | None:
    """Download the audio track for a YouTube video as m4a.

    Returns the path to the downloaded file, or None if the download failed.
    Audio goes to ``dest_dir/{video_id}.m4a``.
    """
    if not _VIDEO_ID_RE.match(video_id):
        log.warning("rejecting malformed video id %r", video_id)
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(dest_dir / (video_id + ".%(ext)s"))
    url = "https://www.youtube.com/watch?v=" + video_id
    cmd = _ytdlp_cmd() + [
        "-f", "bestaudio[ext=m4a]/bestaudio/best",
        "-x", "--audio-format", "m4a",
        "--no-playlist",
        "--no-warnings",
        "-o", out_tmpl,
        url,
    ]
    cp = subprocess.run(cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        log.warning("yt-dlp audio download failed for %s: %s",
                    video_id, cp.stderr[-300:] if cp.stderr else "(no stderr)")
        return None
    # Find the downloaded file (extension may vary if m4a wasn't available).
    matches = [p for p in dest_dir.glob(video_id + ".*")
               if p.suffix.lower() in _AUDIO_EXTS]
    if not matches:
        log.warning("no audio file found for %s after download", video_id)
        return None
    return matches[0]


# ---------------------------------------------------------------------------
# Whisper transcription
# ---------------------------------------------------------------------------

def _ensure_cuda_dlls() -> None:
    """Register NVIDIA CUDA DLL directories so CTranslate2 can find cuBLAS.

    On Windows, the nvidia-cublas-cu12 and nvidia-cuda-nvrtc-cu12 packages
    install DLLs under site-packages/nvidia/<lib>/bin/, which Python doesn't
    automatically search. We use both ``os.add_dll_directory()`` (for Python's
    ctypes/ffi loader) and prepend to ``PATH`` (for native DLLs loaded by
    ``ctranslate2.dll`` via ``LoadLibrary``).
    """
    import os
    import sys

    if sys.platform != "win32":
        return
    # Look for nvidia/*/bin directories under the venv's site-packages.
    sp = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not sp.exists():
        return
    paths_to_add: list[str] = []
    for sub in sp.iterdir():
        bindir = sub / "bin"
        if bindir.is_dir():
            os.add_dll_directory(str(bindir))
            paths_to_add.append(str(bindir))
    # Prepend to PATH so native LoadLibrary calls also find the DLLs.
    if paths_to_add:
        existing = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(paths_to_add) + os.pathsep + existing


def load_model(
    model: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
):
    """Load the faster-whisper model once. Returns the model instance.

    Args override the ``WHISPER_*`` settings for this run.
    """
    _ensure_cuda_dlls()
    from faster_whisper import WhisperModel
    s = settings()
    model_name = model or s.whisper_model
    device_name = device or s.whisper_device
    compute = compute_type or s.whisper_compute_type
    print("Loading Whisper model '" + model_name + "' on device='" + device_name
          + "' compute_type='" + compute + "'...")
    t0 = time.time()
    m = WhisperModel(model_name, device=device_name, compute_type=compute)
    print("Model loaded in {:.1f}s".format(time.time() - t0))
    return m


def transcribe_audio(model, audio_path: Path) -> tuple[str, str]:
    """Transcribe an audio file. Returns (transcript_text, detected_language).

    Language is auto-detected by Whisper when ``settings().whisper_language``
    is empty. Cantonese → 'yue', English → 'en', etc.
    """
    s = settings()
    language = s.whisper_language or None  # None = auto-detect
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=s.whisper_beam_size,
        language=language,
    )
    # faster-whisper segments are lazy generators — consume them now.
    lines = []
    for seg in segments:
        t = seg.text.strip()
        if t:
            lines.append(t)
    # Whisper segments are punctuated sentences — assemble them into
    # capped paragraphs (same canonical markdown shape as YouTube VTT
    # transcripts) instead of a single \n-joined wall of text.
    from .scrapers.youtube import _assemble_paragraphs
    return _assemble_paragraphs([" ".join(lines)]), info.language


# ---------------------------------------------------------------------------
# Markdown update
# ---------------------------------------------------------------------------

def update_md_file(md_path: Path, transcript: str, language: str) -> bool:
    """Replace the NO_TRANSCRIPT_MARKER in the .md file with the transcript.

    Also updates front-matter: has_transcript → true, adds transcription_language.
    Returns True if the file was updated.
    """
    if not md_path or not md_path.exists():
        return False
    doc = load_md(md_path)
    body = doc.body

    if NO_TRANSCRIPT_MARKER in body:
        body = body.replace(NO_TRANSCRIPT_MARKER, transcript.strip())
    else:
        # If the marker isn't found (unexpected), append the transcript
        # after the existing "## Transcript" heading.
        if "## Transcript" in body:
            body = body.replace(
                "## Transcript\n",
                "## Transcript\n\n" + transcript.strip() + "\n",
                1,
            )
        else:
            body += "\n\n## Transcript\n\n" + transcript.strip() + "\n"

    doc.body = body
    doc.front["has_transcript"] = True
    doc.front["transcription_language"] = language
    doc.front["transcribed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    md_path.write_text(doc.dump(), encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def transcribe_all(
    candidates: list[dict],
    model,
    tmp_dir: Path,
    dry_run: bool = False,
) -> tuple[int, int, dict[str, int]]:
    """Process each candidate sequentially. Returns (done, failed, lang_counts)."""
    done = 0
    failed = 0
    lang_counts: dict[str, int] = {}
    total = len(candidates)
    s = settings()

    for i, row in enumerate(candidates, 1):
        vid = row["external_id"]
        title = (row["title"] or "")[:60]
        status = row.get("transcription_status") or "pending"
        duration = row.get("duration_sec")
        print("\n[{}/{}] {} | {}".format(i, total, vid, title))
        print("  channel: {} | status: {} | duration: {}s".format(
            row.get("channel_name", "?"), status, duration or "?"))

        # Skip videos that are too long (0 = no limit).
        if duration and s.whisper_max_duration_sec and duration > s.whisper_max_duration_sec:
            print("  ✗ skipped (duration {}s > limit {}s)".format(
                duration, s.whisper_max_duration_sec))
            failed += 1
            update_status(row["id"], "failed",
                          error="duration {}s exceeds limit".format(duration))
            continue

        if dry_run:
            print("  (dry run — skipping download + transcription)")
            continue

        md_path = Path(row["md_path"]) if row.get("md_path") else None

        # --- Step 1: Download audio ---
        print("  downloading audio...", end=" ", flush=True)
        t0 = time.time()
        audio_path = download_audio(vid, tmp_dir)
        if audio_path is None:
            print("✗ failed ({:.1f}s)".format(time.time() - t0))
            failed += 1
            update_status(row["id"], "failed", error="audio download failed")
            continue
        size_mb = audio_path.stat().st_size / (1024 * 1024)
        print("✓ {} ({:.1f} MB, {:.1f}s)".format(
            audio_path.name, size_mb, time.time() - t0))

        update_status(row["id"], "audio_downloaded")

        # --- Step 2: Transcribe ---
        print("  transcribing...", end=" ", flush=True)
        t0 = time.time()
        try:
            transcript, lang = transcribe_audio(model, audio_path)
        except Exception as exc:  # noqa: BLE001
            print("✗ error: {}".format(exc))
            failed += 1
            update_status(row["id"], "failed", error=str(exc))
            # Clean up audio regardless.
            _safe_delete(audio_path)
            continue
        elapsed = time.time() - t0
        word_count = len(transcript.split())
        print("✓ lang={} | {} words | {:.1f}s".format(lang, word_count, elapsed))

        if not transcript.strip():
            print("  ✗ empty transcript")
            failed += 1
            update_status(row["id"], "failed", error="empty transcript")
            _safe_delete(audio_path)
            continue

        # Print a preview.
        preview = transcript[:200].replace("\n", " ")
        print("  preview: {}...".format(preview))

        # --- Step 3: Update markdown + DB ---
        print("  updating .md + DB...", end=" ", flush=True)
        if md_path and update_md_file(md_path, transcript, lang):
            ingest_file(md_path)
            update_status(row["id"], "done", language=lang)
            print("✓")
            done += 1
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        else:
            print("✗ md file not found: {}".format(md_path))
            failed += 1
            update_status(row["id"], "failed",
                          error="md file not found: {}".format(md_path))

        # --- Step 4: Delete audio ---
        _safe_delete(audio_path)

    return done, failed, lang_counts


def _safe_delete(path: Path) -> None:
    """Delete a file, ignoring errors."""
    try:
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def run_transcribe(
    limit: int = 0,
    channel: str | None = None,
    retry_failed: bool = False,
    dry_run: bool = False,
    model: str | None = None,
    device: str | None = None,
    external_ids: list[str] | None = None,
) -> tuple[int, int, int]:
    """Run the transcription pipeline over pending candidates.

    Returns ``(total, done, failed)``. Used by ``kb youtube transcribe`` and
    by ``kb youtube scrape --transcribe`` (which passes ``external_ids`` to
    restrict the pass to the videos it just fetched).
    """
    candidates = gather_candidates(limit, channel, retry_failed,
                                   external_ids=external_ids)
    total = len(candidates)
    if total == 0:
        counts = count_by_status()
        if counts:
            print("No items pending transcription. Status summary:")
            for st, n in counts.items():
                print("  {}: {}".format(st, n))
        else:
            print("No items pending transcription.")
        return 0, 0, 0

    print("Transcribing {} YouTube video(s) with faster-whisper...".format(total))
    print("(one at a time — GPU runs sequentially)")
    if dry_run:
        print("(DRY RUN — no downloads/transcription)")
        for row in candidates:
            print("  {} | {} | {}s | {}".format(
                row["external_id"], row.get("channel_name", "?"),
                row.get("duration_sec") or "?", (row["title"] or "")[:60]))
        return total, 0, 0

    tmp_dir = audio_tmp_dir()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    print("Audio temp dir: {}".format(tmp_dir))

    # Load the model once.
    whisper_model = load_model(model=model, device=device)

    # Run the pipeline.
    done, failed, lang_counts = transcribe_all(candidates, whisper_model,
                                               tmp_dir, dry_run)

    print("\n" + "=" * 60)
    print("Done: {} transcribed, {} failed (of {} total)".format(done, failed, total))
    if lang_counts:
        print("Detected languages: {}".format(lang_counts))
    # Clean up temp dir if empty.
    try:
        tmp_dir.rmdir()
    except OSError:
        pass  # not empty or doesn't exist
    return total, done, failed
