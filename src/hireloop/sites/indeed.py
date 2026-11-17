"""Indeed page handling.

Job ids are 'indeed:<jk>', where jk is Indeed's 16-character job key.

NOT YET CHECKED AGAINST THE LIVE SITE. Written from Indeed's known page shapes
(Nov 2026) and built to fall back where it can:
- Search (/jobs?q=&l=&start=N) embeds its result cards as JSON in
  window.mosaic.providerData["mosaic-provider-jobcards"]; the card markup
  (data-jk attributes) is the fallback.
- Job pages (/viewjob?jk=) carry window._initialData and often a schema.org
  JobPosting in JSON-LD; #jobDescriptionText is the fallback for the text.
- "Easily apply" jobs open Indeed Apply (smartapply.indeed.com) in a new tab:
  several pages (contact info, resume, employer questions, work experience,
  review), each with a Continue button, ending in "Submit your application".
  Jobs that say "Apply on company site" are skipped.
The apply pages are read in the browser by generic form reading (labels and
field names), not by Indeed's class names, so small redesigns don't break it.
"""

import asyncio
import json
import logging
import random
import re
from typing import Any
from urllib.parse import urlencode

from .. import config
from ..browser import Browser
from .common import DESCRIPTION_CHARS, ApplyError, dedupe, json_ld, notice, place, summary, text, trim

log = logging.getLogger(__name__)

NAME = "indeed"
LABEL = "Indeed"
BASE_URL = config.INDEED_URL
LOGIN_URL = f"https://secure.indeed.com/auth?{urlencode({'continue': BASE_URL + '/'})}"
# Logged-out visits to the profile page are sent to secure.indeed.com/auth.
CHECK_URL = "https://profile.indeed.com/"
LOGIN_PAGES = ("secure.indeed.com", "/auth", "accounts.google", "appleid.apple", "facebook.com")
APPLIED_URL = "https://myjobs.indeed.com/applied"
PREFIX = "indeed:"
PER_PAGE = 10
MAX_PAGES = 5
# Indeed's search filter for remote jobs.
REMOTE_FILTER = "0kf:attr(DSQF7);"
MAX_STEPS = 12


# ---------- ids ----------

def owns(job_id: str) -> bool:
    return job_id.startswith(PREFIX)


def _jk(job_id: str) -> str:
    return job_id.removeprefix(PREFIX)


def job_url(job_id: str) -> str:
    return f"{BASE_URL}/viewjob?jk={_jk(job_id)}"


# ---------- embedded JSON ----------

def _assigned_json(page_html: str, marker: str) -> Any:
    """The JSON value assigned after `marker =` in an inline script, or None."""
    i = page_html.find(marker)
    if i < 0:
        return None
    j = page_html.find("=", i + len(marker))
    k = min((x for x in (page_html.find("{", j), page_html.find("[", j)) if x >= 0), default=-1)
    if j < 0 or k < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(page_html, k)[0]
    except json.JSONDecodeError:
        return None


def _find(data: Any, key: str) -> Any:
    """First non-empty value stored under `key` anywhere in nested JSON."""
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get(key) not in (None, "", [], {}):
                return cur[key]
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            stack.extend(reversed(cur))
    return None


def _salary_text(s: Any) -> str | None:
    """Indeed's salary as shown ("₹40,000 - ₹60,000 a month"), from text or a {min,max,type} object."""
    if isinstance(s, str):
        return text(s) or None
    if not isinstance(s, dict):
        return None
    if s.get("text"):
        return text(s["text"])
    lo, hi, per = s.get("min"), s.get("max"), str(s.get("type") or "").lower()
    if lo is None and hi is None:
        return None
    unit = {"yearly": "a year", "monthly": "a month", "weekly": "a week", "daily": "a day", "hourly": "an hour"}.get(per, per)
    amt = " - ".join(f"₹{x:,.0f}" for x in dedupe([v for v in (lo, hi) if v is not None]))
    return f"{amt} {unit}".strip()


