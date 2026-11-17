"""One shared, persistent Chromium session, opened lazily.

Also the "behave like a person" helpers: random pauses between page loads,
a little scrolling, typing at human speed, moving the mouse to a button before
clicking it, retries for page loads, and waiting for you to clear a Cloudflare
check in the visible window. Submitting is never retried (see the site modules' submit).
"""

import asyncio
import logging
import os
import random
import re
import time

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from . import config

log = logging.getLogger(__name__)

# Shared by the server and the login script, so both open the profile the same
# way. Dropping --enable-automation hides the "controlled by automated
# software" flag, which makes sign-in pages (Google especially) refuse to work.
LAUNCH_OPTIONS = {
    "ignore_default_args": ["--enable-automation"],
    "args": ["--disable-blink-features=AutomationControlled"],
}

NAV_ATTEMPTS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}
CHALLENGE_WAIT_S = 120

# Wellfound loads Cloudflare Turnstile on every page but only shows its overlay
# ("One more step before you proceed...") when it wants a check. Cloudflare's
# full-page check has the title "Just a moment...". Indeed's own check page is
# titled "Security Check" ("Additional Verification Required").
CHALLENGE_JS = """() => {
  const t = document.title;
  if (t.startsWith('Just a moment') || /security check|verification required/i.test(t)) return true;
  const w = document.getElementById('turnstile_widget');
  const overlay = w && w.parentElement;
  return !!(overlay && getComputedStyle(overlay).display !== 'none');
}"""


class BrowserError(Exception):
    pass


def ensure_display() -> None:
    """Find the desktop for the visible browser when the environment doesn't say.

    MCP clients often start servers with a trimmed environment that drops
    DISPLAY, so Chromium can't open a window. Fall back to the local X server
    (which XWayland also provides) when its socket exists.
    """
    if config.HEADLESS or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    sockets = sorted(p for p in os.listdir("/tmp/.X11-unix") if p[1:].isdigit()) if os.path.isdir("/tmp/.X11-unix") else []
    if sockets:
        os.environ["DISPLAY"] = f":{sockets[0][1:]}"
        log.info("DISPLAY wasn't set; using %s", os.environ["DISPLAY"])


