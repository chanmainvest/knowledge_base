"""YouTube scraper using yt-dlp (no API key required).

Strategy: for each channel, list latest videos via yt-dlp's metadata-only
extraction, download auto/uploaded subtitles (vtt), convert to plain text
markdown. Falls back to youtube-transcript-api if yt-dlp can't fetch subs.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import text as sa_text

from ..config import DATA_DIR, settings
from ..io_md import slugify
from .base import BaseScraper, ScrapedItem


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


class YouTubeScraper(BaseScraper):
    code = "youtube"
    name = "YouTube"

    def __init__(self) -> None:
        super().__init__()
        if not shutil.which("yt-dlp"):
            self.log.warning("yt-dlp not on PATH; will try via 'python -m yt_dlp'")

    # ---- helpers ---------------------------------------------------------
    def _ytdlp(self, *args: str, **kw) -> subprocess.CompletedProcess:
        import sys
        cmd = (["yt-dlp"] if shutil.which("yt-dlp")
               else [sys.executable, "-m", "yt_dlp"]) + list(args)
        cb = settings().yt_dlp_cookies_from_browser
        if cb:
            cmd += ["--cookies-from-browser", cb]
        cmd += ["--user-agent", settings().scrape_user_agent]
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", **kw)

    async def run(self, limit: int | None = None) -> list[Path]:
        out: list[Path] = []
        async for d in self.discover(limit=limit):
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
        return out

    @staticmethod
    def _vtt_to_text(vtt: str) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for raw in vtt.splitlines():
            s = raw.strip()
            if not s or s.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
                continue
            if "-->" in s:
                continue
            if re.fullmatch(r"\d+", s):
                continue
            # strip inline timestamp tags
            s = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", s)
            s = re.sub(r"</?c[^>]*>", "", s)
            s = re.sub(r"<[^>]+>", "", s).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            lines.append(s)
        return "\n".join(lines)

    # ---- discover --------------------------------------------------------
    async def discover(self, limit: int | None = None) -> AsyncIterator[dict]:
        for handle, display in _load_channels():
            url = (handle if handle.startswith("http")
                   else f"https://www.youtube.com/{handle}/videos")
            self.log.info("discovering %s", url)
            args = ["--flat-playlist", "--dump-json", "--ignore-errors", url]
            # Only cap per-channel when caller explicitly passed a limit.
            if limit:
                args = ["--flat-playlist", "--dump-json",
                        "--playlist-end", str(limit),
                        "--ignore-errors", url]
            cp = self._ytdlp(*args)
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
                    "duration": j.get("duration"),
                    "upload_date": j.get("upload_date"),
                }
                await asyncio.sleep(0)

    def already_scraped(self, d: dict) -> bool:
        date = d.get("upload_date") or "undated"
        date_fmt = (f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else "undated")
        year = date[:4] if len(date) == 8 else "undated"
        title = d.get("title") or d["external_id"]
        stem = f"{date_fmt}-{slugify(title, 80)}"
        ch = slugify(d["channel_handle"])
        md_path = DATA_DIR / "youtube" / ch / year / f"{stem}.md"
        return md_path.exists() and md_path.stat().st_size > 200

    # ---- fetch -----------------------------------------------------------
    async def fetch(self, d: dict) -> ScrapedItem | None:
        await self.limiter.wait("https://www.youtube.com")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cp = self._ytdlp(
                "--skip-download",
                "--write-info-json",
                "--write-auto-subs", "--write-subs",
                "--sub-langs", "en.*,zh.*,en,zh",
                "--convert-subs", "vtt",
                "--ignore-errors", "--no-abort-on-error",
                "--no-warnings",
                "-o", str(tmp / "%(id)s.%(ext)s"),
                d["url"],
            )
            if cp.returncode != 0:
                self.log.warning("yt-dlp fetch err %s :: %s",
                                 d["external_id"], cp.stderr[-300:])
            info_path = tmp / f"{d['external_id']}.info.json"
            if not info_path.exists():
                # fallback: just dump metadata
                cp2 = self._ytdlp("--skip-download", "--dump-json",
                                  "--no-warnings", d["url"])
                if cp2.returncode == 0 and cp2.stdout.strip():
                    try:
                        info = json.loads(cp2.stdout.splitlines()[0])
                    except Exception:
                        info = {"id": d["external_id"], "title": d["title"]}
                else:
                    info = {"id": d["external_id"], "title": d["title"],
                            "upload_date": d.get("upload_date")}
            else:
                info = json.loads(info_path.read_text("utf-8"))

            # Find first vtt file
            vtt = next(iter(tmp.glob(f"{d['external_id']}*.vtt")), None)
            transcript_text = self._vtt_to_text(vtt.read_text("utf-8")) if vtt else ""

        if not transcript_text:
            # Last-ditch: youtube-transcript-api (supports both old + new API)
            try:
                from youtube_transcript_api import YouTubeTranscriptApi
                langs = ["en", "zh-Hant", "zh-Hans", "zh"]
                if hasattr(YouTubeTranscriptApi, "get_transcript"):
                    tx = YouTubeTranscriptApi.get_transcript(d["external_id"], languages=langs)
                    transcript_text = "\n".join(seg["text"] for seg in tx)
                else:
                    api = YouTubeTranscriptApi()
                    fetched = api.fetch(d["external_id"], languages=langs)
                    transcript_text = "\n".join(s.text for s in fetched)
            except Exception as exc:  # noqa: BLE001
                self.log.info("no transcript for %s :: %s", d["external_id"], exc)

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
            f"## Transcript\n\n{transcript_text or '_(no transcript available)_'}\n"
        )

        return ScrapedItem(
            source="youtube",
            channel=d["channel_handle"],
            channel_name=d["channel_name"],
            external_id=d["external_id"],
            title=title,
            url=d["url"],
            published_at=published_at,
            duration_sec=info.get("duration"),
            language=info.get("language") or "en",
            body_md=body,
            folder_name=folder_name,
            flat_layout=True,
            extra={
                "uploader": info.get("uploader"),
                "uploader_id": info.get("uploader_id"),
                "view_count": info.get("view_count"),
                "tags": info.get("tags"),
                "categories": info.get("categories"),
            },
        )
