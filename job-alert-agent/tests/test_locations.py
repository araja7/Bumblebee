import pytest

from job_alert.filters import LocationMatcher
from tests.conftest import make_job


@pytest.fixture
def lm(cfg):
    return LocationMatcher(cfg["locations"], allow_remote=False)


@pytest.mark.parametrize("loc,metro", [
    # Seattle
    ("Seattle, WA", "seattle"), ("Seattle", "seattle"), ("Seattle, Washington, United States", "seattle"),
    ("South Lake Union, Seattle", "seattle"),
    # San Francisco
    ("San Francisco, CA", "san_francisco"), ("SF", "san_francisco"), ("San Francisco", "san_francisco"),
    ("SF Bay Area", "san_francisco"), ("San Francisco Bay Area", "san_francisco"),
    ("US-CA-San Francisco", "san_francisco"), ("San Francisco, California, USA", "san_francisco"),
    # New York
    ("New York, NY", "new_york"), ("NYC", "new_york"), ("New York City", "new_york"), ("New York", "new_york"),
    ("Brooklyn, NY", "new_york"), ("Manhattan", "new_york"), ("Queens, New York", "new_york"),
    ("New York, New York, United States", "new_york"), ("NY, NY", "new_york"), ("US-NY-New York", "new_york"),
    ("Long Island City, NY", "new_york"), ("The Bronx", "new_york"), ("New York, NY (HQ)", "new_york"),
    # Boston
    ("Boston, MA", "boston"), ("Boston", "boston"), ("Boston, Massachusetts", "boston"),
    # Chicago
    ("Chicago, IL", "chicago"), ("Chicago", "chicago"), ("Chicago, Illinois, United States", "chicago"),
])
def test_metro_variants(lm, loc, metro):
    job = make_job(location=loc, locations=[loc])
    assert lm.check(job) is None, (loc, job.matched_metros)
    assert metro in job.matched_metros


@pytest.mark.parametrize("loc", [
    "Albany, New York",           # New York in state position
    "Rochester, New York, USA",
    "South San Francisco, CA",    # separate city
    "Manhattan Beach, CA",
    "Manhattan, KS",
    "Austin, TX",
    "London, UK",
    "Toronto, Canada",
    "Bellevue, WA",               # not Seattle unless you opt in via config
    "Newark, NJ",
    "United States",
])
def test_non_matching(lm, loc):
    job = make_job(location=loc, locations=[loc])
    assert lm.check(job) is not None, (loc, job.matched_metros)


def test_multi_location_any_qualifies(lm):
    job = make_job(location="Austin, TX; Chicago, IL; Denver, CO", locations=[])
    assert lm.check(job) is None
    assert job.matched_metros == ["chicago"]


def test_multi_location_list(lm):
    job = make_job(location="", locations=["London", "Toronto", "Seattle, WA", "San Francisco, CA"])
    assert lm.check(job) is None
    assert set(job.matched_metros) == {"seattle", "san_francisco"}


def test_multi_location_comma_separated(lm):
    job = make_job(location="San Francisco, CA, New York, NY", locations=[])
    assert lm.check(job) is None
    assert set(job.matched_metros) == {"san_francisco", "new_york"}


def test_slash_and_or_separators(lm):
    for loc in ("SF / NYC", "Seattle or Remote", "Boston | Chicago"):
        assert lm.check(make_job(location=loc, locations=[])) is None, loc


@pytest.mark.parametrize("loc", ["Remote", "Remote - US", "Remote (United States)", "US Remote", "Anywhere"])
def test_remote_only_excluded(lm, loc):
    job = make_job(location=loc, locations=[loc])
    reason = lm.check(job)
    assert reason == "remote-only location"


def test_remote_city_excluded(lm):
    # A remote role "based in" a city is still remote
    for loc in ("Remote - New York", "San Francisco (Remote)"):
        assert lm.check(make_job(location=loc, locations=[loc])) is not None, loc


def test_workplace_type_remote_excluded(lm):
    job = make_job(location="New York, NY", locations=["New York, NY"], workplace_type="remote")
    assert lm.check(job) == "fully remote role"


def test_hybrid_qualifies(lm):
    job = make_job(location="Hybrid - Seattle, WA", locations=["Hybrid - Seattle, WA"], workplace_type="hybrid")
    assert lm.check(job) is None


def test_city_plus_remote_option_qualifies(lm):
    # On-site/hybrid in NYC is offered, so it matches even though Remote is also listed
    job = make_job(location="", locations=["New York, NY (HQ)", "Remote (US)"], workplace_type="hybrid")
    assert lm.check(job) is None
    assert job.matched_metros == ["new_york"]


def test_short_label(lm):
    assert lm.short_label(["new_york", "san_francisco"]) == "NYC/SF"


def test_allow_remote_config(cfg):
    lm = LocationMatcher(cfg["locations"], allow_remote=True)
    assert lm.check(make_job(location="Remote - New York", locations=["Remote - New York"])) is None
