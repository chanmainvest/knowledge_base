"""Scraper registry."""
from __future__ import annotations

from .base import BaseScraper
from .macrovoices import MacroVoicesScraper
from .youtube import YouTubeScraper
from .hkej import HKEJScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    MacroVoicesScraper.code: MacroVoicesScraper,
    YouTubeScraper.code: YouTubeScraper,
    HKEJScraper.code: HKEJScraper,
}


def get(code: str) -> BaseScraper:
    cls = SCRAPERS[code]
    return cls()
