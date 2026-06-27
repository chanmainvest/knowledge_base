"""Scraper registry."""
from __future__ import annotations

from .base import BaseScraper
from .macrovoices import MacroVoicesScraper
from .patreon import PatreonScraper
from .youtube import YouTubeScraper
from .hkej import HKEJScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    MacroVoicesScraper.code: MacroVoicesScraper,
    YouTubeScraper.code: YouTubeScraper,
    HKEJScraper.code: HKEJScraper,
    PatreonScraper.code: PatreonScraper,
}


def get(code: str) -> BaseScraper:
    cls = SCRAPERS[code]
    return cls()
