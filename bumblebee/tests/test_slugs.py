import pytest

from bumblebee.discovery.slug_probe import generate_slugs, name_plausible, probe_names
from bumblebee.http import HttpError
from bumblebee.sources import ADAPTERS, BoardResult
from tests.conftest import FakeHttp

GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}"
LEVER = "https://api.lever.co/v0/postings/{}?mode=json"


@pytest.mark.parametrize("name,expected_first,must_contain", [
    ("Stripe", "stripe", []),
    ("Scale AI", "scaleai", ["scale-ai", "scale"]),
    ("Scale AI, Inc.", "scaleai", ["scale"]),
    ("Two Sigma", "twosigma", ["two-sigma"]),
    ("Hudson River Trading", "hudsonrivertrading", ["hudson-river-trading"]),
    ("Macy's", "macys", []),
    ("Nestlé", "nestle", []),
    ("Johnson & Johnson", "johnsonandjohnson", ["johnsonjohnson"]),
    ("Monday.com", "mondaycom", ["monday"]),
    ("Block (Square)", "block", []),
    ("dbt Labs", "dbtlabs", ["dbt"]),
    ("The Trade Desk", "tradedesk", ["trade-desk"]),
])
def test_generate_slugs(name, expected_first, must_contain):
    slugs = generate_slugs(name)
    assert slugs[0] == expected_first, slugs
    for s in must_contain:
        assert s in slugs
    assert len(slugs) == len(set(slugs)) <= 4
    assert all(s == s.lower() and " " not in s for s in slugs)


def test_generate_slugs_empty():
    assert generate_slugs("!!!") == []


def test_name_plausible_greenhouse_board_name():
    assert name_plausible("Stripe", BoardResult([], company_name="Stripe"))
    assert name_plausible("Scale AI", BoardResult([], company_name="Scale AI, Inc."))
    assert name_plausible("DoorDash", BoardResult([], company_name="DoorDash USA"))
    assert not name_plausible("Figure", BoardResult([], company_name="Acme Robotics"))
    assert not name_plausible("Hex", BoardResult([], company_name="Hexagon Manufacturing"))  # too short to contain


def test_name_plausible_evidence_text():
    ev = ["About Linear: Linear is building the issue tracker you'll enjoy using."]
    assert name_plausible("Linear", BoardResult([], name_evidence=ev))
    assert not name_plausible("Mercury", BoardResult([], name_evidence=["Join Acme Bank today."]))


def _gh_board(name, n=1):
    return {"jobs": [{"id": i, "title": "SWE", "company_name": name, "location": {"name": "NYC"},
                      "absolute_url": f"https://x/{i}"} for i in range(n)]}


def test_probe_accepts_only_plausible_hits(db):
    http = FakeHttp({
        GH.format("twosigma"): _gh_board("Two Sigma"),
        GH.format("figure"): _gh_board("Some Other Company"),   # wrong company
        GH.format("emptyco"): {"jobs": []},                     # no jobs
    })
    rep = probe_names([("Two Sigma", None), ("Figure", None), ("EmptyCo", None)], http, db, ADAPTERS,
                      ats_order=["greenhouse"], max_probes=50)
    assert [(h.ats, h.slug) for h in rep.hits] == [("greenhouse", "twosigma")]
    assert db.probe_cached("greenhouse", "figure", 30) == "miss"
    assert db.probe_cached("greenhouse", "emptyco", 30) == "miss"


def test_probe_negative_cache_prevents_reprobe(db):
    http = FakeHttp({})  # everything 404s
    probe_names([("Nope Corp", None)], http, db, ADAPTERS, ats_order=["greenhouse", "ashby", "lever"],
                max_probes=50)
    first_calls = len(http.calls)
    assert first_calls > 0
    rep = probe_names([("Nope Corp", None)], http, db, ADAPTERS, ats_order=["greenhouse", "ashby", "lever"],
                      max_probes=50)
    assert len(http.calls) == first_calls
    assert rep.probes == 0 and rep.cached_skips > 0


def test_probe_cap(db):
    http = FakeHttp({})
    names = [(f"Company {i}", None) for i in range(50)]
    rep = probe_names(names, http, db, ADAPTERS, ats_order=["greenhouse", "ashby", "lever"], max_probes=7)
    assert rep.probes == 7
    assert len(http.calls) == 7


def test_probe_transient_errors_not_cached(db):
    http = FakeHttp({GH.format("flaky"): HttpError(GH.format("flaky"), 503)})
    probe_names([("Flaky", None)], http, db, ADAPTERS, ats_order=["greenhouse"], max_probes=5)
    assert db.probe_cached("greenhouse", "flaky", 30) is None


def test_probe_uses_ats_hint_and_skips_known(db):
    http = FakeHttp({ASHBY.format("acme"): {"jobs": [{"id": "a", "title": "SWE", "descriptionPlain": "Acme rocks"}]}})
    rep = probe_names([("Acme", "ashby"), ("Stripe", None)], http, db, ADAPTERS,
                      ats_order=["greenhouse", "ashby", "lever"], max_probes=10, known={"stripe"})
    assert [(h.ats, h.slug) for h in rep.hits] == [("ashby", "acme")]
    assert http.calls == [ASHBY.format("acme")]
