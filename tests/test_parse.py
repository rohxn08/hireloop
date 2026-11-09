"""Offline tests for the page parsers, using tiny pages shaped like Wellfound's
(structure copied from the live site, Nov 2026)."""

import json

from hireloop.sites import wellfound as w


def search_page(viewer_user=None) -> str:
    apollo = {
        "ROOT_QUERY": {"talent": {
            "viewer": {"currentUser": viewer_user},
            'seoLandingPageJobSearchResults({"page":1,"role":"x"})': {
                "totalJobCount": 2, "startups": [{"__ref": "StartupResult:1"}],
            },
        }},
        "StartupResult:1": {"name": "Acme", "highlightedJobListings": [
            {"__ref": "JobListingSearchResult:10"}, {"__ref": "JobListingSearchResult:11"}]},
        "JobListingSearchResult:10": {
            "id": "10", "slug": "backend-engineer", "title": "Backend Engineer",
            "locationNames": ["Bengaluru", "Bengaluru"], "remote": False,
            "compensation": "$100k – $120k &amp; more", "yearsExperienceMin": 3, "yearsExperienceMax": None,
            "description": "## **About Us**\n\nWe build delightful developer tools for small teams everywhere.",
        },
        "JobListingSearchResult:11": {"id": "11", "slug": "sre", "title": "SRE", "yearsExperienceMin": 2, "yearsExperienceMax": 5},
    }
    nd = {"props": {"pageProps": {"apolloState": {"data": apollo}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></html>'


JOB_PAGE = """<html><script type="application/ld+json">{ld}</script>
<h1 class="x">Backend Engineer</h1><ul class="y"><li class="z">$150k – $180k • 0.5% – 1.0%</li><li>Remote</li></ul>
<div><span class="text-md font-semibold">Visa Sponsorship</span><p><span>Not Available</span></p></div>
<div><span class="text-md font-semibold">Skills</span><div class="flex flex-wrap">
<div class="mr-2 rounded-3xl border">Python</div><div class="mr-2 rounded-3xl border">Go &amp; gRPC</div></div></div></div>
<h2>About the job</h2></html>""".format(ld=json.dumps({
    "@type": "JobPosting", "title": "Backend Engineer", "hiringOrganization": {"name": "Acme"},
    "employmentType": "FULL_TIME", "jobLocationType": "TELECOMMUTE", "datePosted": "2026-07-01T15:49:14Z",
    "jobLocation": [{"address": {"addressLocality": "Berlin", "addressCountry": "Germany"}}],
    "experienceRequirements": {"monthsOfExperience": 36}, "directApply": True,
    "description": "<p>About us</p><ul><li>Fast &amp; small</li></ul>" + "<p>" + "word " * 500 + "</p>",
}))


def test_parse_search():
    rows, total = w.parse_search(search_page())
    assert total == 2
    assert [r["id"] for r in rows] == ["10-backend-engineer", "11-sre"]
    r = rows[0]
    assert r["company"] == "Acme" and r["location"] == "Bengaluru"
    assert r["salary"] == "$100k – $120k & more"
    assert r["years"] == "3+" and rows[1]["years"] == "2-5"
    assert r["summary"].startswith("We build delightful")


def test_logged_in():
    assert w.parse_logged_in(search_page()) is False
    assert w.parse_logged_in(search_page({"id": "1"})) is True
    assert w.parse_logged_in("<html></html>") is None


def test_logged_in_viewer_ref():
    apollo = {"ROOT_QUERY": {"talent": {"viewer": {"__ref": "TalentViewer:7"}}},
              "TalentViewer:7": {"id": "7"}}
    nd = {"props": {"pageProps": {"apolloState": {"data": apollo}}}}
    page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
    assert w.parse_logged_in(page) is True


def test_search_url():
    assert w.search_url("Software Engineer", None, True) == "https://wellfound.com/role/r/software-engineer"
    assert w.search_url("ML Engineer", "New York", False, 2) == "https://wellfound.com/role/l/ml-engineer/new-york?page=2"
    assert w.search_url("C++ Dev", None, False) == "https://wellfound.com/role/c-dev"


def test_parse_job():
    j = w.parse_job(JOB_PAGE, "10-backend-engineer")
    assert j["company"] == "Acme" and j["remote"] is True
    assert j["salary"] == "$150k – $180k • 0.5% – 1.0%"
    assert j["stack"] == ["Python", "Go & gRPC"]
    assert j["visa"] == "Not Available" and j["years"] == 3.0
    assert j["locations"] == ["Berlin, Germany"] and j["posted"] == "2026-07-01"
    assert "• Fast & small" in j["description"]
    assert len(j["description"]) <= w.DESCRIPTION_CHARS


def test_parse_job_missing():
    assert "error" in w.parse_job("<html></html>", "x")


APPLY_FORM = """<div class="ReactModal__Content" role="dialog"><form class="">
<div class="fieldError"><div class="font-bold">Improve your odds</div><span>It seems you’re outside the years of experience preferred for this role (5+ years)</span><div>Indicate how...</div></div>
<div class="mb-2"><label for="form-input--undefined"><div><div>What interests you about working for this company?</div></div>
<div><textarea id="a" name="customQuestionAnswers[332035][answer]" rows="4"></textarea></div></label></div>
<div class="mb-2"><label for="form-input--undefined"><div><div>Experience in Generative AI (out of 5)<span>*</span></div></div>
<div><input type="text" id="b" name="customQuestionAnswers[150118][answer]" value=""></div></label></div>
<button data-test="Button" type="button">Cancel</button><button data-test="JobApplicationModal--SubmitButton" type="submit">Send application</button>
</form></div>"""


def test_parse_application():
    form = w.parse_application(APPLY_FORM)
    assert form["warnings"].startswith("It seems you’re outside")
    q1, q2 = form["questions"]
    assert q1 == {"key": "332035", "question": "What interests you about working for this company?",
                  "type": "textarea", "required": False, "options": None}
    assert q2["key"] == "150118" and q2["type"] == "text" and q2["required"] is True
    assert q2["question"] == "Experience in Generative AI (out of 5)"
    assert w.parse_application("<html>no modal</html>") is None


def test_parse_job_logged_in():
    apollo = {
        "ROOT_QUERY": {'jobListing({"id":"5","slug":"ai"})': {"__ref": "JobListing:5"}},
        "JobListing:5": {
            "title": "AI Engineer", "startup": {"__ref": "Startup:1"}, "compensation": "₹10L – ₹15L",
            "jobType": "full_time", "remote": False, "remoteConfig": {"__ref": "RC:1"},
            "locationNames": ["Bengaluru"], "yearsExperienceMin": 3, "visaSponsorship": False,
            "allowRelocation": True, "skills": [{"__ref": "NewTag:1"}], "liveStartAt": 1787638107,
            "source": "PLATFORM", "currentUserQualificationReport": {"errors": []},
            "description": "​**About Us**\nWe build things.",
        },
        "Startup:1": {"name": "Acme "}, "RC:1": {"kind": "ONSITE"}, "NewTag:1": {"displayName": "Python"},
    }
    nd = {"props": {"pageProps": {"apolloState": {"data": apollo}}}}
    j = w.parse_job(f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>', "5-ai")
    assert j["company"] == "Acme" and j["salary"] == "₹10L – ₹15L"
    assert j["remote_policy"] == "In office" and j["years"] == "3+"
    assert j["visa"] == "Not available" and j["relocation"] == "Allowed"
    assert j["stack"] == ["Python"] and j["applies_on_site"] is True
    assert j["eligibility_issues"] is None
    assert j["description"].startswith("About Us")


def test_typing_chunks_keep_text_exact():
    from hireloop.browser import _chunks
    text = "Hi  there,\nI build voice agents and RAG systems.   Happy to chat!\n\n- Rohan"
    for _ in range(20):
        parts = _chunks(text)
        assert "".join(parts) == text and all(parts)


def test_apply_error_detects_limit():
    e = w.ApplyError("form didn't open", "You have reached the maximum number of active applications")
    assert e.limit_reached and "limit on active applications" in str(e)
    e2 = w.ApplyError("form didn't open", "Something else went wrong")
    assert not e2.limit_reached and str(e2) == "form didn't open" and e2.notice


def test_parse_scheduled():
    page = "Applications\nOngoing\nArchived\nNewton School\nDATA ANALYST\nScheduled\nUndo · 1:48\nAcme\nAI Engineer\nPending\n1 minute ago"
    assert w.parse_scheduled(page) == [108]
    assert w.parse_scheduled("Acme\nPending\n2 minutes ago") == []
    assert w.parse_scheduled("A\nScheduled\nUndo · 4:38\nB\nScheduled\nUndo · 0:09") == [278, 9]
    assert w.parse_scheduled("A\nScheduled\n") == [30]          # timer not readable: short wait
