"""Dedupe + the "posted since the last message" rule (and --seed)."""
from datetime import timedelta

from bumblebee.agent import Agent
from bumblebee.notify import ConsoleNotifier
from bumblebee.sources import ADAPTERS
from bumblebee.sources.simplify import SimplifySource
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


# ---- Agent: "posted since the last message" + dedupe ----------------------------

def make_agent_at(cfg, db, http, now, notifier=None):
    return Agent(cfg, db, http, notifier or ConsoleNotifier(), adapters={"greenhouse": ADAPTERS["greenhouse"]},
                 feeds=[], now=now)


def test_first_run_sends_jobs_posted_in_last_max_age_hours(cfg, db):
    db.add_company("greenhouse", "acme", name="Acme", status="active")
    http = FakeHttp(routes([gh_job(1, hours_ago=2), gh_job(2, hours_ago=30)]))
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, http, notifier=notifier).run()
    assert not s.seed_mode and s.new_matches == 1
    assert notifier.sent[0].endswith("/jobs/1")
    assert db.get_meta("last_notified_run_at") == NOW.isoformat()
    s2 = make_agent(cfg, db, http, notifier=notifier).run()             # dedupe
    assert s2.new_matches == 0 and len(notifier.sent) == 1


def test_only_jobs_posted_since_last_message_are_sent(cfg, db):
    active_seeded_company(db)
    db.set_meta("last_notified_run_at", (NOW - timedelta(hours=5)).isoformat())
    # cutoff = 5h ago - 60min slack = 6h ago
    jobs = [gh_job(1, hours_ago=3), gh_job(2, hours_ago=5.5), gh_job(3, hours_ago=7), gh_job(4, hours_ago=20)]
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, FakeHttp(routes(jobs)), notifier=notifier).run()
    assert s.new_matches == 2
    assert sorted(m[-1] for m in notifier.sent) == ["1", "2"]


def test_job_posted_during_sending_run_is_not_lost(cfg, db):
    # Run A starts at T, fetches acme before job 2 exists, then sends job 1.
    # Job 2 is posted at T+4min (before A's send). Run B must still send it.
    active_seeded_company(db)
    t = NOW - timedelta(hours=1)
    notifier = ConsoleNotifier()
    make_agent_at(cfg, db, FakeHttp(routes([gh_job(1, hours_ago=1.5)])), t, notifier).run()
    assert len(notifier.sent) == 1
    job2 = gh_job(2)
    job2["first_published"] = (t + timedelta(minutes=4)).isoformat()
    s = make_agent(cfg, db, FakeHttp(routes([gh_job(1, hours_ago=1.5), job2])), notifier=notifier).run()
    assert s.new_matches == 1 and notifier.sent[-1].endswith("/jobs/2")


def test_seed_flag_forces_silent(cfg, db):
    active_seeded_company(db)
    notifier = ConsoleNotifier()
    s = make_agent(cfg, db, FakeHttp(routes([gh_job(9)])), notifier=notifier).run(seed=True)
    assert s.seed_mode and s.seeded == 1 and notifier.sent == []


def test_new_company_first_fetch_uses_same_rule(cfg, db):
    db.set_meta("last_notified_run_at", (NOW - timedelta(hours=2)).isoformat())
    db.add_company("greenhouse", "acme", name="Acme", status="active")   # never fetched
    notifier = ConsoleNotifier()
    http = FakeHttp(routes([gh_job(1, hours_ago=1), gh_job(2, hours_ago=48)]))
    s = make_agent(cfg, db, http, notifier=notifier).run()
    assert s.seeded == 0 and s.new_matches == 1 and notifier.sent[0].endswith("/jobs/1")
    assert db.get_company("greenhouse", "acme").seeded_at


def test_failed_send_does_not_advance_cutoff(cfg, db):
    active_seeded_company(db)
    last = (NOW - timedelta(hours=5)).isoformat()
    db.set_meta("last_notified_run_at", last)

    class Failing(ConsoleNotifier):
        def send(self, *a, **k):
            from bumblebee.notify import NotifyError
            raise NotifyError("down")

    make_agent(cfg, db, FakeHttp(routes([gh_job(1)])), notifier=Failing()).run()
    assert db.get_meta("last_notified_run_at") == last
    assert not db.is_seen(make_job(id="1", url="https://job-boards.greenhouse.io/acme/jobs/1"))


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


def test_dry_run_max_age_override_widens_window(cfg, db):
    active_seeded_company(db)
    db.set_meta("last_notified_run_at", (NOW - timedelta(hours=1)).isoformat())
    http = FakeHttp(routes([gh_job(1, hours_ago=100)]))
    agent = Agent(cfg, db, http, ConsoleNotifier(), adapters={"greenhouse": ADAPTERS["greenhouse"]},
                  feeds=[], dry_run=True, now=NOW, max_age_override=168)
    s = agent.run()
    assert s.new_matches == 1 and db.seen_count() == 0


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
