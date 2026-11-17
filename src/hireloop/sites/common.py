"""Helpers shared by the site modules: HTML to text, JSON-LD, and the apply error."""

import html as htmllib
import json
import re
from typing import Any

DESCRIPTION_CHARS = 1500
SUMMARY_CHARS = 140


def text(fragment: str) -> str:
    """HTML fragment -> plain text with paragraph breaks kept."""
    s = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", fragment, flags=re.I)
    s = re.sub(r"<li[^>]*>", "• ", s, flags=re.I)
    s = htmllib.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[​‌‍﻿]", "", s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def trim(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def summary(desc: str) -> str | None:
    """First real sentence of a description, skipping headings like 'About Us'."""
    for line in desc.split("\n"):
        line = re.sub(r"[#*_`>]+", "", line).strip(" •-:")
        if len(line) >= 40:
            return trim(line, SUMMARY_CHARS)
    return None


def dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def json_ld(page_html: str, kind: str = "JobPosting") -> dict[str, Any] | None:
    """The first schema.org object of this @type in the page's JSON-LD."""
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page_html, re.S):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for item in d if isinstance(d, list) else d.get("@graph", [d]) if isinstance(d, dict) else []:
            if isinstance(item, dict) and item.get("@type") == kind:
                return item
    return None


def place(p: dict[str, Any]) -> str:
    """A schema.org Place as 'City, Country'."""
    a = p.get("address") or {}
    country = a.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    return ", ".join(x for x in (a.get("addressLocality"), country) if x)


class ApplyError(Exception):
    """Applying failed. `notice` is what the site showed; `limit_reached` means stop for the day."""

    def __init__(self, message: str, notice: str | None = None) -> None:
        self.notice = notice or None
        self.limit_reached = False
        super().__init__(message)


NOTICE_JS = """() => [...document.querySelectorAll(
  '[role=dialog], [role=alert], [role=status], .ReactModal__Content, [class*=flash], [class*=toast], [class*=Toast], [class*=fieldError], [class*=error], [class*=Error]')]
  .map(e => e.innerText.trim()).filter(Boolean).join(' | ').slice(0, 500)"""


async def notice(page) -> str | None:
    """Whatever the site is showing in dialogs, alerts, toasts or field errors."""
    try:
        return await page.evaluate(NOTICE_JS) or None
    except Exception:
        return None
