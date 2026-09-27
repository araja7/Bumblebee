"""Dedupe + the seeding rules (first run, --seed, newly added companies)."""
from datetime import timedelta

from job_alert.agent import Agent
from job_alert.notify import ConsoleNotifier
from job_alert.sources import ADAPTERS
from job_alert.sources.simplify import SimplifySource
from tests.conftest import NOW, FakeHttp, make_job

GH_LIST = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
GH_DETAIL = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/{}"
SIMPLIFY = "https://example.test/listings.json"


def gh_job(i, title="Software Engineer, New Grad", loc="New York, NY", hours_ago=1):
    return {"id": i, "title": title, "company_name": "Acme", "location": {"name": loc},
            "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{i}",
            "first_published": (NOW - timedelta(hours=hours_ago)).isoformat()}


def routes(jobs, descriptions=None):
    r = {GH_LIST: {"jobs": jobs}}
    for j in jobs:
        r[GH_DETAIL.format(j["id"])] = {"content": (descriptions or {}).get(j["id"], "New grads welcome.")}
    return r


def make_agent(cfg, db, http, feeds=None, dry_run=False, notifier=None):
    return Agent(cfg, db, http, notifier or ConsoleNotifier(), adapters={"greenhouse": ADAPTERS["greenhouse"]},
                 feeds=feeds or [], dry_run=dry_run, now=NOW)


def active_seeded_company(db):
    db.add_company("greenhouse", "acme", name="Acme", status="active")
    db.mark_company_seeded("greenhouse", "acme")
    db.set_meta("initialized", NOW.isoformat())


# ---- DB-level dedupe ----------------------------------------------------------

def test_seen_by_key(db):
    j = make_job()
    assert not db.is_seen(j)
    db.mark_seen([j], "notified")
    assert db.is_seen(make_job())  # same canonical key
    assert db.mark_seen([j], "notified") == 0  # idempotent


def test_cross_source_same_ats_job_is_duplicate(db):
    db.mark_seen([make_job(id="555", url="https://job-boards.greenhouse.io/acme/jobs/555")], "notified")
    simplify_copy = make_job(id="uuid-1", source="simplify", ats="greenhouse",
                             url="https://boards.greenhouse.io/acme/jobs/555?utm_source=Simplify")
    assert simplify_copy.canonical_key == "greenhouse:555"
    assert db.is_seen(simplify_copy)


def test_fingerprint_fallback_across_sources(db):
    a = make_job(id="1", url="https://acme.com/careers/1", source="greenhouse", ats=None, matched_metros=["new_york"])
    db.mark_seen([a], "notified")
    b = make_job(id="xyz", source="simplify", ats=None, company="Acme, Inc.", title="Software  Engineer",
                 url="https://acme.wd5.myworkdayjobs.com/x", location="New York City", matched_metros=["new_york"])
    assert a.canonical_key != b.canonical_key
    assert a.fingerprint == b.fingerprint
    assert db.is_seen(b)


def test_fingerprint_same_source_different_req_not_duplicate(db):
    # Two separate "Software Engineer" reqs at one company are distinct postings.
    db.mark_seen([make_job(id="1", matched_metros=["new_york"])], "notified")
    other = make_job(id="2", url="https://job-boards.greenhouse.io/acme/jobs/2", matched_metros=["new_york"])
    assert not db.is_seen(other)


# ---- Agent seeding & dedupe -------------------------------------------------------

def test_first_run_seeds_without_texting(cfg, db):
    db.add_company("greenhouse", "acme", name="Acme", status="active")
    http = FakeHttp(routes([gh_job(1), gh_job(2)]))
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, http, notifier=notifier).run()
    assert s.seed_mode and s.seeded == 2
    assert notifier.sent == []
    assert db.get_meta("initialized")
    # Second run: nothing new
    s2 = make_agent(cfg, db, http, notifier=notifier).run()
    assert not s2.seed_mode and s2.new_matches == 0 and notifier.sent == []


def test_new_job_after_seed_is_texted_once(cfg, db):
    db.add_company("greenhouse", "acme", name="Acme", status="active")
    make_agent(cfg, db, FakeHttp(routes([gh_job(1)]))).run()          # seed
    notifier = ConsoleNotifier()
    http = FakeHttp(routes([gh_job(1), gh_job(2)]))
    s = make_agent(cfg, db, http, notifier=notifier).run()
    assert s.new_matches == 1 and len(notifier.sent) == 1
    assert "Acme - Software Engineer, New Grad (NYC) https://job-boards.greenhouse.io/acme/jobs/2" == notifier.sent[0]
    s = make_agent(cfg, db, http, notifier=notifier).run()             # dedupe
    assert s.new_matches == 0 and len(notifier.sent) == 1


