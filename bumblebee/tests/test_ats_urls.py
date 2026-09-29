import pytest

from bumblebee.ats import JobRef, extract_company_refs, extract_job_ref
from bumblebee.companies import parse_company_spec


@pytest.mark.parametrize("url,ats,slug,job_id", [
    ("https://boards.greenhouse.io/stripe/jobs/6789012", "greenhouse", "stripe", "6789012"),
    ("https://job-boards.greenhouse.io/twitch/jobs/8751076002?gh_src=abc", "greenhouse", "twitch", "8751076002"),
    ("https://job-boards.greenhouse.io/Figma", "greenhouse", "figma", None),
    ("https://boards.greenhouse.io/embed/job_app?for=airbnb&token=12345", "greenhouse", "airbnb", "12345"),
    ("https://boards-api.greenhouse.io/v1/boards/discord/jobs", "greenhouse", "discord", None),
    ("https://boards-api.greenhouse.io/v1/boards/discord/jobs/42", "greenhouse", "discord", "42"),
    ("https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd", "lever", "palantir",
     "6ed76ce8-4156-4b60-b120-403538bd66cd"),
    ("https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd/apply?lever-source=x", "lever",
     "palantir", "6ed76ce8-4156-4b60-b120-403538bd66cd"),
    ("https://jobs.lever.co/some-co", "lever", "some-co", None),
    ("https://api.lever.co/v0/postings/zoox?mode=json", "lever", "zoox", None),
    ("https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245", "ashby", "ramp",
     "34413f8d-26bf-4bbc-8ade-eb309a0e2245"),
    ("https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245/application?utm_source=Simplify",
     "ashby", "ramp", "34413f8d-26bf-4bbc-8ade-eb309a0e2245"),
    ("https://jobs.ashbyhq.com/openai", "ashby", "openai", None),
    ("https://api.ashbyhq.com/posting-api/job-board/notion", "ashby", "notion", None),
])
def test_extract(url, ats, slug, job_id):
    ref = extract_job_ref(url)
    assert ref == JobRef(ats, slug, job_id), url


def test_company_hosted_ids_without_slug():
    assert extract_job_ref("https://stripe.com/jobs/search?gh_jid=8172508") == JobRef("greenhouse", None, "8172508")
    ref = extract_job_ref("https://acme.com/careers?ashby_jid=34413f8d-26bf-4bbc-8ade-eb309a0e2245")
    assert ref == JobRef("ashby", None, "34413f8d-26bf-4bbc-8ade-eb309a0e2245")


@pytest.mark.parametrize("url", [
    "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/SWE_JR1",
    "https://jobs.smartrecruiters.com/Visa/74400",
    "https://boards.greenhouse.io/",
    "https://boards.greenhouse.io/embed/job_app",
    "not a url",
    "",
    None,
])
def test_non_ats(url):
    ref = extract_job_ref(url)
    assert ref is None or ref.slug is None


def test_extract_from_markdown():
    md = """
| Company | Role | Link |
| **[Twitch](https://www.twitch.tv)** | SWE | <a href="https://job-boards.greenhouse.io/twitch/jobs/1"><img></a> |
| Ramp | SWE | [Apply](https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245). |
| Zoox | SWE | https://jobs.lever.co/zoox/6ed76ce8-4156-4b60-b120-403538bd66cd, |
| Nvidia | SWE | https://nvidia.wd5.myworkdayjobs.com/x |
"""
    assert extract_company_refs(md) == {JobRef("greenhouse", "twitch"), JobRef("ashby", "ramp"),
                                        JobRef("lever", "zoox")}


@pytest.mark.parametrize("spec,expected", [
    ("greenhouse:stripe", ("greenhouse", "stripe")),
    ("Lever/Palantir", ("lever", "palantir")),
    ("https://jobs.ashbyhq.com/ramp", ("ashby", "ramp")),
])
def test_parse_company_spec(spec, expected):
    assert parse_company_spec(spec) == expected


@pytest.mark.parametrize("spec", ["workday:nvidia", "stripe", "https://example.com/jobs"])
def test_parse_company_spec_errors(spec):
    with pytest.raises(ValueError):
        parse_company_spec(spec)