# ---------- login ----------

def parse_logged_in_url(url: str) -> bool:
    return not any(k in url for k in ("secure.indeed.com", "/account/login", "/auth"))


async def is_logged_in(browser: Browser) -> bool:
    page = await browser.goto(CHECK_URL)
    return parse_logged_in_url(page.url)


async def logged_in_on(tab) -> bool:
    """Login helper's check, in a background tab and without the human-speed pauses."""
    await tab.goto(CHECK_URL, wait_until="domcontentloaded")
    return parse_logged_in_url(tab.url)


# ---------- search ----------

def search_url(role: str, location: str | None, remote: bool, start: int = 0) -> str:
    q = {"q": role, "l": location or ""}
    if remote:
        q["sc"] = REMOTE_FILTER
    if start:
        q["start"] = str(start)
    return f"{BASE_URL}/jobs?{urlencode(q)}"


def _row(r: dict[str, Any]) -> dict[str, Any] | None:
    jk = r.get("jobkey")
    if not jk:
        return None
    loc = r.get("formattedLocation") or ""
    return {
        "id": PREFIX + jk,
        "title": r.get("displayTitle") or r.get("title"),
        "company": r.get("company") or r.get("truncatedCompany"),
        "location": text(loc) or None,
        "remote": bool(r.get("remoteLocation")) or "remote" in loc.lower() or None,
        "salary": _salary_text(r.get("salarySnippet")) or _salary_text(r.get("extractedSalary")),
        # Indeed Apply ("Easily apply"); false means the company's own site.
        "easy_apply": bool(r.get("indeedApplyEnabled") or r.get("indeedApplyable")),
        "summary": summary(text(r.get("snippet") or "")),
    }


def _parse_search_dom(page_html: str) -> list[dict[str, Any]]:
    """Fallback: read the job cards' markup."""
    rows = []
    for m in re.finditer(r'data-jk="([0-9a-f]{16})"', page_html):
        card = page_html[m.start():m.start() + 6000]
        title = re.search(r'<span[^>]*title="([^"]+)"', card)
        company = re.search(r'data-testid="company-name"[^>]*>(.*?)</span>', card, re.S)
        loc = re.search(r'data-testid="text-location"[^>]*>(.*?)</div>', card, re.S)
        location = text(loc.group(1)) if loc else ""
        rows.append({
            "id": PREFIX + m.group(1),
            "title": text(title.group(1)) if title else None,
            "company": text(company.group(1)) if company else None,
            "location": location or None,
            "remote": "remote" in location.lower() or None,
            "easy_apply": "Easily apply" in card or None,
        })
    return list({r["id"]: r for r in rows}.values())


def parse_search(page_html: str) -> list[dict[str, Any]]:
    data = _assigned_json(page_html, 'window.mosaic.providerData["mosaic-provider-jobcards"]')
    results = _find(data, "results") if data else None
    rows = [r for r in map(_row, results or []) if r]
    return rows or _parse_search_dom(page_html)


async def search_jobs(browser: Browser, role: str, location: str | None, remote: bool, limit: int) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for n in range(MAX_PAGES):
        page = await browser.goto(search_url(role, location, remote, n * PER_PAGE))
        found = parse_search(await page.content())
        new = [r for r in found if r["id"] not in rows]
        rows.update((r["id"], r) for r in new)
        if not new or len(rows) >= limit:
            break
    return list(rows.values())[:limit]


# ---------- job detail ----------

def _easy_apply(page_html: str) -> bool | None:
    if re.search(r'id="indeedApplyButton"|"indeedApplyEnabled"\s*:\s*true|IndeedApplyButton', page_html):
        return True
    if re.search(r"Apply on company site|\"indeedApplyEnabled\"\s*:\s*false", page_html):
        return False
    return None


