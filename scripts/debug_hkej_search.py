"""Debug HKEJ search — wait for Cloudflare challenge to clear."""
from __future__ import annotations

import asyncio
import re
import urllib.parse

from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox


async def main(name: str = "李聲揚") -> None:
    url = (
        "https://search.hkej.com/template/fulltextsearch/php/search.php?author="
        + urllib.parse.quote(name)
    )
    print("URL:", url)
    async with AsyncCamoufox(headless=False, humanize=True) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # Wait up to 45s for real search results (not CF challenge)
        for i in range(15):
            html = await page.content()
            if "Performing security verification" not in html and (
                "article" in html.lower() or "搜尋結果" in html or "search result" in html.lower()
            ):
                print(f"Challenge cleared after ~{i * 3}s")
                break
            print(f"  waiting... ({i+1}/15) title={await page.title()}")
            await asyncio.sleep(3)
        else:
            print("Challenge may not have cleared")
        await page.screenshot(path="scripts/debug_search.png")
        html = await page.content()

    with open("scripts/debug_search.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML length:", len(html))
    print("Title snippet:", html[html.find("<title>"):html.find("</title>")+8] if "<title>" in html else "no title")

    soup = BeautifulSoup(html, "lxml")
    for sel in [
        "a[href*='article?id=']",
        "a[href*='/wm/article/id/']",
        "a[href*='article']",
        ".search-result a",
        "a",
    ]:
        anchors = soup.select(sel)
        print(f"  {sel}: {len(anchors)}")
        for a in anchors[:8]:
            href = a.get("href", "")
            if "article" in href.lower() or "id=" in href:
                print(f"    {href[:90]} | {a.get_text(strip=True)[:50]}")

    ids = re.findall(r"article/id/(\d+)", html) + re.findall(r"[?&]id=(\d+)", html)
    print("IDs:", sorted(set(ids))[:15])


if __name__ == "__main__":
    asyncio.run(main())
