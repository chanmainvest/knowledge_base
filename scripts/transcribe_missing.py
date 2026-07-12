#!/usr/bin/env python3
"""Transcribe YouTube videos missing subtitles using faster-whisper (large-v3).

For YouTube items where no subtitle/transcript could be fetched
(``has_transcript=false``), this script:

1. Downloads the audio track (m4a) via yt-dlp to a gitignored ``tmp/audio/``
   folder.
2. Runs faster-whisper on GPU (one video at a time — no parallel GPU load).
3. Writes the generated transcript into the existing ``.md`` file (replacing the
   ``_(no transcript available)_`` placeholder) and re-ingests to update the DB.
4. Deletes the audio file immediately after each item.
5. Tracks the full lifecycle in the ``item.transcription_status`` column:
   ``pending → audio_downloaded → transcribing → done`` (or ``failed``).

Usage::

    # Transcribe all pending (one at a time)
    uv run python scripts/transcribe_missing.py

    # Test with one Cantonese video from latp channel
    uv run python scripts/transcribe_missing.py --channel latp --limit 1

    # Test with one English video from cpm-group channel
    uv run python scripts/transcribe_missing.py --channel cpm-group --limit 1

    # List candidates without transcribing
    uv run python scripts/transcribe_missing.py --list

    # Reset items stuck in 'transcribing' (e.g. after a crash) back to 'pending'
    uv run python scripts/transcribe_missing.py --reset-stuck

    # Re-attempt items that previously failed
    uv run python scripts/transcribe_missing.py --retry-failed
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kb.config import settings  # noqa: E402
from kb.db import engine  # noqa: E402
from kb.ingest import ingest_file  # noqa: E402
from kb.io_md import load_md  # noqa: E402
from kb.scrapers.youtube import NO_TRANSCRIPT_MARKER  # noqa: E402
from sqlalchemy import text  # noqa: E402


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def gather_candidates(
    limit: int,
    channel: str | None,
    retry_failed: bool,
) -> list[dict]:
    """Return YouTube item rows queued for transcription.

    By default selects items with ``transcription_status = 'pending'`` (or
    NULL). With ``--retry-failed``, also includes ``'failed'`` items.
    """
    statuses = ["pending"] if not retry_failed else ["pending", "failed"]
    sql = """
        SELECT i.id, i.external_id, i.title, i.md_path, i.duration_sec,
               i.transcription_status,
               ch.handle AS channel_handle, ch.name AS channel_name
        FROM item i
        JOIN source s ON s.id = i.source_id
        LEFT JOIN channel ch ON ch.id = i.channel_id
        WHERE s.code = 'youtube'
          AND i.has_transcript = false
          AND (i.transcription_status = ANY(:statuses)
               OR i.transcription_status IS NULL)
    """
    params: dict = {"statuses": statuses}
    if channel:
        sql += " AND (ch.handle ILIKE :ch OR ch.name ILIKE :ch)"
        params["ch"] = f"%{channel}%"
    sql += " ORDER BY i.id"
    if limit:
        sql += f" LIMIT {limit}"
    with engine().connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def reset_stuck() -> int:
    """Reset items stuck in 'transcribing' back to 'pending'. Returns count."""
    with engine().begin() as conn:
        result = conn.execute(text("""
            UPDATE item SET transcription_status = 'pending'
            WHERE transcription_status = 'transcribing'
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
    """Update the transcription_status (and related fields) for an item."""
    sets = ["transcription_status = :st"]
    params: dict = {"st": status, "id": item_id}
    if error is not None:
        sets.append("transcription_error = :err")
        params["err"] = error[:1000]
    if language is not None:
        sets.append("transcription_language = :lang")
        params["lang"] = language
    if status == "done":
        sets.append("transcribed_at = now()")
        sets.append("has_transcript = true")
    with engine().begin() as conn:
        conn.execute(text(f"UPDATE item SET {', '.join(set_ for set_ in sets)} "
                          f"WHERE id = :id"), params)


def count_by_status() -> dict[str, int]:
    """Return a summary of transcription_status counts for YouTube items."""
    sql = """
        SELECT COALESCE(transcription_status, 'NULL') AS st, COUNT(*) AS n
        FROM item i
        JOIN source s ON s.id = i.source_id
        WHERE s.code = 'youtube' AND i.has_transcript = false
        GROUP BY st ORDER BY st
    """
    with engine().connect() as conn:
        return {r.st: r.n for r in conn.execute(text(sql))}


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------

