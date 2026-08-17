"""Tests for the YouTube metadata backfill (`kb youtube backfill-metadata`).

The repair targets files written by fetch()'s stub-info fallback (dead
tunnel / 429 during bulk scrapes): empty `## Description` section, null
duration/uploader front-matter, `- Published: unknown` / `- Duration: None
sec` body lines. `_apply_metadata_to_md` is the pure rewrite; these tests
pin its behaviour without touching the network or the DB.
"""
from __future__ import annotations

import re

import yaml

from kb.io_md import MdDoc
from kb.scrapers.youtube import (
    _EMPTY_DESC_RE,
    _apply_metadata_to_md,
    _err_class,
    _extract_json_object,
    _watch_page_info,
)

SCARRED_MD = """---
source: youtube
channel: '@vricmedia'
channel_name: VRIC Media
external_id: abc12345678
url: https://www.youtube.com/watch?v=abc12345678
title: 'Patrick Karim: Gold is Bottoming'
published_at: 2026-08-08 00:00:00+00:00
language: en
duration_sec: null
scraped_at: '2026-08-11T07:14:20.068603Z'
extra:
  uploader: null
  uploader_id: null
  view_count: null
  tags: null
  categories: null
has_transcript: false
---

# Patrick Karim: Gold is Bottoming

- Channel: VRIC Media (@vricmedia)
- URL: https://www.youtube.com/watch?v=abc12345678
- Published: unknown
- Duration: None sec

## Description



## Transcript

_(no transcript available)_
"""

HEALTHY_INFO = {
    "id": "abc12345678",
    "description": "Patrick Karim on gold charts.\nChapters:\n0:00 intro",
    "duration": 3725,
    "language": "en",
    "uploader": "VRIC Media",
    "uploader_id": "@vricmedia",
    "view_count": 4210,
    "tags": ["gold", "silver"],
    "categories": ["News & Politics"],
    "upload_date": "20260808",
}


def _load(text: str) -> MdDoc:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert m
    return MdDoc(front=yaml.safe_load(m.group(1)), body=m.group(2))


def test_empty_desc_regex_matches_scarred_section_only():
    assert _EMPTY_DESC_RE.search(SCARRED_MD)
    filled = SCARRED_MD.replace(
        "## Description\n\n\n\n## Transcript",
        "## Description\n\nA real description.\n\n## Transcript")
    assert not _EMPTY_DESC_RE.search(filled)


def test_apply_fills_description_and_body_lines():
    doc = _load(SCARRED_MD)
    assert _apply_metadata_to_md(doc, HEALTHY_INFO) is True
    # description written verbatim (same as fetch()), canonical section spacing
    assert "## Description\n\nPatrick Karim on gold charts.\nChapters:\n0:00 intro\n\n## Transcript" in doc.body
    assert "- Duration: 3725 sec" in doc.body
    assert "- Published: 2026-08-08" in doc.body
    # transcript section is untouched
    assert "_(no transcript available)_" in doc.body
    # front-matter refreshed + resumability marker stamped
    assert doc.front["duration_sec"] == 3725
    assert doc.front["extra"]["uploader"] == "VRIC Media"
    assert doc.front["extra"]["view_count"] == 4210
    assert doc.front["extra"]["tags"] == ["gold", "silver"]
    assert "metadata_synced_at" in doc.front


def test_apply_published_from_frontmatter_without_upload_date():
    doc = _load(SCARRED_MD)
    info = dict(HEALTHY_INFO, upload_date=None, description="Teaser text.")
    assert _apply_metadata_to_md(doc, info) is True
    # upload_date missing → fall back to the front-matter published_at
    assert "- Published: 2026-08-08" in doc.body


def test_apply_idempotent_and_no_change_returns_false():
    doc = _load(SCARRED_MD)
    assert _apply_metadata_to_md(doc, HEALTHY_INFO) is True
    body1 = doc.body
    front1 = {k: v for k, v in doc.front.items() if k != "metadata_synced_at"}
    assert _apply_metadata_to_md(doc, HEALTHY_INFO) is False
    assert doc.body == body1
    front2 = {k: v for k, v in doc.front.items() if k != "metadata_synced_at"}
    assert front2 == front1


