"""Scraper registry."""
from __future__ import annotations

from .base import BaseScraper
from .macrovoices import MacroVoicesScraper
from .patreon import PatreonScraper
from .youtube import YouTubeScraper
from .hkej import HKEJScraper
from .yahoohk import YahooHKScraper
from .master_insight import MasterInsightScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    MacroVoicesScraper.code: MacroVoicesScraper,
    YouTubeScraper.code: YouTubeScraper,
    HKEJScraper.code: HKEJScraper,
    PatreonScraper.code: PatreonScraper,
    YahooHKScraper.code: YahooHKScraper,
    MasterInsightScraper.code: MasterInsightScraper,
}


def get(code: str) -> BaseScraper:
    cls = SCRAPERS[code]
    return cls()