def _dom_description(page_html: str) -> str:
    m = re.search(r'id="jobDescriptionText"[^>]*>', page_html)
    if not m:
        return ""
    seg = page_html[m.end():m.end() + 40_000]
    cut = re.search(r'<script|<div[^>]*id="(?!jobDescriptionText)', seg)
    return text(seg[:cut.start()] if cut else seg)


def _ld_salary(d: dict[str, Any]) -> str | None:
    b = d.get("baseSalary") or {}
    v = b.get("value") or {}
    lo, hi = v.get("minValue"), v.get("maxValue")
    if lo is None and hi is None:
        return v.get("value") and f"₹{v['value']:,.0f}"
    unit = {"YEAR": "a year", "MONTH": "a month", "WEEK": "a week", "DAY": "a day", "HOUR": "an hour"}.get(
        str(v.get("unitText", "")).upper(), "")
    return (" - ".join(f"₹{x:,.0f}" for x in dedupe([x for x in (lo, hi) if x is not None])) + f" {unit}").strip()


def _view_job_data(page_html: str) -> dict[str, Any]:
    """The job page's data: window._rootProps.preloadedVJData (seen Nov 2026), else the older
    window._initialData when that is plain JSON."""
    root = _assigned_json(page_html, "window._rootProps")
    if isinstance(root, dict) and isinstance(root.get("preloadedVJData"), dict):
        return root["preloadedVJData"]
    init = _assigned_json(page_html, "window._initialData")
    return init if isinstance(init, dict) else {}


def _vj_easy_apply(vj: dict[str, Any]) -> bool | None:
    """Indeed Apply is on when the page has an enabled Indeed Apply button; None if unknown."""
    if "indeedApplyButtonContainer" not in vj:
        return None
    box = vj["indeedApplyButtonContainer"]
    return bool(box) and not box.get("disabled")


def _vj_applied(vj: dict[str, Any]) -> bool:
    state = (vj.get("saveJobButtonContainerModel") or {}).get("currentJobState")
    return state == "APPLIED" or bool(_find(vj, "appliedStateBannerModel"))


def _vj_salary(vj: dict[str, Any]) -> str | None:
    hdr = _find(vj, "jobInfoHeaderModel") or {}
    if hdr.get("salaryMin") is not None or hdr.get("salaryMax") is not None:
        return _salary_text({"min": hdr.get("salaryMin"), "max": hdr.get("salaryMax"), "type": hdr.get("salaryType")})
    return _salary_text(_find(vj, "salaryText")) or _salary_text(_find(vj, "salarySnippet"))


def parse_job(page_html: str, job_id: str) -> dict[str, Any]:
    d = json_ld(page_html) or {}
    init = _view_job_data(page_html)
    title = d.get("title") or _find(init, "jobTitle")
    if not title:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.S)
        title = text(h1.group(1)) if h1 else None
    if not title:
        return {"error": "Could not find job data on the page.", "id": job_id}

    locs = d.get("jobLocation") or []
    locations = dedupe([x for x in map(place, locs if isinstance(locs, list) else [locs]) if x])
    if not locations and (fl := _find(init, "formattedLocation")):
        locations = [text(fl)]
    desc = d.get("description") or _find(init, "sanitizedJobDescription") or ""
    if isinstance(desc, dict):
        desc = desc.get("content") or ""
    desc = text(desc) or _dom_description(page_html)
    salary = (_ld_salary(d) if d.get("baseSalary") else None) or _vj_salary(init)
    where = " ".join(locations).lower()
    types = [t.get("label") for t in _find(init, "jobTypes") or [] if isinstance(t, dict) and t.get("label")]
    easy = _vj_easy_apply(init)
    return {
        "id": job_id,
        "title": text(title),
        "company": (d.get("hiringOrganization") or {}).get("name") or _find(init, "companyName"),
        "url": job_url(job_id),
        "salary": salary,
        "type": d.get("employmentType") or ", ".join(types) or _find(init, "jobType"),
        "remote": (d.get("jobLocationType") == "TELECOMMUTE" or bool(_find(init, "remoteLocation"))
                   or "remote" in where or "work from home" in where),
        "locations": locations,
        "hires_in": [],
        "posted": (d.get("datePosted") or "")[:10] or _find(init, "age"),
        "applies_on_site": easy if easy is not None else _easy_apply(page_html),
        "already_applied": _vj_applied(init) or None,
        "description": trim(desc, DESCRIPTION_CHARS),
    }


