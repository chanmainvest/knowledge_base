"""Probe HKEJ subscribe login page structure."""
from __future__ import annotations

import asyncio

from camoufox.async_api import AsyncCamoufox

from kb.config import settings

LOGIN_URL = (
    "https://subscribe.hkej.com/member/login"
    "?forwardURL=%2F%2Fwww.hkej.com%2Flanding%2Findex"
)


async def main() -> None:
    s = settings()
    async with AsyncCamoufox(
        headless=False,
        humanize=True,
        disable_coop=True,
        i_know_what_im_doing=True,
    ) as browser:
        page = await browser.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        html = await page.content()
        with open("scripts/debug_login.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("saved scripts/debug_login.html", "len", len(html))

        # list buttons / inputs
        info = await page.evaluate(
            """
            () => {
              const out = {inputs: [], buttons: [], links: []};
              for (const el of document.querySelectorAll('input, button, a')) {
                const tag = el.tagName.toLowerCase();
                const row = {
                  tag, type: el.type || '',
                  name: el.name || '', id: el.id || '',
                  text: (el.innerText || el.value || '').trim().slice(0, 60),
                  cls: (el.className || '').toString().slice(0, 120),
                  bg: getComputedStyle(el).backgroundColor,
                  visible: !!(el.offsetParent),
                };
                if (tag === 'input') out.inputs.push(row);
                else if (tag === 'button') out.buttons.push(row);
                else if (tag === 'a' && /登|login/i.test(row.text)) out.links.push(row);
              }
              return out;
            }
            """
        )
        print("INPUTS:", info["inputs"])
        print("BUTTONS:", info["buttons"])
        print("LINKS:", info["links"])

        if s.hkej_user and s.hkej_pass:
            # fill
            pwd = await page.query_selector("input[type='password']")
            if pwd:
                form = await pwd.evaluate_handle("el => el.closest('form') || el")
                user = await page.query_selector(
                    "input[type='email'], input[type='text'], input[name*='user' i], input[name*='email' i]"
                )
                if user:
                    await user.fill(s.hkej_user)
                await pwd.fill(s.hkej_pass)
                await asyncio.sleep(1)
                info2 = await page.evaluate(
                    """
                    () => {
                      const btns = Array.from(document.querySelectorAll('button, input[type=submit], a'));
                      return btns.map(el => ({
                        tag: el.tagName,
                        type: el.type || '',
                        text: (el.innerText || el.value || '').trim(),
                        cls: el.className || '',
                        bg: getComputedStyle(el).backgroundColor,
                        visible: !!(el.offsetParent),
                      })).filter(x => x.visible);
                    }
                    """
                )
                print("VISIBLE CLICKABLES AFTER FILL:", info2)
                await page.screenshot(path="scripts/debug_login_filled.png")
                print("screenshot scripts/debug_login_filled.png")


if __name__ == "__main__":
    asyncio.run(main())