class Browser:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self._last_nav = 0.0
        self.last_status: int | None = None

    async def page(self) -> Page:
        async with self._lock:
            if self._ctx is None:
                config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                ensure_display()
                self._pw = await async_playwright().start()
                try:
                    self._ctx = await self._pw.chromium.launch_persistent_context(
                        str(config.PROFILE_DIR),
                        headless=config.HEADLESS,
                        viewport={"width": 1280, "height": 900},
                        **LAUNCH_OPTIONS,
                    )
                except PlaywrightError as e:
                    await self._pw.stop()
                    self._pw = None
                    msg = str(e)
                    if "XServer" in msg or "DISPLAY" in msg:
                        raise BrowserError("Couldn't open the browser window (no display found). Set DISPLAY "
                                           "for the MCP server, or HIRELOOP_HEADLESS=1 to run hidden.") from None
                    if "ProcessSingleton" in msg or "profile" in msg.lower() and "in use" in msg.lower():
                        raise BrowserError("The browser profile is already open, probably by "
                                           "hireloop-login. Close that window and try again.") from None
                    raise BrowserError(f"Couldn't start the browser: {msg.splitlines()[0]}") from None
                # If the window gets closed (by you, or a crash), start a fresh one next time.
                self._ctx.on("close", lambda _: self._forget())
            pages = self._ctx.pages
            return pages[0] if pages else await self._ctx.new_page()

    async def goto(self, url: str) -> Page:
        """Load a page at human speed, retrying network errors and 429/5xx.
        404 and 410 are returned as-is (see last_status) for the caller to handle."""
        page = await self.page()
        for attempt in range(1, NAV_ATTEMPTS + 1):
            wait = random.uniform(config.MIN_DELAY, config.MAX_DELAY) - (time.monotonic() - self._last_nav)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                self.last_status = response.status if response else None
                problem = f"HTTP {self.last_status}" if self.last_status in RETRY_STATUSES else None
            except PlaywrightError as e:
                self.last_status, problem = None, str(e).splitlines()[0]
            self._last_nav = time.monotonic()

            if problem is None:
                await self._wait_out_challenge(page)
                await self._glance(page)
                return page
            if attempt == NAV_ATTEMPTS:
                raise BrowserError(f"Couldn't load {url} after {NAV_ATTEMPTS} tries ({problem}).")
            # Back off harder when rate-limited.
            backoff = (30 if self.last_status == 429 else 5) * attempt + random.uniform(0, 3)
            log.warning("Load failed (%s), retrying in %.0fs: %s", problem, backoff, url)
            await asyncio.sleep(backoff)
        raise AssertionError("unreachable")

    async def _wait_out_challenge(self, page: Page) -> None:
        """If Cloudflare asks for a check, let the user clear it in the window."""
        if not await page.evaluate(CHALLENGE_JS):
            return
        if config.HEADLESS:
            raise BrowserError("The site is showing a bot check. Set HIRELOOP_HEADLESS=0 "
                               "and clear it in the browser window.")
        log.warning("Bot check showing; waiting up to %ss for you to clear it.", CHALLENGE_WAIT_S)
        for _ in range(CHALLENGE_WAIT_S // 2):
            await asyncio.sleep(2)
            if not await page.evaluate(CHALLENGE_JS):
                await asyncio.sleep(random.uniform(1, 2))
                return
        raise BrowserError("A bot check is waiting in the browser window. "
                           "Clear it there, then try again.")

    async def _glance(self, page: Page) -> None:
        """Move the mouse and scroll a little, like someone reading the page."""
        try:
            await page.mouse.move(random.randint(200, 1000), random.randint(150, 700), steps=random.randint(8, 20))
            for _ in range(random.randint(1, 3)):
                await page.mouse.wheel(0, random.randint(150, 500))
                await asyncio.sleep(random.uniform(0.4, 1.2))
            await page.mouse.wheel(0, -random.randint(600, 1500))
        except PlaywrightError:
            pass  # Cosmetic only; never fail a load over it.

    async def type_like_human(self, page: Page, selector: str, text: str) -> None:
        """Click a field and type into it with uneven keystroke timing."""
        await self.click_like_human(page, selector)
        await page.fill(selector, "")
        # Long answers type faster, so a 1,000-character note doesn't take minutes.
        per_key = 55 if len(text) < 300 else 25
        for chunk in _chunks(text):
            await page.locator(selector).press_sequentially(chunk, delay=random.randint(per_key // 2, per_key * 3 // 2))
            await asyncio.sleep(random.uniform(0.1, 0.6))

    async def click_like_human(self, page: Page, selector: str) -> None:
        """Glide the mouse to a random point on the element, pause, then click."""
        el = page.locator(selector).first
        await el.scroll_into_view_if_needed()
        box = await el.bounding_box()
        if not box:
            await el.click()
            return
        x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        await page.mouse.move(x, y, steps=random.randint(12, 30))
        await asyncio.sleep(random.uniform(0.2, 0.7))
        await page.mouse.click(x, y)

    def _forget(self) -> None:
        pw, self._ctx, self._pw = self._pw, None, None
        if pw:
            asyncio.ensure_future(pw.stop())

    async def close(self) -> None:
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()
        self._ctx = self._pw = None


def _chunks(text: str) -> list[str]:
    """Split text into bursts of a few words, so typing has natural pauses.
    Joining the chunks gives back the exact original text."""
    out, cur, limit = [], "", random.randint(15, 45)
    for piece in re.findall(r"\S+\s*|\s+", text):
        cur += piece
        if len(cur) >= limit:
            out.append(cur)
            cur, limit = "", random.randint(15, 45)
    return out + [cur] if cur else out
