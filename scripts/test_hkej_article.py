"""One-shot test: login to HKEJ with camoufox and scrape a single article URL.

Usage:
    uv run python scripts/test_hkej_article.py
"""
from __future__ import annotations
import asyncio, os, sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

URL = "https://www.hkej.com/dailynews/article?id=4429669"
LOGIN_URL = "https://subscribe.hkej.com/member/login?forwardURL=https%3A%2F%2Fwww.hkej.com%2F"


async def main():
    user = os.environ.get("HKEJ_USER", "")
    pw_val = os.environ.get("HKEJ_PASS", "")
    if not user or not pw_val:
        print("ERROR: HKEJ_USER / HKEJ_PASS not set", file=sys.stderr)
        sys.exit(1)

    from camoufox.async_api import AsyncCamoufox

    async with AsyncCamoufox(headless=False, humanize=True) as browser:
        page = await browser.new_page()

        # --- login ---
        print("Navigating to login page ...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=40000)
        # Wait for Turnstile to auto-complete and reveal actual form inputs
        # With headless=False, Turnstile usually auto-verifies after a few seconds
        await asyncio.sleep(12)
        try:
            turnstile_frame = None
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    turnstile_frame = frame
                    break
            if turnstile_frame:
                for sel in ["input[type='checkbox']", ".cb-lb", ".ctp-checkbox-label", ".mark"]:
                    try:
                        await turnstile_frame.click(sel, timeout=3000)
                        print("Clicked Turnstile:", sel)
                        await asyncio.sleep(4)
                        break
                    except Exception:
                        continue
        except Exception as e:
            print(f"Turnstile iframe handling failed (ok): {e}")
        await page.screenshot(path="scripts/debug_login.png")

        inputs = await page.evaluate("""
            () => Array.from(document.querySelectorAll('input')).map(i => ({
                type: i.type, name: i.name, id: i.id,
                placeholder: i.placeholder, visible: i.offsetParent !== null
            }))
        """)
        print("Login page inputs:", inputs)

        user_sel = None
        pass_sel = None
        for inp in inputs:
            t = (inp.get("type") or "text").lower()
            if t == "hidden":  # skip Turnstile response and other hidden fields
                continue
            n = (inp.get("name") or "").lower()
            eid = (inp.get("id") or "").lower()
            def _sel(i=inp):
                if i.get("name"):
                    return f"input[name='{i['name']}']"
                if i.get("id"):
                    return f"input[id='{i['id']}']"
                return f"input[type='{i.get('type','text')}']"
            if t == "password":
                pass_sel = _sel()
            elif t in ("email", "text") or any(x in n + eid for x in ("user","email","login","account","id","member")):
                if user_sel is None:
                    user_sel = _sel()

        print(f"user_sel={user_sel}  pass_sel={pass_sel}")
        if user_sel:
            await page.fill(user_sel, user, timeout=8000)
        if pass_sel:
            await page.fill(pass_sel, pw_val, timeout=8000)

        submitted = False
        for sel in ["button[type=submit]", "input[type=submit]",
                    "input[name='formSubmit']", "input[type='button'][name='formSubmit']",
                    "button:has-text('登入')", "button:has-text('Login')",
                    ".btn-login", "[class*='login']"]:
            try:
                await page.click(sel, timeout=3000)
                submitted = True
                print("Clicked submit:", sel)
                break
            except Exception:
                continue
        if not submitted:
            # Press Enter in the password field as last resort
            if pass_sel:
                await page.focus(pass_sel)
            await page.keyboard.press("Enter")
            print("Submitted via Enter")

        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await page.screenshot(path="scripts/debug_after_login.png")

        cookies = await page.context.cookies()
        print("Cookies after login:", sorted({c['name'] for c in cookies}))
        post_html = await page.content()
        logged_in = "登出" in post_html or "Logout" in post_html
        print("Login detected:", logged_in)

        # --- fetch article ---
        print(f"\nFetching {URL} ...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=40000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await page.screenshot(path="scripts/debug_article.png")
        html = await page.content()

    Path("scripts/debug_article.html").write_text(html, encoding="utf-8")
    print(f"Saved raw HTML ({len(html)} chars) -> scripts/debug_article.html")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.find("h1") or soup.find("h2")
    title = title_el.get_text(strip=True) if title_el else "(not found)"
    print(f"\nTitle:  {title}")

    all_classes = set()
    for tag in soup.find_all(True, class_=True):
        for c in tag.get("class", []):
            all_classes.add(c)
    interesting = sorted(c for c in all_classes if any(
        x in c.lower() for x in [
            "author","column","date","time","content","article","body",
            "para","detail","cat","section","writer","topic","news"
        ]
    ))
    print("\nInteresting CSS classes:", interesting)

    paras = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    print(f"\nFirst 10 <p> texts:")
    for p in paras[:10]:
        print(" ", repr(p[:150]))

    print("\nRelevant <meta> tags:")
    for m in soup.find_all("meta"):
        name = m.get("name","") or m.get("property","")
        if any(x in name.lower() for x in ["author","description","keyword","date","article","section"]):
            print(f"  {name} = {m.get('content','')[:150]}")

    paywall = any(m in html for m in ["付費內容","請登入","訂戶尊享","subscribe_wall","paywall"])
    print(f"\nPaywall markers present: {paywall}")


if __name__ == "__main__":
    asyncio.run(main())