def test_apply_genuinely_empty_description_stamps_marker():
    # A video with no description at all: the section stays empty, but the
    # other scars (duration line) are still repaired and the marker is
    # stamped so re-runs skip the video.
    doc = _load(SCARRED_MD)
    info = {"id": "abc12345678", "description": "", "duration": 3725,
            "uploader": None, "view_count": None, "tags": None,
            "categories": None, "upload_date": "20260808"}
    assert _apply_metadata_to_md(doc, info) is True
    assert _EMPTY_DESC_RE.search(doc.body)
    assert "- Duration: 3725 sec" in doc.body
    assert "metadata_synced_at" in doc.front


def test_err_class_blocked_signatures():
    assert _err_class("ERROR: [youtube] abc: HTTP Error 429: Too Many Requests") == "blocked"
    assert _err_class("Please sign in to confirm you're not a bot") == "blocked"
    assert _err_class("Sign in to protect our community") == "blocked"
    assert _err_class("Your IP has been blocked") == "blocked"


def test_err_class_gone_and_unknown():
    # yt-dlp's private-video error mentions "Sign in" — the full
    # "sign in to confirm" phrase must NOT match it.
    assert _err_class(
        "ERROR: [youtube] abc: Private video. "
        "Sign in if you've been granted access to this video") == "gone"
    assert _err_class("ERROR: [youtube] abc: Video unavailable") == "gone"
    assert _err_class("ERROR: [youtube] abc: This video has been removed") == "gone"
    assert _err_class("") is None
    assert _err_class("ERROR: connection reset by peer") is None


def test_extract_json_object_brace_matching():
    html = (
        'var x = 1; ytInitialPlayerResponse = {"videoDetails": '
        '{"title": "T", "shortDescription": "brace } inside {\\" string", '
        '"lengthSeconds": "61"}, "microformat": {"playerMicroformatRenderer": '
        '{"uploadDate": "2025-08-02"}}}; var meta = {"other": true};'
    )
    blob = _extract_json_object(html, "ytInitialPlayerResponse")
    import json as _json
    parsed = _json.loads(blob)
    assert parsed["videoDetails"]["shortDescription"] == 'brace } inside {" string'
    assert parsed["microformat"]["playerMicroformatRenderer"]["uploadDate"] == "2025-08-02"
    assert _extract_json_object(html, "noSuchMarker") is None


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


def test_watch_page_info_parses_video_details(monkeypatch):
    import json as _json

    pr = {
        "videoDetails": {
            "title": "Markets Weekly",
            "shortDescription": "Private Credit Panic — chapters inside",
            "lengthSeconds": "3725",
            "viewCount": "4210",
            "author": "Joseph Wang",
            "keywords": ["fed", "markets"],
        },
        "microformat": {"playerMicroformatRenderer": {
            "uploadDate": "2026-03-14"}},
    }
    html = f"<html><body>ytInitialPlayerResponse = {_json.dumps(pr)};</body></html>"
    monkeypatch.setattr(
        "requests.get", lambda *a, **kw: _FakeResp(html))
    info, err = _watch_page_info("EDi5ZnwXEVk")
    assert err is None
    assert info["description"].startswith("Private Credit Panic")
    assert info["duration"] == 3725
    assert info["view_count"] == 4210
    assert info["uploader"] == "Joseph Wang"
    assert info["tags"] == ["fed", "markets"]
    assert info["upload_date"] == "20260314"


def test_watch_page_info_classifies_shell_and_gone(monkeypatch):
    # Bot-wall shell page: no embedded player response.
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **kw: _FakeResp("<html><head><title> - YouTube</title>"))
    assert _watch_page_info("EDi5ZnwXEVk") == (None, "blocked")
    # Player response without videoDetails = unavailable video.
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **kw: _FakeResp(
            'ytInitialPlayerResponse = {"playabilityStatus": {"status": "ERROR"}};'))
    assert _watch_page_info("EDi5ZnwXEVk") == (None, "gone")
    # HTTP 429.
    monkeypatch.setattr(
        "requests.get", lambda *a, **kw: _FakeResp("", status_code=429))
    assert _watch_page_info("EDi5ZnwXEVk") == (None, "blocked")