def _ytdlp_cmd() -> list[str]:
    """Build the yt-dlp base command (same cookies as the scraper)."""
    import sys as _sys
    cmd = (["yt-dlp"] if shutil.which("yt-dlp")
           else [_sys.executable, "-m", "yt_dlp"])
    cb = settings().yt_dlp_cookies_from_browser
    if cb:
        cmd += ["--cookies-from-browser", cb]
    # Deno JS runtime — same logic as the scraper.
    from kb.scrapers.youtube import _find_deno
    deno = _find_deno()
    if deno:
        cmd += ["--js-runtimes", f"deno:{deno}"]
    cmd += ["--retries", "8", "--fragment-retries", "8", "--socket-timeout", "30",
            "--user-agent", settings().scrape_user_agent]
    return cmd


def download_audio(video_id: str, dest_dir: Path) -> Path | None:
    """Download the audio track for a YouTube video as m4a.

    Returns the path to the downloaded file, or None if the download failed.
    Audio goes to ``dest_dir/{video_id}.m4a``.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(dest_dir / f"{video_id}.%(ext)s")
    url = f"https://www.youtube.com/watch?v={video_id}"
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
        print(f"    yt-dlp error: {cp.stderr[-300:] if cp.stderr else '(no stderr)'}")
        return None
    # Find the downloaded file (extension may vary if m4a wasn't available).
    matches = list(dest_dir.glob(f"{video_id}.*"))
    # Filter out non-audio files (info.json, etc.)
    audio_exts = {".m4a", ".webm", ".mp3", ".opus", ".wav", ".mp4"}
    matches = [p for p in matches if p.suffix.lower() in audio_exts]
    if not matches:
        print("    no audio file found after download")
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


def load_model():
    """Load the faster-whisper model once. Returns the model instance."""
    _ensure_cuda_dlls()
    from faster_whisper import WhisperModel
    s = settings()
    print(f"Loading Whisper model '{s.whisper_model}' on device='{s.whisper_device}' "
          f"compute_type='{s.whisper_compute_type}'...")
    t0 = time.time()
    model = WhisperModel(
        s.whisper_model,
        device=s.whisper_device,
        compute_type=s.whisper_compute_type,
    )
    print(f"Model loaded in {time.time() - t0:.1f}s")
    return model


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
        text = seg.text.strip()
        if text:
            lines.append(text)
    transcript = "\n".join(lines)
    return transcript, info.language


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
                f"## Transcript\n\n{transcript.strip()}\n",
                1,
            )
        else:
            body += f"\n\n## Transcript\n\n{transcript.strip()}\n"

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
    dry_run: bool,
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
        print(f"\n[{i}/{total}] {vid} | {title}")
        print(f"  channel: {row.get('channel_name', '?')} | "
              f"status: {status} | duration: {duration or '?'}s")

        # Skip videos that are too long (0 = no limit).
        if duration and s.whisper_max_duration_sec and duration > s.whisper_max_duration_sec:
            print(f"  ✗ skipped (duration {duration}s > limit {s.whisper_max_duration_sec}s)")
            failed += 1
            update_status(row["id"], "failed",
                          error=f"duration {duration}s exceeds limit")
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
            print(f"✗ failed ({time.time() - t0:.1f}s)")
            failed += 1
            update_status(row["id"], "failed", error="audio download failed")
            continue
        size_mb = audio_path.stat().st_size / (1024 * 1024)
        print(f"✓ {audio_path.name} ({size_mb:.1f} MB, {time.time() - t0:.1f}s)")

        update_status(row["id"], "audio_downloaded")

        # --- Step 2: Transcribe ---
        print("  transcribing...", end=" ", flush=True)
        t0 = time.time()
        try:
            transcript, lang = transcribe_audio(model, audio_path)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ error: {exc}")
            failed += 1
            update_status(row["id"], "failed", error=str(exc))
            # Clean up audio regardless.
            _safe_delete(audio_path)
            continue
        elapsed = time.time() - t0
        word_count = len(transcript.split())
        print(f"✓ lang={lang} | {word_count} words | {elapsed:.1f}s")

        if not transcript.strip():
            print("  ✗ empty transcript")
            failed += 1
            update_status(row["id"], "failed", error="empty transcript")
            _safe_delete(audio_path)
            continue

        # Print a preview.
        preview = transcript[:200].replace("\n", " ")
        print(f"  preview: {preview}...")

        # --- Step 3: Update markdown + DB ---
        print("  updating .md + DB...", end=" ", flush=True)
        if md_path and update_md_file(md_path, transcript, lang):
            ingest_file(md_path)
            update_status(row["id"], "done", language=lang)
            print("✓")
            done += 1
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        else:
            print(f"✗ md file not found: {md_path}")
            failed += 1
            update_status(row["id"], "failed", error=f"md file not found: {md_path}")

        # --- Step 4: Delete audio ---
        _safe_delete(audio_path)

    return done, failed, lang_counts


def _safe_delete(path: Path) -> None:
    """Delete a file, ignoring errors."""
    try:
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--limit", type=int, default=0,
                    help="Max items to transcribe (0=all)")
    ap.add_argument("--channel", default=None,
                    help="Only transcribe this channel handle/name")
    ap.add_argument("--dry-run", action="store_true",
                    help="List candidates but don't download/transcribe")
    ap.add_argument("--list", action="store_true",
                    help="List candidates and exit")
    ap.add_argument("--retry-failed", action="store_true",
                    help="Also re-attempt items previously marked 'failed'")
    ap.add_argument("--reset-stuck", action="store_true",
                    help="Reset items stuck in 'transcribing' back to 'pending' and exit")
    ap.add_argument("--model", default=None,
                    help="Override whisper model (default: from config)")
    ap.add_argument("--device", default=None,
                    help="Override device (default: from config)")
    args = ap.parse_args()

    # --reset-stuck: clean up and exit.
    if args.reset_stuck:
        n = reset_stuck()
        print(f"Reset {n} items from 'transcribing' → 'pending'.")
        return

    # --list: show candidates and exit.
    if args.list:
        candidates = gather_candidates(args.limit, args.channel, args.retry_failed)
        print(f"\n{len(candidates)} transcription candidate(s):\n")
        for row in candidates:
            vid = row["external_id"]
            title = (row["title"] or "")[:60]
            ch = row.get("channel_name", "?")
            st = row.get("transcription_status") or "pending"
            dur = row.get("duration_sec") or "?"
            print(f"  {vid} | {ch} | {st} | {dur}s | {title}")
        # Also show status summary.
        counts = count_by_status()
        print(f"\nStatus summary (has_transcript=false YouTube items):")
        for st, n in counts.items():
            print(f"  {st}: {n}")
        return

    # Gather candidates.
    candidates = gather_candidates(args.limit, args.channel, args.retry_failed)
    total = len(candidates)
    if total == 0:
        print("No items pending transcription.")
        counts = count_by_status()
        if counts:
            print("\nStatus summary:")
            for st, n in counts.items():
                print(f"  {st}: {n}")
        return

    print(f"Transcribing {total} YouTube video(s) with faster-whisper...")
    print(f"(one at a time — GPU runs sequentially)")
    if args.dry_run:
        print("(DRY RUN — no downloads/transcription)")

    # Resolve temp dir.
    s = settings()
    tmp_dir = Path(s.whisper_tmp_dir)
    if not tmp_dir.is_absolute():
        tmp_dir = ROOT / tmp_dir
    tmp_dir.mkdir(parents=True, exist_ok=True)
    print(f"Audio temp dir: {tmp_dir}")

    if args.dry_run:
        # Just list candidates.
        for row in candidates:
            vid = row["external_id"]
            title = (row["title"] or "")[:60]
            ch = row.get("channel_name", "?")
            dur = row.get("duration_sec") or "?"
            print(f"  {vid} | {ch} | {dur}s | {title}")
        return

    # Load the model once.
    model = load_model()

    # Run the pipeline.
    done, failed, lang_counts = transcribe_all(
        candidates, model, tmp_dir, args.dry_run,
    )

    # Summary.
    print(f"\n{'='*60}")
    print(f"Done: {done} transcribed, {failed} failed (of {total} total)")
    if lang_counts:
        print(f"Detected languages: {lang_counts}")
    # Clean up temp dir if empty.
    try:
        tmp_dir.rmdir()
    except OSError:
        pass  # not empty or doesn't exist


if __name__ == "__main__":
    main()
