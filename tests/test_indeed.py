"""Offline tests for the Indeed parsers. The pages here are shaped like Indeed's
known markup (Nov 2026) but not yet copied from the live site; see sites/indeed.py."""

import json

from hireloop import factcheck, sites
from hireloop.sites import indeed as i

CARDS = {"metaData": {"mosaicProviderJobCardsModel": {"results": [
    {"jobkey": "0123456789abcdef", "displayTitle": "Machine Learning Engineer", "company": "Acme AI",
     "formattedLocation": "Bengaluru, Karnataka", "salarySnippet": {"text": "₹6,00,000 - ₹9,00,000 a year"},
     "indeedApplyEnabled": True, "snippet": "<ul><li>Build and ship ranking models for our search product.</li></ul>"},
    {"jobkey": "fedcba9876543210", "title": "Data Analyst", "company": "Beta", "formattedLocation": "Remote",
     "remoteLocation": True, "extractedSalary": {"min": 30000, "max": 45000, "type": "monthly"},
     "indeedApplyEnabled": False},
    {"title": "no key, ignored"},
]}}}
SEARCH = ('<html><script>window.mosaic.providerData["mosaic-provider-jobcards"]='
          + json.dumps(CARDS) + ';window.mosaic.providerData["other"]={};</script></html>')


def test_parse_search_json():
    rows = i.parse_search(SEARCH)
    assert [r["id"] for r in rows] == ["indeed:0123456789abcdef", "indeed:fedcba9876543210"]
    a, b = rows
    assert a["title"] == "Machine Learning Engineer" and a["company"] == "Acme AI"
    assert a["salary"] == "₹6,00,000 - ₹9,00,000 a year" and a["easy_apply"] is True
    assert a["summary"].startswith("Build and ship ranking models")
    assert b["remote"] is True and b["easy_apply"] is False
    assert b["salary"] == "₹30,000 - ₹45,000 a month"


def test_parse_search_dom_fallback():
    page = ('<div><a data-jk="00000000000000aa"><span title="AI Engineer">AI Engineer</span></a>'
            '<span data-testid="company-name">Gamma</span><div data-testid="text-location">Pune</div>'
            '<span>Easily apply</span></div>')
    [r] = i.parse_search(page)
    assert r == {"id": "indeed:00000000000000aa", "title": "AI Engineer", "company": "Gamma",
                 "location": "Pune", "remote": None, "easy_apply": True}


def test_ids_and_urls():
    assert sites.of("indeed:0123456789abcdef") is i
    assert sites.of("4417481-software-engineer") is sites.wellfound
    assert i.job_url("indeed:0123456789abcdef") == f"{i.BASE_URL}/viewjob?jk=0123456789abcdef"
    url = i.search_url("ML Engineer", "Bengaluru", True, 10)
    assert "q=ML+Engineer" in url and "l=Bengaluru" in url and "start=10" in url and "DSQF7" in url


JOB_LD = {"@type": "JobPosting", "title": "Machine Learning Engineer",
          "hiringOrganization": {"@type": "Organization", "name": "Acme AI"},
          "jobLocation": {"@type": "Place", "address": {"addressLocality": "Bengaluru", "addressCountry": {"name": "IN"}}},
          "employmentType": "FULL_TIME", "datePosted": "2026-11-01T10:00:00Z",
          "baseSalary": {"currency": "INR", "value": {"minValue": 600000, "maxValue": 900000, "unitText": "YEAR"}},
          "description": "<p>We need 0-2 years of Python.</p><ul><li>PyTorch</li></ul>"}


def test_parse_job_json_ld():
    page = f'<script type="application/ld+json">{json.dumps(JOB_LD)}</script><button id="indeedApplyButton">Apply now</button>'
    j = i.parse_job(page, "indeed:0123456789abcdef")
    assert j["title"] == "Machine Learning Engineer" and j["company"] == "Acme AI"
    assert j["locations"] == ["Bengaluru, IN"] and j["posted"] == "2026-11-01"
    assert j["salary"] == "₹600,000 - ₹900,000 a year"
    assert j["applies_on_site"] is True and "• PyTorch" in j["description"]
    assert factcheck.fit(j)["ok"], factcheck.fit(j)


def test_parse_job_initial_data_fallback():
    init = {"jobInfoWrapperModel": {"jobInfoModel": {
        "jobInfoHeaderModel": {"jobTitle": "Senior Data Engineer", "companyName": "Beta",
                               "formattedLocation": "Hyderabad, Telangana"},
        "sanitizedJobDescription": "<p>5+ years building pipelines.</p>"}},
        "salaryInfoModel": {"salaryText": "₹25,000 - ₹30,000 a month"}}
    page = f"<script>window._initialData={json.dumps(init)};</script><a>Apply on company site</a>"
    j = i.parse_job(page, "indeed:fedcba9876543210")
    assert j["title"] == "Senior Data Engineer" and j["company"] == "Beta"
    assert j["locations"] == ["Hyderabad, Telangana"] and j["salary"] == "₹25,000 - ₹30,000 a month"
    assert j["applies_on_site"] is False
    reasons = factcheck.fit(j)["reasons"]
    assert any("5+ years" in r for r in reasons) and "Applies on an external site" in reasons


def test_parse_job_dom_description_and_missing():
    page = '<h1>AI Intern</h1><div id="jobDescriptionText"><p>Learn &amp; build.</p></div><div id="footer">x</div>'
    j = i.parse_job(page, "indeed:00000000000000aa")
    assert j["title"] == "AI Intern" and j["description"] == "Learn & build." and j["applies_on_site"] is None
    assert "error" in i.parse_job("<html></html>", "indeed:00000000000000aa")


