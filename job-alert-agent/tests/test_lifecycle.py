"""candidate -> active -> dead lifecycle, plus discovery registration."""
from job_alert.agent import Agent
from job_alert.discovery.lifecycle import validate_candidates
from job_alert.discovery.runner import run_discovery
from job_alert.discovery.seen_urls import mine_seen_urls
from job_alert.http import HttpError
from job_alert.notify import ConsoleNotifier
from job_alert.sources import ADAPTERS, BoardResult
from tests.conftest import NOW, FakeHttp, make_job

GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
BOARD = {"jobs": [{"id": 1, "title": "SWE", "company_name": "Acme", "location": {"name": "NYC"},
                   "absolute_url": "https://x/1"}]}


def validate(db, http, **kw):
    return validate_candidates(db, http, {"greenhouse": ADAPTERS["greenhouse"]}, max_validations=100,
                               dead_after=3, **kw)


def test_candidate_promoted_when_board_has_jobs(db):
    db.add_company("greenhouse", "acme")
    assert db.get_company("greenhouse", "acme").status == "candidate"
    rep = validate(db, FakeHttp({GH.format("acme"): BOARD}))
    assert rep.promoted == ["greenhouse:acme"]
    c = db.get_company("greenhouse", "acme")
    assert c.status == "active" and c.job_count == 1 and c.name == "Acme"


def test_candidate_with_zero_jobs_stays_candidate(db):
    db.add_company("greenhouse", "empty")
    rep = validate(db, FakeHttp({GH.format("empty"): {"jobs": []}}))
    assert rep.still_candidate == 1
    assert db.get_company("greenhouse", "empty").status == "candidate"


def test_candidate_dies_after_three_consecutive_404s(db):
    db.add_company("greenhouse", "gone")
    http = FakeHttp({})  # 404
    for i in range(2):
        validate(db, http)
        c = db.get_company("greenhouse", "gone")
        assert c.status == "candidate" and c.consecutive_404 == i + 1
    rep = validate(db, http)
    assert rep.died == ["greenhouse:gone"]
    assert db.get_company("greenhouse", "gone").status == "dead"


def test_404_counter_resets_on_success(db):
    db.add_company("greenhouse", "blip")
    validate(db, FakeHttp({}))
    validate(db, FakeHttp({}))
    validate(db, FakeHttp({GH.format("blip"): BOARD}))
    c = db.get_company("greenhouse", "blip")
    assert c.status == "active" and c.consecutive_404 == 0


def test_transient_errors_do_not_count_toward_death(db):
    db.add_company("greenhouse", "flaky")
    http = FakeHttp({GH.format("flaky"): HttpError(GH.format("flaky"), 503)})
    for _ in range(5):
        validate(db, http)
    c = db.get_company("greenhouse", "flaky")
    assert c.status == "candidate" and c.consecutive_404 == 0


def test_active_company_dies_during_runs(cfg, db):
    db.set_meta("initialized", NOW.isoformat())
    db.add_company("greenhouse", "acme", status="active")
    for _ in range(3):
        Agent(cfg, db, FakeHttp({}), ConsoleNotifier(), adapters={"greenhouse": ADAPTERS["greenhouse"]},
              feeds=[], now=NOW).run()
    assert db.get_company("greenhouse", "acme").status == "dead"


def test_prefetched_probe_results_skip_refetch(db):
    db.add_company("greenhouse", "probed")
    http = FakeHttp({})
    board = ADAPTERS["greenhouse"].fetch_board(FakeHttp({GH.format("probed"): BOARD}), "probed")
    rep = validate(db, http, prefetched={"greenhouse:probed": board})
    assert rep.promoted == ["greenhouse:probed"] and http.calls == []


def test_removed_never_readded_dead_revived_by_live_link(db):
    db.add_company("greenhouse", "bye", status="active")
    db.set_company_status("greenhouse", "bye", "removed")
    assert db.add_company("greenhouse", "bye", revive_dead=True) == "exists"
    assert db.get_company("greenhouse", "bye").status == "removed"

    db.add_company("greenhouse", "zombie", status="dead")
    assert db.add_company("greenhouse", "zombie", revive_dead=True) == "revived"
    assert db.get_company("greenhouse", "zombie").status == "candidate"


def test_seen_urls_strategy(db):
    db.mark_seen([make_job(url="https://jobs.lever.co/newco/6ed76ce8-4156-4b60-b120-403538bd66cd", source="simplify",
                           ats=None, id="s1"),
                  make_job(url="https://acme.wd1.myworkdayjobs.com/x", source="simplify", ats=None, id="s2")],
                 "seeded")
    refs = mine_seen_urls(db)
    assert {(r.ats, r.slug) for r in refs} == {("lever", "newco")}


def test_run_discovery_end_to_end(cfg, db, tmp_path):
    lists = cfg["discovery"]["github_lists"] = [
        {"name": "test/md", "format": "markdown", "url": "https://gh.test/README.md"},
        {"name": "test/json", "format": "json", "url": "https://gh.test/listings.json"},
    ]
    (tmp_path / "candidate_names.txt").write_text("# comment\nTwo Sigma\n")
    import time as _t
    now = _t.time()
    http = FakeHttp({
        lists[0]["url"]: "| Foo | [apply](https://job-boards.greenhouse.io/foo/jobs/1) |",
        lists[1]["url"]: [
            {"company_name": "Bar", "url": "https://jobs.ashbyhq.com/bar/34413f8d-26bf-4bbc-8ade-eb309a0e2245",
             "date_posted": now, "active": True, "title": "Software Engineer"},
            {"company_name": "Old", "url": "https://jobs.lever.co/old/34413f8d-26bf-4bbc-8ade-eb309a0e2245",
             "date_posted": now - 400 * 86400, "active": False, "title": "Software Engineer"},
        ],
        GH.format("foo"): BOARD,
        "https://api.ashbyhq.com/posting-api/job-board/bar": {"jobs": [{"id": "x", "title": "SWE"}]},
        GH.format("twosigma"): {"jobs": [{"id": 5, "title": "SWE", "company_name": "Two Sigma",
                                          "location": {"name": "New York"}, "absolute_url": "https://x/5"}]},
    })
    s = run_discovery(cfg, db, http, max_probes=10)
    assert sorted(s.added["github"]) == ["ashby:bar", "greenhouse:foo"]  # stale listing ignored
    assert s.added["probe"] == ["greenhouse:twosigma"]
    assert set(s.lifecycle.promoted) == {"ashby:bar", "greenhouse:foo", "greenhouse:twosigma"}
    assert db.get_company("greenhouse", "twosigma").discovered_via == "probe:Two Sigma"
    assert s.counts_after.get("active") == 3
