"""The job sites hireloop can apply on. Each module has the same interface:

  NAME, LABEL, BASE_URL, LOGIN_URL, CHECK_URL, LOGIN_PAGES, APPLIED_URL
  owns(job_id), job_url(job_id)
  is_logged_in(browser), logged_in_on(tab)
  search_jobs(browser, role, location, remote, limit)
  get_job(browser, job_id)
  application_questions(browser, job_id) -> {"questions", "warnings", "job", ...}
  submit(browser, job_id, answers)        raises common.ApplyError on failure

Job ids tell the sites apart: Wellfound's are '<number>-<slug>', Indeed's 'indeed:<jk>'.
"""

from types import ModuleType

from . import indeed, wellfound
from .common import ApplyError

SITES: dict[str, ModuleType] = {"wellfound": wellfound, "indeed": indeed}


def get(name: str) -> ModuleType:
    try:
        return SITES[name.lower()]
    except KeyError:
        raise ValueError(f"Unknown site {name!r}. Use one of: {', '.join(SITES)}.") from None


def of(job_id: str) -> ModuleType:
    """The site a job id belongs to (Wellfound for old, unprefixed ids)."""
    return indeed if indeed.owns(job_id) else wellfound


def name_of(job_id: str) -> str:
    return of(job_id).NAME


__all__ = ["SITES", "ApplyError", "get", "of", "name_of", "indeed", "wellfound"]