def test_logged_in_url():
    assert i.parse_logged_in_url("https://profile.indeed.com/") is True
    assert i.parse_logged_in_url("https://secure.indeed.com/auth?continue=x") is False


def test_new_questions_error_carries_questions():
    e = i.NewQuestions([{"key": "q1", "question": "Notice period?", "type": "text", "required": True}])
    assert isinstance(e, sites.ApplyError) and e.questions[0]["key"] == "q1" and not e.limit_reached


def _root_props(applied=False, ia=True):
    """Trimmed from a real job page (Nov 2026): data lives in window._rootProps.preloadedVJData."""
    vj = {"jobTitle": "Associate AI Engineer", "jobKey": "dfc958b0d614ba18",
          "jobInfoWrapperModel": {"jobInfoModel": {
              "appliedStateBannerModel": None,
              "jobInfoHeaderModel": {"companyName": "Digital Green", "formattedLocation": "Bengaluru, Karnataka",
                                     "jobTitle": "Associate AI Engineer", "remoteLocation": False,
                                     "salaryMin": 40000, "salaryMax": 60000, "salaryType": "MONTHLY"},
              "jobDescriptionSectionModel": {"jobDetailsSection": {"jobTypes": [{"label": "Full-time"}]}},
              "sanitizedJobDescription": "<p><b> Experience : 1-4 years (relevant)</b></p><p>Build LLM features.</p>"}},
          "hiringInsightsModel": {"age": "4 days ago"},
          "indeedApplyButtonContainer": {"disabled": False, "indeedApplyUrl": "https://smartapply.indeed.com/x"} if ia else None,
          "saveJobButtonContainerModel": {"currentJobState": "APPLIED" if applied else "VISITED"}}
    root = {"url": "https://in.indeed.com/viewjob?jk=dfc958b0d614ba18", "preloadedVJData": vj}
    # _initialData is a JS object literal on the real page, not JSON: the parser must not depend on it.
    return ("<script>window._initialData = {\n ssr: true,\n viewJobClientSideModel: window._rootProps.preloadedVJData};"
            f"window._rootProps = {json.dumps(root)};</script>"
            '<a data-testid="viewjob-indeed-apply" href="https://smartapply.indeed.com/x">Apply now</a>')


def test_parse_job_root_props():
    j = i.parse_job(_root_props(), "indeed:dfc958b0d614ba18")
    assert j["title"] == "Associate AI Engineer" and j["company"] == "Digital Green"
    assert j["locations"] == ["Bengaluru, Karnataka"] and j["type"] == "Full-time"
    assert j["salary"] == "₹40,000 - ₹60,000 a month"
    assert j["posted"] == "4 days ago" and j["applies_on_site"] is True and j["already_applied"] is None
    assert factcheck.fit(j)["ok"], factcheck.fit(j)


def test_root_props_applied_and_company_site():
    j = i.parse_job(_root_props(applied=True), "indeed:dfc958b0d614ba18")
    assert j["already_applied"] is True and "Already applied" in factcheck.fit(j)["reasons"]
    assert i.parse_job(_root_props(ia=False), "indeed:dfc958b0d614ba18")["applies_on_site"] is False


# Shaped like window._initialData.screenerQuestions on a real Indeed Apply page (Nov 2026),
# as SCREENER_JS returns it. The page rendered only some of these at once.
SCREENER = [
    {"name": "q_addr", "label": "Address", "type": "TEXT", "format": "NONE", "required": False,
     "prefilled": True, "conditional": False, "options": [], "value": "7th Cross"},
    {"name": "q_py", "label": "Are you comfortable writing Python code independently?*", "type": "TEXTAREA",
     "format": "NONE", "required": True, "prefilled": True, "conditional": False, "options": [],
     "value": "Languages: Python, Rust"},
    {"name": "q_emb", "label": "Do you understand embeddings?*", "type": "MULTISELECT", "format": "NONE",
     "required": True, "prefilled": True, "conditional": False, "options": ["Yes", "No"], "value": ["Yes"]},
    {"name": "q_ctc", "label": "What is your current CTC( in LPA)?*", "type": "TEXT", "format": "INTEGER",
     "required": True, "prefilled": False, "conditional": False, "options": [], "value": ""},
    {"name": "q_if", "label": "If yes, which city?", "type": "TEXT", "format": "NONE",
     "required": True, "prefilled": False, "conditional": True, "options": [], "value": ""},
]


def test_parse_screener():
    qs = {q["key"]: q for q in i.parse_screener(SCREENER)}
    assert qs["q_ctc"]["question"] == "What is your current CTC( in LPA)?" and qs["q_ctc"]["number"]
    assert qs["q_emb"]["type"] == "checkbox" and qs["q_emb"]["options"] == ["Yes", "No"] and qs["q_emb"]["value"] == "Yes"
    assert qs["q_if"]["required"] is False and qs["q_if"]["conditional"]     # only asked after some answers
    assert i._public(qs["q_py"])["indeed_prefill"] == "Languages: Python, Rust"


def test_prefilled_required_questions_still_need_answers():
    qs = i.parse_screener(SCREENER)
    # Indeed's own prefill doesn't count: required questions must come from the profile.
    assert [q["key"] for q in i.unanswered(qs, {})] == ["q_py", "q_emb", "q_ctc"]
    assert i.unanswered(qs, {"q_py": "Yes, daily.", "q_emb": "Yes", "q_ctc": "0"}) == []
    e = i.NewQuestions([i._public(q) for q in i.unanswered(qs, {"q_py": "Yes"})])
    assert [q["key"] for q in e.questions] == ["q_emb", "q_ctc"]
