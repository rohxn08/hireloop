"""MCP server: small tools that return compact JSON.

Talks to Claude over stdio, so never print() to stdout here. Log to stderr.
"""

import asyncio
import functools
import inspect
import json
import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from . import config, factcheck, notify, profile, sites, store
from .browser import Browser, BrowserError
from .sites import ApplyError, wellfound

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
browser = Browser()
# Title/company of jobs seen this session, so the log and previews can name them.
seen_jobs: dict[str, dict] = {}
# Form questions from draft_application, so the submit preview can show them.
drafts: dict[str, list[dict]] = {}
# Full job details (incl. description) for fit checks and fact checks.
job_cache: dict[str, dict] = {}
# job_id -> hash of the exact answers that passed fact_check.
checked: dict[str, str] = {}


@asynccontextmanager
async def lifespan(_server: FastMCP):
    try:
        yield
    finally:
        await browser.close()


# Workflow guidance lives here once, instead of being repeated in every tool description.
INSTRUCTIONS = (
    "Job applications on Wellfound and Indeed (search_jobs site=; other tools tell the site from the "
    "job id; Indeed ids start 'indeed:'; only Indeed jobs with easy_apply can be sent). Flow: get_profile once; search_jobs; get_job (check fit.ok, "
    "skip_job the misfits with the fit reasons); draft_application (questions + description); "
    "write answers ONLY from the profile; fact_check; then submit_application, or "
    "hold_application if anything fails or you're unsure. If submit returns new_questions (Indeed's "
    "later pages), answer them too, fact_check all answers, and submit again. Never invent facts. "
    + ("AUTO MODE ON: the user gave standing permission to submit and skip without asking, but only "
       "jobs with fit.ok and answers that passed fact_check after your own sentence-by-sentence review. "
       + ("" if config.INDEED_AUTO_APPLY else
          "Except Indeed: show each Indeed submit preview and send only after the user approves it.")
       if config.AUTO_APPLY else
       "Manual mode: show the submit preview and send with confirm=true only after the user approves it; "
       "skip_job also needs the user's OK.")
)
mcp = FastMCP("hireloop", instructions=INSTRUCTIONS, lifespan=lifespan)


def _lean(x):
    """Drop None and empty strings so results stay small."""
    if isinstance(x, dict):
        return {k: _lean(v) for k, v in x.items() if v is not None and v != ""}
    if isinstance(x, list):
        return [_lean(v) for v in x]
    return x


def tool(description: str | None = None):
    """Register a tool whose result goes back as ONE compact JSON text block.

    By default FastMCP indents JSON, splits lists into one block per item, and
    for some return types sends the data a second time as structured content.
    """
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return json.dumps(_lean(result), ensure_ascii=False, separators=(",", ":"))
        wrapper.__signature__ = inspect.signature(fn).replace(return_annotation=str)
        wrapper.__annotations__ = {**fn.__annotations__, "return": str}
        return mcp.tool(description=description or inspect.getdoc(fn), structured_output=False)(wrapper)
    return deco


AUTO_SUBMIT_DOC = "Send an application (answers keyed by question key). Auto mode: confirm=true only if fit.ok and fact_check passed on these exact answers; the server enforces it. confirm=false previews."
MANUAL_SUBMIT_DOC = "Send an application (answers keyed by question key). confirm=false previews; confirm=true only after the user approves that exact preview."


def _auto(job_id: str) -> bool:
    """May this job be sent without asking? Indeed needs its own flag on top of AUTO_APPLY."""
    return config.INDEED_AUTO_APPLY if sites.name_of(job_id) == "indeed" else config.AUTO_APPLY


def _names(job_id: str) -> dict:
    j = seen_jobs.get(job_id) or store.get(job_id) or {}
    return {"title": j.get("title") or "", "company": j.get("company") or ""}


def _apply_error(e: Exception, job_id: str) -> dict:
    out = {"error": str(e), "job_id": job_id, "site_says": getattr(e, "notice", None),
           "debug_page": getattr(e, "debug", None)}
    if getattr(e, "limit_reached", False):
        store.record_limit_hit(sites.name_of(job_id))
        out["limit_reached"] = True
        out["next"] = "Stop applying on this site for now and tell the user; slots free up as older applications expire."
    if new := getattr(e, "questions", None):
        # Indeed showed more questions on a later page: add them to the draft.
        known = {q["key"] for q in drafts.get(job_id, [])}
        drafts[job_id] = drafts.get(job_id, []) + [q for q in new if q["key"] not in known]
        checked.pop(job_id, None)
        out["new_questions"] = new
    return {k: v for k, v in out.items() if v is not None}


@tool()
async def check_login(site: str = "wellfound") -> dict:
    """Is the saved session logged in? site: wellfound | indeed | all."""
    names = list(sites.SITES) if site == "all" else [site]
    out = {}
    for name in names:
        try:
            ok = await sites.get(name).is_logged_in(browser)
        except (BrowserError, ValueError) as e:
            out[name] = {"logged_in": None, "error": str(e)}
            continue
        out[name] = {"logged_in": ok, "hint": None if ok else f"Run `uv run hireloop-login {name}` and sign in."}
    return out[names[0]] if len(names) == 1 else out


