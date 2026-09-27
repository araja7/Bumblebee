"""Adapters parse realistic (trimmed) API payloads correctly."""
from job_alert.sources import ADAPTERS
from job_alert.sources.base import clean_url
from job_alert.sources.simplify import SimplifySource
from tests.conftest import FakeHttp

GH_LIST = {"jobs": [{
    "absolute_url": "https://stripe.com/jobs/search?gh_jid=8172508", "id": 8172508,
    "location": {"name": "New York, NY"}, "title": "Software Engineer, New Grad ",
    "company_name": "Stripe", "first_published": "2026-09-26T13:32:53-04:00",
    "updated_at": "2026-09-26T16:45:00-04:00"}]}
LEVER = [{
    "id": "6ed76ce8-4156-4b60-b120-403538bd66cd", "text": "Software Engineer, New Grad",
    "categories": {"location": "New York, NY", "allLocations": ["New York, NY", "Seattle, WA"]},
    "createdAt": 1790000000000, "workplaceType": "on-site", "country": "US",
    "hostedUrl": "https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd",
    "openingPlain": "Palantir builds software.", "descriptionPlain": "The role",
    "lists": [{"text": "Requirements", "content": "<li>3+ years of experience</li>"}],
    "additionalPlain": ""}]
ASHBY = {"jobs": [{
    "id": "34413f8d-26bf-4bbc-8ade-eb309a0e2245", "title": " Software Engineer", "location": "New York, NY (HQ)",
    "secondaryLocations": [{"location": "Remote (US)"}], "publishedAt": "2026-09-26T17:12:35.753+00:00",
    "isListed": True, "workplaceType": "Hybrid",
    "address": {"postalAddress": {"addressCountry": "USA"}},
    "jobUrl": "https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245",
    "descriptionPlain": "About Ramp"}, {"id": "hidden", "title": "x", "isListed": False}]}


def test_greenhouse():
    http = FakeHttp({"https://boards-api.greenhouse.io/v1/boards/stripe/jobs": GH_LIST})
    res = ADAPTERS["greenhouse"].fetch_board(http, "stripe")
    j = res.jobs[0]
    assert res.company_name == "Stripe"
    assert (j.id, j.company, j.title, j.location) == ("8172508", "Stripe", "Software Engineer, New Grad", "New York, NY")
    assert j.posted_at.isoformat() == "2026-09-26T13:32:53-04:00"
    assert j.canonical_key == "greenhouse:8172508"


def test_greenhouse_description_and_offices():
    http = FakeHttp({"https://boards-api.greenhouse.io/v1/boards/stripe/jobs/8172508":
                     {"content": "&lt;p&gt;2+ years&lt;/p&gt;", "offices": [{"name": "Seattle"}]}})
    j = ADAPTERS["greenhouse"]._to_job(GH_LIST["jobs"][0], "stripe", "Stripe")
    text = ADAPTERS["greenhouse"].fetch_description(http, j)
    assert "2+ years" in text and "<p>" not in text
    assert "Seattle" in j.locations


def test_lever():
    http = FakeHttp({"https://api.lever.co/v0/postings/palantir?mode=json": LEVER})
    j = ADAPTERS["lever"].fetch_board(http, "palantir", "Palantir").jobs[0]
    assert j.company == "Palantir" and j.workplace_type == "onsite"
    assert j.locations == ["New York, NY", "Seattle, WA"]
    assert "3+ years of experience" in j.description
    assert j.posted_at.year == 2026


def test_ashby():
    http = FakeHttp({"https://api.ashbyhq.com/posting-api/job-board/ramp": ASHBY})
    res = ADAPTERS["ashby"].fetch_board(http, "ramp", "Ramp")
    assert len(res.jobs) == 1  # unlisted job dropped
    j = res.jobs[0]
    assert j.title == "Software Engineer" and j.workplace_type == "hybrid"
    assert j.locations == ["New York, NY (HQ)", "Remote (US)"]


def test_simplify_feed():
    url = "https://example.test/listings.json"
    items = [
        {"id": "a", "company_name": "Acme", "title": "Software Engineer", "active": True, "is_visible": True,
         "date_posted": 1790000000, "locations": ["NYC", "SF"],
         "url": "https://job-boards.greenhouse.io/acme/jobs/77?utm_source=Simplify&ref=Simplify"},
        {"id": "b", "company_name": "Closed", "title": "SWE", "active": False, "is_visible": True},
        {"id": "c", "company_name": "Hidden", "title": "SWE", "active": True, "is_visible": False},
    ]
    jobs = SimplifySource(url).fetch(FakeHttp({url: items}))
    assert [j.id for j in jobs] == ["a"]
    j = jobs[0]
    assert j.url == "https://job-boards.greenhouse.io/acme/jobs/77"
    assert (j.ats, j.company_slug) == ("greenhouse", "acme")
    assert j.canonical_key == "greenhouse:77"


def test_simplify_description_via_ats():
    from job_alert.models import Job
    j = Job(id="a", company="Acme", title="SWE", location="NYC", url="https://job-boards.greenhouse.io/acme/jobs/77",
            posted_at=None, source="simplify")
    http = FakeHttp({"https://boards-api.greenhouse.io/v1/boards/acme/jobs/77": {"content": "4+ years of experience"}})
    assert "4+ years" in SimplifySource().fetch_description(http, j)
    j.url = "https://acme.wd1.myworkdayjobs.com/x"
    assert SimplifySource().fetch_description(http, j) is None


def test_clean_url_keeps_ids():
    assert clean_url("https://stripe.com/jobs/search?gh_jid=1&utm_source=x&ref=Simplify") == \
        "https://stripe.com/jobs/search?gh_jid=1"


def test_clean_url_shortens_ats_links():
    assert clean_url("https://jobs.ashbyhq.com/qumulo/e1cebc33-3bfc-4c86-9581-4d558cd5f8cc/application?embed=true") == \
        "https://jobs.ashbyhq.com/qumulo/e1cebc33-3bfc-4c86-9581-4d558cd5f8cc"
    assert clean_url("https://jobs.lever.co/acme/6ed76ce8-4156-4b60-b120-403538bd66cd/apply") == \
        "https://jobs.lever.co/acme/6ed76ce8-4156-4b60-b120-403538bd66cd"
    assert clean_url("https://boards.greenhouse.io/anduril/jobs/5248751007?gh_jid=5248751007") == \
        "https://boards.greenhouse.io/anduril/jobs/5248751007"