def test_seed_flag_forces_silent(cfg, db):
    active_seeded_company(db)
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, FakeHttp(routes([gh_job(9)])), notifier=notifier).run(seed=True)
    assert s.seed_mode and s.seeded == 1 and notifier.sent == []


def test_new_company_first_fetch_is_seeded_silently(cfg, db):
    db.set_meta("initialized", NOW.isoformat())
    db.add_company("greenhouse", "acme", name="Acme", status="active")   # never fetched
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, FakeHttp(routes([gh_job(1)])), notifier=notifier).run()
    assert s.seeded == 1 and notifier.sent == []
    assert db.get_company("greenhouse", "acme").seeded_at
    s = make_agent(cfg, db, FakeHttp(routes([gh_job(1), gh_job(2)])), notifier=notifier).run()
    assert s.new_matches == 1 and len(notifier.sent) == 1


def test_filters_applied(cfg, db):
    active_seeded_company(db)
    jobs = [gh_job(1), gh_job(2, title="Senior Software Engineer"), gh_job(3, loc="Remote"),
            gh_job(4, hours_ago=30), gh_job(5), gh_job(6, loc="Austin, TX")]
    http = FakeHttp(routes(jobs, descriptions={5: "Requirements: 5+ years of experience with Java"}))
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, http, notifier=notifier).run()
    assert s.new_matches == 1 and s.rejected == 1
    assert notifier.sent[0].endswith("/jobs/1")
    # rejected job is remembered so we don't re-fetch its description each run
    calls_before = len(http.calls)
    make_agent(cfg, db, http, notifier=notifier).run()
    assert GH_DETAIL.format(5) not in http.calls[calls_before:]


def test_dry_run_writes_nothing(cfg, db):
    active_seeded_company(db)
    notifier = ConsoleNotifier()
    http = FakeHttp(routes([gh_job(1)]))
    s = make_agent(cfg, db, http, notifier=notifier, dry_run=True).run()
    assert s.new_matches == 1 and len(notifier.sent) == 1
    assert db.seen_count() == 0 and db.notifications_today() == 0


def test_dry_run_on_empty_db_previews_instead_of_seeding(cfg, db):
    db.add_company("greenhouse", "acme", name="Acme", status="active")
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, FakeHttp(routes([gh_job(1)])), notifier=notifier, dry_run=True).run()
    assert not s.seed_mode and s.new_matches == 1
    assert db.get_meta("initialized") is None and db.seen_count() == 0


def test_simplify_duplicate_of_monitored_board_skipped(cfg, db):
    active_seeded_company(db)
    db.set_meta("feed_seeded:simplify", NOW.isoformat())
    listing = [{"id": "u1", "company_name": "Acme", "title": "Software Engineer, New Grad", "active": True,
                "is_visible": True, "date_posted": NOW.timestamp() - 3600, "locations": ["New York, NY"],
                "url": "https://job-boards.greenhouse.io/acme/jobs/1?utm_source=Simplify"},
               {"id": "u2", "company_name": "Other Co", "title": "Software Engineer I", "active": True,
                "is_visible": True, "date_posted": NOW.timestamp() - 3600, "locations": ["Chicago, IL"],
                "url": "https://otherco.wd1.myworkdayjobs.com/job/123?utm_source=Simplify"}]
    r = routes([gh_job(1)])
    r[SIMPLIFY] = listing
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, FakeHttp(r), feeds=[SimplifySource(SIMPLIFY)], notifier=notifier).run()
    assert s.new_matches == 2
    assert sum("jobs/1" in m for m in notifier.sent) == 1        # not texted twice
    assert any("Other Co" in m and "utm_source" not in m for m in notifier.sent)


def test_one_failing_source_does_not_crash(cfg, db):
    active_seeded_company(db)
    db.add_company("greenhouse", "broken", status="active")
    db.mark_company_seeded("greenhouse", "broken")
    r = routes([gh_job(1)])
    r["https://boards-api.greenhouse.io/v1/boards/broken/jobs"] = RuntimeError("boom")
    r[SIMPLIFY] = RuntimeError("feed down")
    s = make_agent(cfg, db, FakeHttp(r), feeds=[SimplifySource(SIMPLIFY)]).run()
    assert s.companies_failed == 1 and s.new_matches == 1