@tool()
def get_profile() -> dict:
    """The user's profile.md. Read once per session; answer only from it."""
    return profile.read()


@tool()
def save_answer(question: str, answer: str) -> dict:
    """Append an answer the user gave to profile.md. Only with their OK."""
    return profile.save_answer(question, answer)


@tool()
async def search_jobs(role: str, location: str | None = None, remote: bool = False, limit: int = 10,
                      summaries: bool = False, site: str = "wellfound") -> dict:
    """Search jobs on a site (wellfound | indeed). Hides skipped ones; marks drafted/held/submitted. summaries=true adds a one-line summary per job."""
    try:
        rows = await sites.get(site).search_jobs(browser, role, location, remote, min(limit, 25))
    except (BrowserError, ValueError) as e:
        return {"error": str(e)}
    out, hidden = [], 0
    for r in rows:
        seen_jobs[r["id"]] = r
        if not summaries:
            r = {k: v for k, v in r.items() if k != "summary"}
        if seen := store.get(r["id"]):
            if seen["status"] == "skipped":
                hidden += 1
                continue
            r["already"] = seen["status"]
        out.append(r)
    return {"jobs": out, "hidden_skipped": hidden}


def _remember(job_id: str, job: dict) -> None:
    job_cache[job_id] = job
    seen_jobs[job_id] = {**seen_jobs.get(job_id, {}), "title": job["title"], "company": job["company"],
                         "salary": job.get("salary"), "location": ", ".join(job.get("locations") or [])}


async def _wait_for_scheduled() -> dict | None:
    """Hold off while an earlier application is still in its undo window.
    Returns None when it's fine to send, or an error dict asking to retry later."""
    if not config.WAIT_FOR_SCHEDULED:
        return None
    # Only Wellfound has the undo window; the caller only asks for Wellfound jobs.
    waited = 0.0
    while True:
        left = await wellfound.scheduled_remaining(browser)
        if left <= config.SCHEDULED_SLACK_S:
            return None
        if waited >= config.SCHEDULED_MAX_WAIT_S:
            return {"submitted": False, "waiting": True, "retry_after_seconds": left,
                    "error": f"The previous application is still scheduled ({left}s left). Call again after that."}
        pause = min(left - config.SCHEDULED_SLACK_S + 2, 60, config.SCHEDULED_MAX_WAIT_S - waited)
        logging.info("Previous application still scheduled (%ss left); waiting %.0fs", left, pause)
        await asyncio.sleep(max(pause, 5))
        waited += max(pause, 5)


def _job_for_email(job_id: str) -> dict:
    site = sites.of(job_id)
    return {**seen_jobs.get(job_id, {}), **_names(job_id), "id": job_id, "url": site.job_url(job_id),
            "site": site.LABEL, "applied_url": site.APPLIED_URL}


@tool()
async def get_job(job_id: str, full: bool = False) -> dict:
    """Job details and fit (rules: <=3 yrs, pay floor, India or visa, applies on the site itself). full=true adds the description."""
    try:
        job = await sites.of(job_id).get_job(browser, job_id)
    except BrowserError as e:
        return {"error": str(e), "job_id": job_id}
    if "error" not in job:
        if job.get("applies_on_site") is None and (easy := seen_jobs.get(job_id, {}).get("easy_apply")) is not None:
            job["applies_on_site"] = easy          # the search card knew; the job page didn't say
        _remember(job_id, job)
        job["fit"] = factcheck.fit(job)          # uses the description, so check before trimming
        if not full:
            job = {k: v for k, v in job.items() if k != "description"}
    return job


@tool(description="Skip jobs so search hides them (reason recommended). confirm=false previews. undo=true un-skips.")
def skip_job(job_ids: list[str], reason: str = "", confirm: bool = False, undo: bool = False) -> dict:
    if undo:
        if not confirm:
            return {"preview": True, "would_unskip": [{"job_id": j, **_names(j)} for j in job_ids]}
        done = [j for j in job_ids if (s := store.get(j)) and s["status"] == "skipped"]
        for j in done:
            store.delete(j)
        return {"unskipped": done}

    applied = [j for j in job_ids if (s := store.get(j)) and s["status"] == "submitted"]
    todo = [j for j in job_ids if j not in applied]
    if not confirm:
        return {"preview": True, "reason": reason,
                "would_skip": [{"job_id": j, **_names(j)} for j in todo],
                "not_skippable_already_applied": applied or None,
                "next": "Show this list to the user. Call again with confirm=True only if they approve."}
    for j in todo:
        store.record(j, "skipped", notes=reason, **_names(j))
    return {"skipped": todo, "not_skippable_already_applied": applied or None}