async def get_job(browser: Browser, job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"indeed:[0-9a-f]{16}", job_id):
        return {"error": "job_id must be the id from search_jobs, like 'indeed:0123456789abcdef'."}
    page = await browser.goto(job_url(job_id))
    if browser.last_status in (404, 410):
        return {"error": "This job has expired or doesn't exist.", "id": job_id}
    job = parse_job(await page.content(), job_id)
    if "error" in job:
        job["debug_page"] = await _save_debug(page, f"job-{_jk(job_id)}")
    if "error" not in job and re.search(r"This job has expired|no longer available", await page.content()):
        job["error"] = "This job has expired."
    return job


async def _save_debug(page, name: str) -> str | None:
    """Keep a page we couldn't read in data/debug/ (gitignored), to fix the parser from it."""
    try:
        d = config.DATA_DIR / "debug"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"indeed-{name}.html"
        f.write_text(f"<!-- {page.url} | {await page.title()} -->\n" + await page.content())
        return str(f)
    except Exception:
        return None


# ---------- applying (logged in) ----------

# Reads every visible field on the current Indeed Apply page: its label, type,
# options and current value. Radio and checkbox groups become one question.
# Indeed marks optional questions "(optional)"; the rest are required.
READ_FORM_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const shown = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const labelOf = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return clean(l.innerText); }
    const by = el.getAttribute('aria-labelledby');
    if (by) return clean(by.split(/\s+/).map(i => document.getElementById(i)?.innerText || '').join(' '));
    const wrap = el.closest('label'); if (wrap) return clean(wrap.innerText);
    return clean(el.getAttribute('aria-label') || el.placeholder || '');
  };
  const groupLabel = el => {
    const fs = el.closest('fieldset, [role=radiogroup], [role=group]');
    if (!fs) return '';
    const lg = fs.querySelector('legend');
    if (lg) return clean(lg.innerText);
    const by = fs.getAttribute('aria-labelledby');
    if (by) return clean(by.split(/\s+/).map(i => document.getElementById(i)?.innerText || '').join(' '));
    return clean(fs.getAttribute('aria-label') || '');
  };
  const root = document.querySelector('main') || document.body;
  const out = [], groups = {};
  for (const el of root.querySelectorAll('input, textarea, select')) {
    const type = el.tagName === 'INPUT' ? (el.type || 'text').toLowerCase() : el.tagName.toLowerCase();
    if (['hidden', 'submit', 'button', 'file', 'image', 'reset', 'search'].includes(type)) continue;
    const name = el.name || el.id;
    if (!name) continue;
    const grouped = type === 'radio' || (type === 'checkbox' && document.getElementsByName(name).length > 1);
    if (!grouped && !shown(el)) continue;
    if (grouped) {
      let q = groups[name];
      if (!q) { q = groups[name] = {key: name, question: groupLabel(el), type, options: [], value: ''}; out.push(q); }
      const opt = labelOf(el) || el.value;
      q.options.push(opt);
      if (el.checked) q.value = q.value ? q.value + ', ' + opt : opt;
      continue;
    }
    const q = {key: name, question: labelOf(el), type, options: null,
               value: type === 'checkbox' ? (el.checked ? 'yes' : '') : clean(el.value)};
    if (type === 'select') {
      q.options = [...el.options].map(o => clean(o.text)).filter(t => t && !/^(select|choose)/i.test(t));
      q.value = el.selectedIndex > 0 ? clean(el.options[el.selectedIndex].text) : '';
    }
    out.push(q);
  }
  for (const q of out) {
    q.required = !/\(optional\)/i.test(q.question);
    q.question = q.question.replace(/\s*\(optional\)\s*/i, ' ').replace(/(\s*\*)+\s*$/, '').trim() || q.key;
  }
  const btns = [...document.querySelectorAll('button')].filter(shown).map(b => clean(b.innerText));
  return {url: location.href, heading: clean(document.querySelector('h1, h2')?.innerText),
          questions: out, buttons: btns, body: clean(document.body.innerText).slice(0, 4000)};
}"""

# Sets radio or checkbox group `name` so exactly the options labelled in `wanted`
# are checked (unchecking others, e.g. Indeed's predicted "Yes"). Returns the
# wanted labels that weren't found.
SET_CHOICES_JS = r"""([name, wanted]) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const want = new Set(wanted.map(clean)), seen = new Set();
  for (const el of document.getElementsByName(name)) {
    const l = (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) || el.closest('label');
    const label = clean(l ? l.innerText : el.value), on = want.has(label) || want.has(clean(el.value));
    if (on) { seen.add(label); seen.add(clean(el.value)); }
    if (el.checked !== on && !(el.type === 'radio' && !on)) (l || el).click();
  }
  return wanted.filter(w => !seen.has(clean(w)));
}"""

# Every employer question in this application, from the data Indeed Apply loads
# with its first page (window._initialData, seen Nov 2026). The page itself only
# renders some of them at a time, so the list comes from here.
SCREENER_JS = r"""() => {
  const d = window._initialData || {};
  return [...(d.screenerQuestions || []), ...(d.demographicQuestions || []), ...(d.interviewScreenerQuestions || [])]
    .filter(q => q && q.name)
    .map(q => ({name: q.name, label: q.labelHtml || '', type: q.type || '', format: q.format || '',
                required: !!q.required, prefilled: !!q.prefilled, conditional: q.condition != null,
                options: (q.options || []).map(o => o.label || o.value),
                value: q.formField ? q.formField.value : null}));
}"""

SCREENER_TYPES = {"TEXT": "text", "TEXTAREA": "textarea", "MULTISELECT": "checkbox", "SELECT": "select",
                  "DROPDOWN": "select", "RADIO": "radio", "SINGLESELECT": "radio", "DATE": "date"}

# The Apply button: a link to smartapply.indeed.com on job pages (Nov 2026), formerly a button.
APPLY_BUTTON = 'a[data-testid="viewjob-indeed-apply"], #indeedApplyButton, button[id*="indeedApply"]'
# Visible error messages inside the form only (Indeed's header menu isn't an error).
FIELD_ERRORS_JS = r"""() => {
  const root = document.querySelector('main') || document.body;
  const shown = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  return [...root.querySelectorAll('[role=alert], [id*="error-text"], [id*="errorText"], [class*="error"], [class*="Error"]')]
    .filter(e => shown(e) && !e.closest('header, nav')).map(e => e.innerText.trim()).filter(Boolean)
    .filter((t, i, a) => a.indexOf(t) === i).join(' | ').slice(0, 500);
}"""

SUBMIT_TEXT = re.compile(r"^\s*(submit( your)? application\b|submit\s*$)", re.I)
CONTINUE_TEXT = re.compile(r"^\s*(continue|next|review your application)\b", re.I)
DONE_TEXT = re.compile(r"application has been submitted|your application was sent|you've applied|you applied", re.I)
YES = {"yes", "y", "true", "1", "checked"}


class NewQuestions(ApplyError):
    """Indeed showed required questions that weren't answered; `questions` lists them."""

    def __init__(self, questions: list[dict[str, Any]]) -> None:
        super().__init__("Indeed asked more questions on a later page. Answer these too (from the profile), "
                         "fact_check all answers again, then resubmit.")
        self.questions = questions


