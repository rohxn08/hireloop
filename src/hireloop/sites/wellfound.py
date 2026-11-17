"""Wellfound page handling.

Everything site-specific lives here, so when Wellfound changes its markup
only this file needs fixing. Parsing is split into pure `parse_*` functions
over page HTML (testable offline) and thin async wrappers that fetch pages.

What the pages look like (checked against the live site, Nov 2026):
- Search pages (/role/<role>, /role/r/<role>, /role/l/<role>/<city>, ?page=N)
  embed a Next.js Apollo cache in __NEXT_DATA__ with JobListingSearchResult
  and StartupResult objects.
- Job pages (/jobs/<id>-<slug>) have no __NEXT_DATA__ but carry a schema.org
  JobPosting in JSON-LD. The slug is required (bare /jobs/<id> is a 404), and
  expired listings return 410.
"""

import asyncio
import html as htmllib
import json
import random
import re
from datetime import date
from typing import Any
from urllib.parse import quote

from ..browser import Browser
from .common import DESCRIPTION_CHARS, ApplyError as _ApplyError, dedupe as _dedupe, json_ld, notice as _notice
from .common import place as _place, summary as _summary, text as _text, trim as _trim

NAME = "wellfound"
LABEL = "Wellfound"
BASE_URL = "https://wellfound.com"
LOGIN_URL = f"{BASE_URL}/login"
CHECK_URL = f"{BASE_URL}/role/r/software-engineer"
# URL fragments of the sign-in pages, so the login helper doesn't reload them while you type.
LOGIN_PAGES = ("/login", "/signup", "/auth", "accounts.google")
APPLIED_URL = f"{BASE_URL}/jobs/applications"
PER_PAGE = 20


# ---------- helpers ----------

def _slug(text: str) -> str:
    return quote(re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-"))


def owns(job_id: str) -> bool:
    """Wellfound ids are '<number>-<slug>', with no site prefix."""
    return bool(re.fullmatch(r"\d+-[a-z0-9-]+", job_id))


def job_url(job_id: str) -> str:
    return f"{BASE_URL}/jobs/{job_id}"


def _next_data(page_html: str) -> dict[str, Any] | None:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S)
    return json.loads(m.group(1)) if m else None


def _apollo(page_html: str) -> dict[str, Any]:
    data = _next_data(page_html) or {}
    state = data.get("props", {}).get("pageProps", {}).get("apolloState", {})
    return state.get("data", state)


# ---------- login ----------

def parse_logged_in(page_html: str) -> bool | None:
    """True/False from the Apollo viewer on a search page; None if not found."""
    a = _apollo(page_html)
    viewer = a.get("ROOT_QUERY", {}).get("talent", {}).get("viewer")
    if isinstance(viewer, dict) and "__ref" in viewer:
        # Logged in: the viewer is a reference like "TalentViewer:<user id>".
        viewer = a.get(viewer["__ref"], {})
        return bool(viewer.get("id"))
    if not isinstance(viewer, dict) or "currentUser" not in viewer:
        return None
    # Logged out: an inline viewer with id and currentUser set to null.
    return viewer["currentUser"] is not None


async def is_logged_in(browser: Browser) -> bool:
    page = await browser.goto(CHECK_URL)
    if "/login" in page.url:
        return False
    found = parse_logged_in(await page.content())
    return bool(found)


async def logged_in_on(tab) -> bool:
    """Login helper's check, in a background tab and without the human-speed pauses."""
    await tab.goto(CHECK_URL, wait_until="domcontentloaded")
    return bool(parse_logged_in(await tab.content()))


# ---------- search ----------

def search_url(role: str, location: str | None, remote: bool, page: int = 1) -> str:
    if remote:
        url = f"{BASE_URL}/role/r/{_slug(role)}"
    elif location:
        url = f"{BASE_URL}/role/l/{_slug(role)}/{_slug(location)}"
    else:
        url = f"{BASE_URL}/role/{_slug(role)}"
    return url if page == 1 else f"{url}?page={page}"


