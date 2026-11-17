"""Open a visible browser on the saved profile so you can log in by hand.

Run with: uv run hireloop-login [wellfound|indeed]   (wellfound if not given)
It closes by itself once you're logged in (no need to press Enter), so it also
works from Claude Code's `!` prompt. Stop the MCP server first: Chromium can't
open the same profile twice.

You must log in inside the window this opens. Logging in from your normal
browser doesn't help, because the session is saved in this profile.
"""

import asyncio
import sys

from playwright.async_api import async_playwright

from . import config, sites
from .browser import LAUNCH_OPTIONS, ensure_display

TIMEOUT_MIN = 10


def say(msg: str) -> None:
    print(msg, flush=True)


async def _logged_in(ctx, site) -> bool:
    """Check in a background tab, so the tab you're typing in isn't disturbed."""
    tab = await ctx.new_page()
    try:
        return await site.logged_in_on(tab)
    except Exception:
        return False
    finally:
        await tab.close()


async def _run(site) -> int:
    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ensure_display()
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(str(config.PROFILE_DIR), headless=False, **LAUNCH_OPTIONS)
        if await _logged_in(ctx, site):
            say(f"Already logged in to {site.LABEL}. Nothing to do.")
            await ctx.close()
            return 0

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(site.LOGIN_URL)
        say(f"Profile: {config.PROFILE_DIR}")
        say(f"A Chromium window opened on {site.LOGIN_URL}")
        say("Log in THERE (not in your usual browser). If it shows another page, paste that URL into its address bar.")
        say(f"Tip: if 'Sign in with Google' is blocked, use email + password or {site.LABEL}'s email code instead.")
        say(f"Waiting up to {TIMEOUT_MIN} minutes; this closes by itself once you're in...")

        for _ in range(TIMEOUT_MIN * 60 // 5):
            await asyncio.sleep(5)
            if not ctx.pages:
                say("The browser window was closed before logging in.")
                return 1
            # Only check once you've left the login/signup pages, to avoid
            # reloading anything while you type.
            if any(k in page.url for k in site.LOGIN_PAGES):
                continue
            if await _logged_in(ctx, site):
                await ctx.close()
                say(f"Logged in to {site.LABEL} and saved. The MCP server will reuse this session.")
                return 0

        await ctx.close()
        say("Timed out without detecting a login. Run it again when ready.")
        return 1


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "wellfound"
    try:
        site = sites.get(name)
    except ValueError as e:
        say(str(e))
        sys.exit(2)
    sys.exit(asyncio.run(_run(site)))


if __name__ == "__main__":
    main()
