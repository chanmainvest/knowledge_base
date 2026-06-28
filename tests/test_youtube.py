"""Tests for YouTube scraper helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from kb.scrapers.youtube import YouTubeScraper


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