def parse_search(page_html: str) -> tuple[list[dict[str, Any]], int]:
    """Return (rows in page order, total job count)."""
    a = _apollo(page_html)
    talent = a.get("ROOT_QUERY", {}).get("talent", {})
    results = next((v for k, v in talent.items() if k.startswith("seoLandingPageJobSearchResults")), None)
    if not results:
        return [], 0

    def deref(ref: Any) -> dict[str, Any]:
        return a.get(ref.get("__ref", ""), {}) if isinstance(ref, dict) else {}

    rows = []
    for s in map(deref, results.get("startups", [])):
        for j in map(deref, s.get("highlightedJobListings", [])):
            if not j.get("id"):
                continue
            desc = _text(j.get("description") or "")
            lo, hi = j.get("yearsExperienceMin"), j.get("yearsExperienceMax")
            rows.append({
                "id": f"{j['id']}-{j.get('slug', '')}".rstrip("-"),
                "title": j.get("title"),
                "company": s.get("name"),
                "location": ", ".join(_dedupe(j.get("locationNames") or [])) or None,
                "remote": j.get("remote"),
                "salary": htmllib.unescape(j.get("compensation") or "") or None,
                "years": f"{lo}+" if lo and not hi else (f"{lo}-{hi}" if hi else None),
                "summary": _summary(desc),
            })
    return rows, int(results.get("totalJobCount") or 0)