async def _open_apply(browser: Browser, job_id: str):
    """Open the job and click Indeed's Apply button. Returns (job page, apply page, job details)."""
    page = await browser.goto(job_url(job_id))
    if browser.last_status in (404, 410):
        raise ApplyError("This job has expired or doesn't exist.")
    html = await page.content()
    job = parse_job(html, job_id)
    if job.get("already_applied"):
        raise ApplyError("Already applied to this job on Indeed.")
    btn = page.locator(APPLY_BUTTON).first
    if not await btn.count():
        if _easy_apply(html) is False:
            raise ApplyError("This job applies on the company's own site, not through Indeed.")
        raise ApplyError("No Apply button found. You may need to log in again (hireloop-login indeed).",
                         await notice(page))
    label = (await btn.inner_text()).strip()
    if re.search(r"\bapplied\b", label, re.I):
        raise ApplyError("Already applied to this job on Indeed.")

    before = set(page.context.pages)
    await browser.click_like_human(page, APPLY_BUTTON)
    apply = None
    for _ in range(30):                      # a new tab, or the same tab navigating
        await asyncio.sleep(0.5)
        new = [p for p in page.context.pages if p not in before]
        if new:
            apply = new[0]
            break
        if "smartapply" in page.url or "indeedapply" in page.url:
            apply = page
            break
    if apply is None:
        raise ApplyError("Indeed's application didn't open.", await notice(page))
    await apply.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(random.uniform(2, 4))
    return page, apply, (None if "error" in job else job)


