"""Scraper registry."""
from __future__ import annotations

from .base import BaseScraper
from .macrovoices import MacroVoicesScraper
from .patreon import PatreonScraper
from .substack import SubstackScraper
from .youtube import YouTubeScraper
from .hkej import HKEJScraper
from .yahoohk import YahooHKScraper
from .master_insight import MasterInsightScraper
from .businessfocus import BusinessFocusScraper
from .madxcap import MadxcapScraper
from .gorozen import GorozenScraper
from .blogspot import BlogspotScraper, GreenhornScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    MacroVoicesScraper.code: MacroVoicesScraper,
    YouTubeScraper.code: YouTubeScraper,
    HKEJScraper.code: HKEJScraper,
    PatreonScraper.code: PatreonScraper,
    SubstackScraper.code: SubstackScraper,
    YahooHKScraper.code: YahooHKScraper,
    MasterInsightScraper.code: MasterInsightScraper,
    BusinessFocusScraper.code: BusinessFocusScraper,
    MadxcapScraper.code: MadxcapScraper,
    GorozenScraper.code: GorozenScraper,
    BlogspotScraper.code: BlogspotScraper,
    GreenhornScraper.code: GreenhornScraper,
}


def get(code: str) -> BaseScraper:
    cls = SCRAPERS[code]
    return cls()
