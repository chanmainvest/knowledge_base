"""Tests for Patreon scraper helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kb.scrapers.patreon import (
    PatreonScraper,
    _campaign_lookup_url,
    _html_to_md,
    _parse_dt,
    _posts_list_url,
    normalize_vanity,
)


def test_normalize_vanity() -> None:
    assert normalize_vanity("aminvest") == "aminvest"
    assert normalize_vanity("https://www.patreon.com/c/aminvest/posts?vanity=aminvest") == "aminvest"
    assert normalize_vanity("c/aminvest/posts") == "aminvest"


def test_scraper_uses_patreon_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATREON_RATE_LIMIT_SEC", "7")
    monkeypatch.setenv("SCRAPE_RATE_LIMIT_SEC", "3")
    from kb.config import settings as settings_fn

    settings_fn.cache_clear()
    sc = PatreonScraper()
    assert sc.limiter.min_interval >= 7.0
    settings_fn.cache_clear()


def test_posts_list_url_contains_campaign_filter() -> None:
    url = _posts_list_url("12345")
    assert "filter%5Bcampaign_id%5D=12345" in url or "filter[campaign_id]=12345" in url
    assert "/api/posts" in url


def test_campaign_lookup_url() -> None:
    url = _campaign_lookup_url("macroalf")
    assert "filter%5Bvanity%5D=macroalf" in url or "filter[vanity]=macroalf" in url


def test_parse_dt() -> None:
    dt = _parse_dt("2024-06-01T12:00:00.000Z")
    assert dt == datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_html_to_md() -> None:
    assert _html_to_md("<p>Hello <strong>world</strong></p>") == "Hello **world**"


@pytest.mark.asyncio
async def test_fetch_builds_markdown() -> None:
    sc = PatreonScraper()
    item = await sc.fetch({
        "external_id": "999",
        "url": "https://www.patreon.com/posts/example-999",
        "title": "Example Post",
        "published_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
        "channel_handle": "macroalf",
        "channel_name": "Macro Alf",
        "campaign_id": "12345",
        "content_html": "<p>Market update.</p>",
        "post_type": "text_only",
        "is_paid": True,
        "patreon_url": "https://www.patreon.com/posts/example-999",
    })
    assert item is not None
    assert item.title == "Example Post"
    assert "Market update." in item.body_md
    assert item.extra["campaign_id"] == "12345"


def test_already_scraped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import kb.scrapers.patreon as patreon_mod

    monkeypatch.setattr(patreon_mod, "DATA_DIR", tmp_path)
    sc = PatreonScraper()
    d = {
        "external_id": "999",
        "channel_handle": "macroalf",
        "published_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
    }
    assert sc.already_scraped(d) is False

    folder = tmp_path / "patreon" / "macroalf" / "2024-01-15__999"
    folder.mkdir(parents=True)
    (folder / "content.md").write_text(
        "# cached\n\n" + ("body " * 20),
        encoding="utf-8",
    )
    assert sc.already_scraped(d) is True