def parse_screener(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Indeed's screener question data -> our question shape.

    Conditional questions (shown only after certain answers) are never required here;
    the submit step catches them if Indeed does show them.
    """
    out = []
    for q in raw:
        value = q.get("value")
        value = ", ".join(map(str, value)) if isinstance(value, list) else str(value or "")
        out.append({
            "key": q["name"],
            "question": re.sub(r"(\s*\*)+\s*$", "", text(q.get("label") or "")).strip() or q["name"],
            "type": SCREENER_TYPES.get(str(q.get("type")).upper(), str(q.get("type") or "text").lower()),
            "required": bool(q.get("required")) and not q.get("conditional"),
            "options": q.get("options") or None,
            "value": value.strip(),
            "number": q.get("format") in ("INTEGER", "DECIMAL") or None,
            "conditional": bool(q.get("conditional")) or None,
        })
    return out


async def _screener(apply) -> list[dict[str, Any]]:
    try:
        return parse_screener(await apply.evaluate(SCREENER_JS))
    except Exception as e:  # noqa: BLE001 - fall back to reading the page
        log.warning("Couldn't read Indeed's question data: %s", e)
        return []


async def _read(apply) -> dict[str, Any]:
    """Read the current page once it has rendered: Indeed Apply changes pages client-side,
    so wait until a Continue/Submit button (or the confirmation) is showing."""
    await apply.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(random.uniform(1.5, 3))
    form = await apply.evaluate(READ_FORM_JS)
    for _ in range(30):
        if _has(form, CONTINUE_TEXT) or _has(form, SUBMIT_TEXT) or DONE_TEXT.search(form["body"]):
            break
        await asyncio.sleep(0.5)
        form = await apply.evaluate(READ_FORM_JS)
    return form


async def _fill(browser: Browser, apply, q: dict[str, Any], answer: str) -> None:
    sel = f'[name="{q["key"]}"]'
    if q["type"] == "select":
        await apply.select_option(sel, label=answer)
    elif q["type"] == "radio" or (q["type"] == "checkbox" and q.get("options")):
        wanted = [answer.strip()] if q["type"] == "radio" else [a.strip() for a in answer.split(",") if a.strip()]
        if missing := await apply.evaluate(SET_CHOICES_JS, [q["key"], wanted]):
            raise ApplyError(f"{missing} isn't an option for: {q['question']} (options: {q['options']})")
        await asyncio.sleep(random.uniform(0.3, 0.9))
    elif q["type"] == "checkbox":
        if (answer.strip().lower() in YES) != (q.get("value") == "yes"):
            await browser.click_like_human(apply, sel)
    else:
        await browser.type_like_human(apply, sel, answer)


async def _click_button(browser: Browser, apply, pattern: re.Pattern) -> None:
    """Click the button a person would: visible only. Indeed Apply pages also carry hidden
    look-alike buttons (data-testid "hp-continue-button-N", seen Nov 2026) that no person can
    see or click; never touch those."""
    visible = apply.get_by_role("button", name=pattern).filter(visible=True)
    real = visible.and_(apply.locator('[data-testid="continue-button"], [data-testid*="submit"]'))
    btn = real.first if await real.count() else visible.first
    if not await btn.count():
        raise ApplyError(f"No visible button matching {pattern.pattern!r} on this page.")
    await btn.scroll_into_view_if_needed()
    box = await btn.bounding_box()
    if box:
        await apply.mouse.move(box["x"] + box["width"] * random.uniform(0.3, 0.7),
                               box["y"] + box["height"] * random.uniform(0.3, 0.7), steps=random.randint(12, 30))
        await asyncio.sleep(random.uniform(0.2, 0.7))
    await btn.click()


def _has(form: dict[str, Any], pattern: re.Pattern) -> bool:
    return any(pattern.match(b) for b in form["buttons"])


def _public(q: dict[str, Any], step: str | None = None) -> dict[str, Any]:
    """A question as the tools show it. `indeed_prefill` is what Indeed filled in by itself
    (from your profile, an earlier application, or its own guess): review it, don't trust it."""
    return {"key": q["key"], "question": q["question"], "type": q["type"], "required": q["required"],
            "options": q["options"], "number": q.get("number"), "conditional": q.get("conditional"),
            "indeed_prefill": q.get("value") or None, "step": step or None}


def unanswered(screener: list[dict[str, Any]], answers: dict[str, str]) -> list[dict[str, Any]]:
    """Required employer questions without an answer. Indeed's own prefill doesn't count:
    every required one goes through the profile and fact_check."""
    return [q for q in screener if q["required"] and not str(answers.get(q["key"], "")).strip()]


async def _fill_page(browser: Browser, apply, answers: dict[str, str], done: set[str]) -> dict[str, Any]:
    """Fill every answered field on the page. Indeed reveals some questions only after
    earlier ones are answered, so read the page again until nothing new shows up."""
    for _ in range(6):
        form = await apply.evaluate(READ_FORM_JS)
        todo = [q for q in form["questions"] if q["key"] not in done and str(answers.get(q["key"], "")).strip()]
        if not todo:
            return form
        for q in todo:
            await _fill(browser, apply, q, answers[q["key"]])
            done.add(q["key"])
        await asyncio.sleep(random.uniform(0.8, 1.6))
    return await apply.evaluate(READ_FORM_JS)


async def _walk(browser: Browser, apply, answers: dict[str, str], send: bool) -> list[dict[str, Any]]:
    """Go through Indeed Apply page by page.

    The employer's questions come from Indeed's data (all of them, prefilled or not).
    Drafting (send=False): fill nothing; pass pages Indeed already filled (contact info,
    resume); stop at the questions page (or the review page) and return the employer's
    questions plus any other empty required fields seen on the way.
    Sending: refuse up front if a required employer question has no answer; fill the
    answers page by page and click Submit once on the review page.
    """
    screener = await _screener(apply)
    keys = {q["key"] for q in screener}
    if send and (missing := unanswered(screener, answers)):
        raise NewQuestions([_public(q) for q in missing])
    collected: list[dict[str, Any]] = []
    done: set[str] = set()
    last_url = None
    for n in range(MAX_STEPS):
        form = await _read(apply)
        if not send:
            await _save_debug(apply, f"draft-step{n + 1}")
        if DONE_TEXT.search(form["body"]) or "post-apply" in form["url"]:
            if send:
                return collected
            raise ApplyError("Indeed says you've already applied to this job.")
        step = form["heading"]
        on_questions = "questions-module" in form["url"] or any(q["key"] in keys for q in form["questions"])
        # Fields outside the employer's list (contact info, resume, experience).
        others = [q for q in form["questions"] if q["key"] not in keys]
        needed = [q for q in others if q["required"] and not q["value"] and q["key"] not in answers]
        if not send:
            collected += [_public(q, step) for q in others if not q["value"]]
            if on_questions or (needed and not screener):
                return collected + [_public(q) for q in screener]
            if needed:
                return collected
        else:
            if needed:
                raise NewQuestions([_public(q, step) for q in needed])
            form = await _fill_page(browser, apply, answers, done)
            # Anything Indeed revealed that we have no answer for, and that it left empty.
            if late := [q for q in form["questions"] if q["required"] and not q["value"]
                        and q["key"] not in answers and q["key"] not in done]:
                raise NewQuestions([_public(q, step) for q in late])

        if _has(form, SUBMIT_TEXT):
            if not send:
                return collected + [_public(q) for q in screener]
            # Read it over, then click. Never retried: a retry could apply twice.
            await asyncio.sleep(random.uniform(1.5, 4))
            await _click_button(browser, apply, SUBMIT_TEXT)
            for _ in range(40):
                await asyncio.sleep(0.75)
                body = await apply.evaluate("document.body.innerText")
                if DONE_TEXT.search(body) or "post-apply" in apply.url:
                    return collected
            raise ApplyError("Indeed didn't confirm the submission. Check the browser window (and "
                             f"{APPLIED_URL}) before trying again, so you don't apply twice.", await notice(apply))
        if not _has(form, CONTINUE_TEXT):
            raise ApplyError(f"Stuck on an Indeed Apply page with no Continue or Submit button ({step or form['url']}).",
                             await notice(apply))
        if form["url"] == last_url and not form["questions"]:
            raise ApplyError("Indeed Apply isn't moving to the next page.", await notice(apply))
        last_url = form["url"]
        await asyncio.sleep(random.uniform(1, 2.5))
        await _click_button(browser, apply, CONTINUE_TEXT)
        for _ in range(30):                  # Indeed can take a while to move on
            await asyncio.sleep(0.5)
            if apply.url != form["url"]:
                break
        else:
            problem = await apply.evaluate(FIELD_ERRORS_JS)
            raise ApplyError("Indeed didn't accept this page." if problem else
                             "Indeed Apply didn't move to the next page after Continue.", problem or None)
    raise ApplyError(f"Indeed Apply had more than {MAX_STEPS} pages; stopped to be safe.")


async def _close(page, apply) -> None:
    if apply is not None and apply is not page:
        try:
            await apply.close()
        except Exception:
            pass


async def application_questions(browser: Browser, job_id: str) -> dict[str, Any]:
    page, apply, job = await _open_apply(browser, job_id)
    try:
        questions = await _walk(browser, apply, {}, send=False)
    except ApplyError as e:
        e.debug = await _save_debug(apply, f"draft-failed-{_jk(job_id)}")
        raise
    finally:
        await _close(page, apply)
    return {"questions": questions, "warnings": None, "job": job,
            "note": "Indeed sends your Indeed profile and resume. Answer every required question "
                    "from the profile, including ones with indeed_prefill (Indeed's own guess: replace "
                    "it if the profile says otherwise). Optional prefilled ones may be left out."}


async def submit(browser: Browser, job_id: str, answers: dict[str, str]) -> None:
    page, apply, _job = await _open_apply(browser, job_id)
    try:
        await _walk(browser, apply, answers, send=True)
    except ApplyError as e:
        e.debug = await _save_debug(apply, f"submit-failed-{_jk(job_id)}")
        raise
    finally:
        await _close(page, apply)
