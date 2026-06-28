"""Tests for YouTube scraper helpers."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kb.scrapers.base import ScrapedItem
from kb.scrapers.youtube import (
    YouTubeScraper,
    _parse_channel_display_name,
    channel_dir_slug,
    migrate_youtube_folders,
    normalize_youtube_handle,
    plan_youtube_folder_renames,
)


def test_undated_youtube_cache_without_published_at_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scraper = YouTubeScraper()
    monkeypatch.setattr("kb.scrapers.youtube.DATA_DIR", tmp_path)

    cached = tmp_path / "youtube" / "latp" / "undated" / "undated-sample.md"
    cached.parent.mkdir(parents=True)
    cached.write_text(
        "---\n"
        "source: youtube\n"
        "external_id: BMi3nQSfKS4\n"
        "published_at: null\n"
        "---\n\n"
        + ("x" * 220),
        encoding="utf-8",
    )

    assert not scraper.already_scraped({
        "external_id": "BMi3nQSfKS4",
        "channel_handle": "@LATP",
        "channel_name": "LATP",
        "title": "Sample",
    })


def test_youtube_cache_with_published_at_is_complete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scraper = YouTubeScraper()
    monkeypatch.setattr("kb.scrapers.youtube.DATA_DIR", tmp_path)

    cached = tmp_path / "youtube" / "latp" / "2024" / "2024-05-07-sample.md"
    cached.parent.mkdir(parents=True)
    cached.write_text(
        "---\n"
        "source: youtube\n"
        "external_id: BMi3nQSfKS4\n"
        "published_at: '2024-05-07T00:00:00+00:00'\n"
        "---\n\n"
        + ("x" * 220),
        encoding="utf-8",
    )

    assert scraper.already_scraped({
        "external_id": "BMi3nQSfKS4",
        "channel_handle": "@LATP",
        "channel_name": "LATP",
        "title": "Sample",
        "upload_date": "20240507",
    })


@pytest.mark.asyncio
async def test_limit_is_not_total_file_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = YouTubeScraper()

    async def fake_discover(limit: int | None = None):
        assert limit == 3
        for index in range(6):
            yield {"external_id": str(index), "url": f"https://youtu.be/{index}"}

    async def fake_fetch(descriptor: dict) -> object:
        return object()

    monkeypatch.setattr(scraper, "discover", fake_discover)
    monkeypatch.setattr(scraper, "already_scraped", lambda descriptor: False)
    monkeypatch.setattr(scraper, "fetch", fake_fetch)

    written: list[object] = []

    def fake_write_md(item: object) -> Path:
        written.append(item)
        return Path(f"video-{len(written)}.md")

    monkeypatch.setattr(scraper, "write_md", fake_write_md)
    paths = await scraper.run(limit=3)

    assert len(paths) == 6
    assert len(written) == 6


def test_parse_channel_display_name_prefers_uploader() -> None:
    blob = '{"uploader": "Jimmy Connor ", "channel": "Jimmy Connor "}'
    assert _parse_channel_display_name(blob) == "Jimmy Connor"


def test_normalize_youtube_handle_adds_at_prefix() -> None:
    assert normalize_youtube_handle("BloorStreetCapital") == "@BloorStreetCapital"
    assert normalize_youtube_handle("@BloorStreetCapital") == "@BloorStreetCapital"
    assert normalize_youtube_handle(
        "https://www.youtube.com/@BloorStreetCapital"
    ) == "https://www.youtube.com/@BloorStreetCapital"


def test_parse_channel_display_name_returns_none_on_empty() -> None:
    assert _parse_channel_display_name("") is None


@pytest.mark.asyncio
async def test_discover_uses_resolved_channel_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = YouTubeScraper()
    monkeypatch.setattr(
        "kb.scrapers.youtube._load_channels",
        lambda: [("@foo", "Old Name")],
    )
    monkeypatch.setattr(
        scraper,
        "resolve_channel_display_name",
        lambda handle: "Resolved Name",
    )
    updated: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "kb.scrapers.youtube._update_channel_name",
        lambda handle, name: updated.append((handle, name)),
    )

    def fake_ytdlp(*args: str, **kwargs: object) -> object:
        import subprocess
        return subprocess.CompletedProcess(
            args, 0, '{"id":"abc","title":"Vid"}\n', "",
        )

    monkeypatch.setattr(scraper, "_ytdlp", fake_ytdlp)
    monkeypatch.setattr(scraper.limiter, "wait", lambda host: asyncio.sleep(0))

    items = [item async for item in scraper.discover()]
    assert items[0]["channel_name"] == "Resolved Name"
    assert updated == [("@foo", "Resolved Name")]


def test_scraped_item_uses_channel_name_for_directory() -> None:
    item = ScrapedItem(
        source="youtube",
        channel="@RealVisionFinance",
        channel_name="Real Vision Finance",
        channel_dir="Real Vision Finance",
        external_id="abc123",
        title="Sample",
        url="https://youtu.be/abc123",
        published_at=None,
        body_md="body",
        flat_layout=True,
        folder_name="undated-sample",
    )
    assert item.content_path().parts[-3:-2] == ("real-vision-finance",)


def test_already_scraped_finds_legacy_handle_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scraper = YouTubeScraper()
    monkeypatch.setattr("kb.scrapers.youtube.DATA_DIR", tmp_path)

    legacy = tmp_path / "youtube" / "realvisionfinance" / "2024" / "2024-01-01-sample.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "---\n"
        "source: youtube\n"
        "external_id: abc123\n"
        "published_at: '2024-01-01T00:00:00+00:00'\n"
        "---\n\n"
        + ("x" * 220),
        encoding="utf-8",
    )

    assert scraper.already_scraped({
        "external_id": "abc123",
        "channel_handle": "@RealVisionFinance",
        "channel_name": "Real Vision Finance",
        "title": "Sample",
        "upload_date": "20240101",
    })


def test_migrate_youtube_folders_renames_handle_slug_to_display_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("kb.scrapers.youtube.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "kb.scrapers.youtube._load_channels",
        lambda: [("@RealVisionFinance", "Real Vision Finance")],
    )

    old = tmp_path / "youtube" / channel_dir_slug("@RealVisionFinance") / "2024"
    old.mkdir(parents=True)
    (old / "2024-01-01-sample.md").write_text("# sample\n", encoding="utf-8")

    moves = migrate_youtube_folders()
    assert len(moves) == 1
    new = tmp_path / "youtube" / channel_dir_slug("Real Vision Finance")
    assert new.is_dir()
    assert not (tmp_path / "youtube" / channel_dir_slug("@RealVisionFinance")).exists()
    assert (new / "2024" / "2024-01-01-sample.md").exists()


def test_plan_youtube_folder_renames_uses_front_matter_for_orphans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("kb.scrapers.youtube.DATA_DIR", tmp_path)
    monkeypatch.setattr("kb.scrapers.youtube._load_channels", lambda: [])

    folder = tmp_path / "youtube" / "pboyle" / "2019"
    folder.mkdir(parents=True)
    (folder / "2019-01-01-sample.md").write_text(
        "---\nchannel_name: Patrick Boyle\n---\n\nbody\n",
        encoding="utf-8",
    )

    renames = plan_youtube_folder_renames()
    assert renames == [
        (tmp_path / "youtube" / "pboyle", tmp_path / "youtube" / "patrick-boyle"),
    ]