@tool()
async def draft_application(job_id: str) -> dict:
    """Open the apply form: questions (answer by key), warnings, fit and the job description. Sends nothing."""
    if (seen := store.get(job_id)) and seen["status"] == "submitted":
        return {"error": "Already applied to this job.", "on": seen["updated_at"]}
    site = sites.of(job_id)
    try:
        form = await site.application_questions(browser, job_id)
    except (ApplyError, BrowserError) as e:
        return _apply_error(e, job_id)
    job = form.pop("job", None)
    note = form.pop("note", None) or f"{site.LABEL} sends the user's {site.LABEL} profile with the application; only these questions are filled here."
    if job:
        _remember(job_id, job)
    drafts[job_id] = form["questions"]
    checked.pop(job_id, None)
    store.record(job_id, "drafted", url=site.job_url(job_id), **_names(job_id))
    return {"job_id": job_id, **_names(job_id), **form,
            "fit": factcheck.fit(job) if job else None,
            "description": (job or {}).get("description", "")[:1000] or None,
            "note": note}


@tool()
def fact_check(job_id: str, answers: dict[str, str]) -> dict:
    """Check answers: every number, capitalised name, link and email must be in the profile or posting. Can't catch true facts paired wrongly; that's your review."""
    questions = drafts.get(job_id)
    if questions is None:
        return {"error": "Run draft_application for this job first."}
    prof = profile.read()
    if not prof.get("profile"):
        return {"error": "No profile.md to check against.", **prof}
    result = factcheck.check(answers, questions, prof["profile"], job_cache.get(job_id, {}))
    if result["passed"]:
        checked[job_id] = result["answers_hash"]
    else:
        checked.pop(job_id, None)
    return {"passed": result["passed"], "issues": result["issues"] or None}


@tool()
async def hold_application(job_id: str, answers: dict[str, str], reason: str) -> dict:
    """Don't send; log it as held and email the user the draft and reason."""
    questions = drafts.get(job_id, [])
    rows = [{"question": q["question"], "answer": answers.get(q["key"], "")} for q in questions]
    store.record(job_id, "held", notes=reason, url=sites.of(job_id).job_url(job_id), **_names(job_id))
    email = await asyncio.to_thread(notify.application_held, _job_for_email(job_id), rows, reason)
    return {"held": True, "job_id": job_id, **_names(job_id), "email_alert": email}


@tool(description=AUTO_SUBMIT_DOC if config.AUTO_APPLY else MANUAL_SUBMIT_DOC)
async def submit_application(job_id: str, answers: dict[str, str], confirm: bool = False) -> dict:
    if (seen := store.get(job_id)) and seen["status"] == "submitted":
        return {"error": "Already applied to this job.", "on": seen["updated_at"]}
    questions = drafts.get(job_id)
    if not confirm:
        if questions is None:
            return {"error": "Run draft_application for this job first, so the preview shows the real questions."}
        rows = [{"question": q["question"], "required": q["required"], "answer": answers.get(q["key"], "")}
                for q in questions]
        missing = [r["question"] for r in rows if r["required"] and not r["answer"].strip()]
        unknown = sorted(set(answers) - {q["key"] for q in questions})
        return {"preview": True, "job_id": job_id, **_names(job_id), "answers": rows,
                "missing_required": missing or None, "unknown_keys": unknown or None,
                "fact_checked": checked.get(job_id) == factcheck.answers_hash(answers),
                "today": store.daily_limit(sites.name_of(job_id)),
                "next": ("Auto mode: send only if fact_checked and fit.ok and you're confident; otherwise hold_application."
                         if _auto(job_id) else
                         "Show this to the user. Resubmit with confirm=True only if they approve.")}
    if config.AUTO_APPLY:
        # The server enforces the auto-mode rules (also for sites still on manual approval);
        # Claude's judgement is the second check.
        if checked.get(job_id) != factcheck.answers_hash(answers):
            return {"submitted": False, "error": "Auto mode: these exact answers haven't passed fact_check. "
                    "Run fact_check, or hold_application if it can't pass."}
        if not (fit := factcheck.fit(job_cache.get(job_id, {})))["ok"]:
            return {"submitted": False, "error": f"Auto mode: job doesn't fit the rules: {fit['reasons']}. Skip it."}
    site = sites.of(job_id)
    today = store.daily_limit(site.NAME)
    if today["submitted_today"] >= today["limit_today"]:
        return {"error": f"Today's limit of {today['limit_today']} {site.LABEL} applications is reached. "
                         f"It rises by 1 each day you apply without {site.LABEL} blocking you.", **today}
    try:
        if site is wellfound and (busy := await _wait_for_scheduled()):
            return {**busy, "job_id": job_id}
        await site.submit(browser, job_id, answers)
    except (ApplyError, BrowserError) as e:
        return {"submitted": False, **_apply_error(e, job_id)}
    store.record(job_id, "submitted", **_names(job_id))
    checked.pop(job_id, None)
    sent = [{"question": q["question"], "answer": answers.get(q["key"], "")} for q in drafts.pop(job_id, [])]
    email = await asyncio.to_thread(notify.application_sent, _job_for_email(job_id), sent)
    return {"submitted": True, "job_id": job_id, **_names(job_id), "email_alert": email}


@tool()
def list_applications(status: str | None = None, limit: int = 50, site: str | None = None) -> list[dict]:
    """Local log. status: submitted | held | drafted | skipped. site: wellfound | indeed."""
    return store.list_all(status, limit, site)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