async def search_jobs(browser: Browser, role: str, location: str | None, remote: bool, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_no = 1
    while len(rows) < limit:
        page = await browser.goto(search_url(role, location, remote, page_no))
        found, total = parse_search(await page.content())
        rows.extend(found)
        if not found or page_no * PER_PAGE >= total:
            break
        page_no += 1
    return rows[:limit]


# ---------- job detail ----------

def _labeled(page_html: str, label: str) -> str | None:
    """Text of the small 'Label / value' blocks in the job sidebar."""
    m = re.search(rf'font-semibold">{re.escape(label)}</span>(.*?)</div>', page_html, re.S)
    return _text(m.group(1)) or None if m else None


def _salary_ld(d: dict[str, Any]) -> str | None:
    b = d.get("baseSalary") or {}
    v = b.get("value") or {}
    lo, hi = v.get("minValue"), v.get("maxValue")
    if lo is None and hi is None:
        return None
    rng = "–".join(f"{x:,.0f}" for x in (lo, hi) if x is not None)
    return f"{b.get('currency', '')} {rng} / {str(v.get('unitText', 'year')).lower()}".strip()


def _parse_job_ld(page_html: str, job_id: str) -> dict[str, Any] | None:
    """Logged-out job page: schema.org JSON-LD plus the sidebar."""
    d = json_ld(page_html)
    if not d:
        return None

    header = re.search(r"<h1[^>]*>.*?</h1>\s*<ul[^>]*>\s*<li[^>]*>(.*?)</li>", page_html, re.S)
    comp = _text(header.group(1)) if header else ""
    months = (d.get("experienceRequirements") or {}).get("monthsOfExperience")
    skills_block = re.search(r'font-semibold">Skills</span>(.*?)<h2', page_html, re.S)
    skills = [_text(s) for s in re.findall(r"<div[^>]*rounded-3xl[^>]*>(.*?)</div>", skills_block.group(1))] if skills_block else []

    return {
        "id": job_id,
        "title": d.get("title"),
        "company": (d.get("hiringOrganization") or {}).get("name"),
        "url": job_url(job_id),
        # The header text includes equity ("$150k – $180k • 0.5% – 1.0%"); JSON-LD doesn't.
        "salary": comp if re.search(r"\d", comp) else _salary_ld(d),
        "type": d.get("employmentType"),
        "remote": d.get("jobLocationType") == "TELECOMMUTE",
        "remote_policy": _labeled(page_html, "Remote Work Policy"),
        "locations": _dedupe([x for x in map(_place, d.get("jobLocation") or []) if x]),
        "hires_in": [c.get("name") for c in d.get("applicantLocationRequirements") or [] if c.get("name")],
        "years": round(months / 12, 1) if months else None,
        "visa": _labeled(page_html, "Visa Sponsorship"),
        "relocation": _labeled(page_html, "Relocation"),
        "stack": skills,
        "posted": (d.get("datePosted") or "")[:10] or None,
        "applies_on_site": d.get("directApply"),
        "description": _trim(_text(d.get("description") or ""), DESCRIPTION_CHARS),
    }


def _parse_job_apollo(page_html: str, job_id: str) -> dict[str, Any] | None:
    """Logged-in job page: no JSON-LD, but a full JobListing in the Apollo cache."""
    a = _apollo(page_html)
    ref = next((v.get("__ref") for k, v in a.get("ROOT_QUERY", {}).items()
                if k.startswith("jobListing(") and isinstance(v, dict)), None)
    j = a.get(ref or "", {})
    if not j.get("title"):
        return None

    def deref(x: Any) -> dict[str, Any]:
        return a.get(x.get("__ref", ""), x) if isinstance(x, dict) else {}

    remote_kind = deref(j.get("remoteConfig")).get("kind")
    yrs = j.get("yearsExperienceMin")
    started = j.get("liveStartAt")
    desc = re.sub(r"\*\*|__", "", j.get("description") or "")
    issues = (j.get("currentUserQualificationReport") or {}).get("errors") or []
    return {
        "id": job_id,
        "title": j.get("title"),
        "company": deref(j.get("startup")).get("name", "").strip() or None,
        "url": job_url(job_id),
        "salary": htmllib.unescape(j.get("compensation") or "") or None,
        "type": j.get("jobType"),
        "remote": j.get("remote"),
        "remote_policy": {"ONSITE": "In office", "REMOTE": "Remote only", "HYBRID": "Hybrid", "ONSITE_OR_REMOTE": "On-site or remote"}.get(remote_kind, remote_kind),
        "locations": _dedupe(j.get("locationNames") or []),
        "hires_in": j.get("acceptedRemoteLocationNames") or [],
        "years": f"{yrs}+" if yrs else None,
        "visa": "Available" if j.get("visaSponsorship") else "Not available",
        "relocation": "Allowed" if j.get("allowRelocation") else "Not allowed",
        "stack": [t.get("displayName") for t in map(deref, j.get("skills") or []) if t.get("displayName")],
        "posted": date.fromtimestamp(started).isoformat() if started else None,
        "applies_on_site": j.get("source") == "PLATFORM" if j.get("source") else None,
        # Wellfound's own eligibility check for this user; non-empty means it may block applying.
        "eligibility_issues": [str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in issues] or None,
        "description": _trim(_text(desc), DESCRIPTION_CHARS),
    }


def parse_job(page_html: str, job_id: str) -> dict[str, Any]:
    return (_parse_job_apollo(page_html, job_id) or _parse_job_ld(page_html, job_id)
            or {"error": "Could not find job data on the page.", "id": job_id})


async def get_job(browser: Browser, job_id: str) -> dict[str, Any]:
    if not owns(job_id):
        return {"error": "job_id must be the full id from search_jobs, like '4417481-software-engineer'."}
    page = await browser.goto(job_url(job_id))
    if browser.last_status == 410:
        return {"error": "This job has expired.", "id": job_id}
    if browser.last_status == 404:
        return {"error": "Job not found. Check the id from search_jobs.", "id": job_id}
    return parse_job(await page.content(), job_id)


# ---------- applying (logged in) ----------
#
# /jobs/<id>-<slug>?autoOpenApplication=true opens a modal whose <form> holds
# one <label> per question, each wrapping a control named
# customQuestionAnswers[<key>][answer]. A trailing "*" in the question marks it
# required. An optional "Improve your odds" warning box sits above them.
# Nothing is uploaded per application: Wellfound sends your profile.

SUBMIT_BUTTON = '[data-test="JobApplicationModal--SubmitButton"]'
FIELD_NAME = "customQuestionAnswers[{key}][answer]"
YES = {"yes", "y", "true", "1", "checked"}


def parse_application(page_html: str) -> dict[str, Any] | None:
    """Questions and warnings from the open apply modal, or None if it isn't open."""
    i = page_html.find("JobApplicationModal--SubmitButton")
    if i < 0:
        return None
    form = page_html[page_html.rfind("<form", 0, i):i]
    first_label = form.find("<label")
    head = form[:first_label] if first_label >= 0 else ""
    # The warning box's <span> holds the actual message ("outside the years of experience...").
    span = re.search(r"<span[^>]*>(.*?)</span>", head, re.S)
    warning = _text(span.group(1) if span else head)

    questions: dict[str, dict[str, Any]] = {}
    for label in re.findall(r"<label[^>]*>(.*?)</label>", form, re.S):
        ctl = re.search(r'<(input|textarea|select)\b([^>]*)name="customQuestionAnswers\[(\d+)\]\[answer\]"', label)
        if not ctl:
            continue
        tag, attrs, key = ctl.groups()
        q = _text(label[:ctl.start()])
        kind = tag if tag != "input" else (re.search(r'type="(\w+)"', attrs + label[ctl.start():ctl.end()]) or [None, "text"])[1]
        entry = questions.setdefault(key, {
            "key": key, "question": q.rstrip("*").strip(), "type": kind,
            "required": q.endswith("*"), "options": None,
        })
        if tag == "select":
            sel = label[ctl.start():label.find("</select>", ctl.start())]
            entry["options"] = [o for o in (_text(x) for x in re.findall(r"<option[^>]*>(.*?)</option>", sel, re.S)) if o]
        elif kind == "radio":
            val = re.search(r'value="([^"]*)"', label[ctl.start():])
            entry["options"] = (entry["options"] or []) + [_text(label[label.find(">", ctl.end()) + 1:]) or (val[1] if val else "")]
    return {"questions": list(questions.values()), "warnings": _trim(warning, 300) if warning else None}


# Wellfound caps how many applications can be active at once (the number isn't
# published); slots free up as applications expire. Its warning is matched on
# these words, since its exact wording hasn't been seen yet.
LIMIT_WORDS = re.compile(r"maximum|limit|too many|active (job )?applications", re.I)

class ApplyError(_ApplyError):
    def __init__(self, message: str, notice: str | None = None) -> None:
        super().__init__(message, notice)
        self.limit_reached = bool(notice and LIMIT_WORDS.search(notice))
        if self.limit_reached:
            self.args = ("Wellfound's limit on active applications seems to be reached. "
                         "Slots free up as older applications expire.",)


async def _open_application(browser: Browser, job_id: str):
    page = await browser.goto(f"{BASE_URL}/jobs/{job_id}?autoOpenApplication=true")
    if browser.last_status in (404, 410):
        raise ApplyError("This job has expired or doesn't exist.")
    try:
        await page.wait_for_selector(SUBMIT_BUTTON, timeout=15_000)
    except Exception:
        raise ApplyError("The application form didn't open. You may have already applied, the job may "
                         "apply on an external site, you may have hit Wellfound's limit on active "
                         "applications, or you may need to log in again.", await _notice(page)) from None
    return page


async def application_questions(browser: Browser, job_id: str) -> dict[str, Any]:
    page = await _open_application(browser, job_id)
    html = await page.content()
    form = parse_application(html)
    if form is None:
        raise ApplyError("Could not read the application form.")
    # The apply page is the job page, so its details come along for free.
    job = parse_job(html, job_id)
    return {**form, "job": None if "error" in job else job}


# ---------- the Applied page ----------
#
# After "Send application", Wellfound lists the application as "Scheduled" with
# an "Undo · m:ss" countdown (5 minutes, seen Nov 2026); only then is it
# delivered to the company and shown as "Pending".

def parse_scheduled(page_text: str) -> list[int]:
    """Seconds left on each still-scheduled application's undo window."""
    left = [int(m) * 60 + int(s) for m, s in re.findall(r"Scheduled\s*Undo\s*·\s*(\d+):(\d{2})", page_text)]
    # A "Scheduled" card whose timer we can't read: assume a short wait rather than none.
    unread = len(re.findall(r"\bScheduled\b", page_text)) - len(left)
    return left + [30] * max(0, unread)


async def scheduled_remaining(browser: Browser) -> int:
    """Longest undo countdown still running on the Applied page (0 if none)."""
    page = await browser.goto(APPLIED_URL)
    try:
        await page.wait_for_selector('[data-test="TabGroup-Tab--Ongoing"]', timeout=10_000)
    except Exception:
        pass
    await asyncio.sleep(1.5)  # let the countdowns render
    return max(parse_scheduled(await page.evaluate("document.body.innerText")), default=0)


async def submit(browser: Browser, job_id: str, answers: dict[str, str]) -> None:
    """Fill the form and send it. Raises ApplyError unless Wellfound confirms."""
    page = await _open_application(browser, job_id)
    form = parse_application(await page.content())
    if form is None:
        raise ApplyError("Could not read the application form.")
    by_key = {q["key"]: q for q in form["questions"]}
    if unknown := set(answers) - set(by_key):
        raise ApplyError(f"These keys aren't on the form (it may have changed): {sorted(unknown)}. Run draft_application again.")
    if missing := [q["question"] for q in form["questions"] if q["required"] and not str(answers.get(q["key"], "")).strip()]:
        raise ApplyError(f"Required questions have no answer: {missing}")

    for key, answer in answers.items():
        q, sel = by_key[key], f'[name="{FIELD_NAME.format(key=key)}"]'
        if q["type"] == "select":
            await page.select_option(sel, label=answer)
        elif q["type"] == "radio":
            await page.get_by_label(answer, exact=True).check()
        elif q["type"] == "checkbox":
            if answer.strip().lower() in YES:
                await page.check(sel)
        else:
            await browser.type_like_human(page, sel, answer)

    # Read it over, then click. Never retried: a retry could apply twice.
    await asyncio.sleep(random.uniform(1.5, 4))
    await browser.click_like_human(page, SUBMIT_BUTTON)
    try:
        # The modal closes once Wellfound accepts the application.
        await page.wait_for_selector(SUBMIT_BUTTON, state="detached", timeout=20_000)
    except Exception:
        raise ApplyError("Wellfound didn't confirm the submission. Check the browser window before "
                         "trying again, so you don't apply twice.", await _notice(page)) from None
