"""Deprecated shim — use :mod:`kb.scrapers.blogspot`.

Greenhorn (https://greenhornfinancefootnote.blogspot.com/) is one blog
hosted on Blogspot; the platform module is now ``blogspot``. This file
remains only so ``import kb.scrapers.greenhorn`` and the ``greenhorn``
registry key keep working.
"""

from .blogspot import BlogspotScraper, GreenhornScraper  # noqa: F401

__all__ = ["BlogspotScraper", "GreenhornScraper"]
