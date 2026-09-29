"""One job-check run: fetch -> filter -> dedupe -> notify."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from bumblebee.config import Config
from bumblebee.db import DB
from bumblebee.fetching import fetch_boards
from bumblebee.filters import JobFilter
from bumblebee.http import HttpClient, NotFound
from bumblebee.log import get_logger
from bumblebee.models import Company, Job
from bumblebee.notify import Notifier, NotifyError
from bumblebee.notify.base import format_batch, format_job_line
from bumblebee.sources import ADAPTERS, ATSAdapter, FeedSource, SimplifySource

log = get_logger(__name__)


@dataclass
class RunSummary:
    companies_checked: int = 0
    companies_skipped: int = 0
    companies_failed: int = 0
    companies_died: int = 0
    jobs_fetched: int = 0
    candidates: int = 0
    new_matches: int = 0
    seeded: int = 0
    rejected: int = 0
    notified_jobs: int = 0
    messages_sent: int = 0
    capped: int = 0
    seed_mode: bool = False
    matches: list[Job] = field(default_factory=list)
    duration_s: float = 0.0


class Agent:
    def __init__(self, cfg: Config, db: DB, http: HttpClient, notifier: Notifier, *,
                 adapters: dict[str, ATSAdapter] | None = None,
                 feeds: list[FeedSource] | None = None,
                 dry_run: bool = False, now: datetime | None = None,
                 max_age_override: float | None = None):
        self.cfg, self.db, self.http, self.notifier = cfg, db, http, notifier
        self.adapters = adapters if adapters is not None else {
            k: v for k, v in ADAPTERS.items() if cfg["sources"].get(k, {}).get("enabled", True)}
        if feeds is None:
            scfg = cfg["sources"].get("simplify", {})
            feeds = [SimplifySource(scfg["url"])] if scfg.get("enabled", True) and scfg.get("url") else []
        self.feeds = feeds
        self.filter = JobFilter(cfg.criteria, cfg["locations"])
        self.dry_run = dry_run
        self.now = now or datetime.now(timezone.utc)
        self.max_age_override = max_age_override
        self.dead_after = cfg["discovery"].get("dead_after_consecutive_404s", 3)
        self._run_keys: set[str] = set()
        self._run_fps: set[str] = set()

    # ------------------------------------------------------------------
    def run(self, seed: bool = False) -> RunSummary:
        t0 = time.monotonic()
        s = RunSummary()
        s.seed_mode = seed
        if s.seed_mode:
            log.info("SEED MODE: recording current matches without texting", reason="--seed")
        self.cutoff = self._cutoff()
        log.info("alerting on jobs posted after cutoff", cutoff=self.cutoff.isoformat())

        companies = self._due_companies(s)
        outcomes = fetch_boards(self.http, self.adapters, companies)
        monitored = {c.key for c in self.db.companies("active")}

        for company, result in outcomes:
            if isinstance(result, Exception):
                self._record_failure(company, result, s)
                continue
            s.companies_checked += 1
            relevant = sum(1 for j in result.jobs if self.filter.locations.metros_for(" / ".join(j.all_locations())))
            if not self.dry_run:
                self.db.record_fetch_ok(company.ats, company.slug, len(result.jobs), relevant, result.company_name)
            self._process(result.jobs, self.adapters[company.ats], s, seed=s.seed_mode,
                          label=company.key, first_fetch=company.seeded_at is None)
            if company.seeded_at is None and not self.dry_run:
                self.db.mark_company_seeded(company.ats, company.slug)

        for feed in self.feeds:
            try:
                jobs = feed.fetch(self.http)
            except Exception as e:  # noqa: BLE001
                log.error("feed failed", feed=feed.name, error=f"{type(e).__name__}: {e}")
                continue
            # Jobs on boards we monitor directly are handled by the ATS adapter.
            jobs = [j for j in jobs if not (j.ats and j.company_slug and f"{j.ats}:{j.company_slug}" in monitored)]
            feed_seeded = self.db.get_meta(f"feed_seeded:{feed.name}") is not None
            self._process(jobs, feed, s, seed=s.seed_mode, label=f"feed:{feed.name}", first_fetch=not feed_seeded)
            if not self.dry_run:
                self.db.set_meta(f"feed_seeded:{feed.name}", self.now.isoformat())

        if s.seed_mode and not self.dry_run:
            self.db.set_meta("initialized", self.now.isoformat())

        s.matches.sort(key=lambda j: j.posted_at or self.now, reverse=True)
        s.new_matches = len(s.matches)
        if s.matches:
            self._notify(s)
        s.duration_s = round(time.monotonic() - t0, 1)
        log.info("run complete", **{k: v for k, v in s.__dict__.items() if k != "matches"},
                 http_requests=self.http.request_count)
        return s

    # ------------------------------------------------------------------
    def _cutoff(self) -> datetime:
        """Alert only on jobs posted after this: the start of the run that sent
        the last message, minus some slack. Using the run's start (not the send
        time) keeps jobs posted mid-run from falling in the gap; the slack
        covers ATS indexing lag. Dedupe stops the overlap from repeating
        anything. Before any message, fall back to `max_age_hours`."""
        last = self.db.get_meta("last_notified_run_at")
        if last and self.max_age_override is None:
            slack = timedelta(minutes=self.cfg.criteria.get("cutoff_slack_minutes", 60))
            return datetime.fromisoformat(last) - slack
        hours = self.max_age_override if self.max_age_override is not None else self.filter.max_age_hours
        return self.now - timedelta(hours=hours)

    def _due_companies(self, s: RunSummary) -> list[Company]:
        """Every active company with jobs in our metros (or unknown) is due each
        run; others only every `low_relevance_interval_minutes`."""
        interval = timedelta(minutes=self.cfg.get("scheduling", {}).get("low_relevance_interval_minutes", 60))
        due: list[Company] = []
        for c in self.db.companies("active"):
            if c.ats not in self.adapters:
                continue
            if c.relevant_count == 0 and c.last_checked and c.seeded_at:
                last = datetime.fromisoformat(c.last_checked)
                if self.now - last < interval:
                    s.companies_skipped += 1
                    continue
            due.append(c)
        log.info("companies due this run", due=len(due), skipped_low_relevance=s.companies_skipped)
        return due

    def _record_failure(self, company: Company, err: Exception, s: RunSummary) -> None:
        s.companies_failed += 1
        not_found = isinstance(err, NotFound)
        log.warning("company fetch failed", company=company.key, error=f"{type(err).__name__}: {err}")
        if self.dry_run:
            return
        status = self.db.record_fetch_error(company.ats, company.slug, str(err)[:300],
                                            not_found=not_found, dead_after=self.dead_after)
        if status == "dead":
            s.companies_died += 1
            log.warning("company marked dead after repeated 404s", company=company.key)

    def _process(self, jobs: list[Job], source: ATSAdapter | FeedSource, s: RunSummary, *,
                 seed: bool, label: str, first_fetch: bool) -> None:
        s.jobs_fetched += len(jobs)
        candidates = [j for j in jobs if self.filter.prefilter(j) is None]
        s.candidates += len(candidates)
        unseen = [j for j in candidates if not self._seen(j)]
        if seed:
            if not self.dry_run:
                s.seeded += self.db.mark_seen(unseen, "seeded", "first run" if s.seed_mode else "new source")
            else:
                s.seeded += len(unseen)
            if unseen:
                log.info("seeded silently", source=label, jobs=len(unseen), first_fetch=first_fetch)
            return
        for job in unseen:
            if not self.filter.posted_after(job, self.cutoff):
                continue
            try:
                job.description = source.fetch_description(self.http, job)
            except Exception as e:  # noqa: BLE001 - can't verify YOE; err toward alerting
                log.warning("description fetch failed; skipping YOE check", job=job.url, error=str(e))
            if reason := self.filter.experience(job):
                s.rejected += 1
                log.info("rejected", company=job.company, title=job.title, reason=reason)
                if not self.dry_run:
                    self.db.mark_seen([job], "rejected", reason)
                continue
            self._run_keys.add(job.canonical_key)
            self._run_fps.add(job.fingerprint)
            s.matches.append(job)
            log.info("NEW MATCH", company=job.company, title=job.title, location=job.location, url=job.url)

    def _seen(self, job: Job) -> bool:
        return (job.canonical_key in self._run_keys or job.fingerprint in self._run_fps
                or self.db.is_seen(job))

    # ------------------------------------------------------------------
    def _line(self, job: Job, batch: bool = False) -> str:
        loc = self.filter.locations.short_label(job.matched_metros) or job.location
        ncfg = self.cfg["notifier"]
        squeeze = self.notifier.sms_like and not (batch and ncfg.get("batch_via_mms", True))
        max_chars = ncfg.get("max_chars", 140) if squeeze else None
        return format_job_line(job.company, job.title, loc, job.url, max_chars)

    def _notify(self, s: RunSummary) -> None:
        ncfg = self.cfg["notifier"]
        cap = ncfg.get("daily_cap", 30)
        remaining = cap - (0 if self.dry_run else self.db.notifications_today())
        jobs = s.matches
        if len(jobs) > ncfg.get("batch_threshold", 5):
            size = max(1, ncfg.get("batch_max_items", 8))
            chunks = [jobs[i:i + size] for i in range(0, len(jobs), size)]
            messages = [(format_batch([self._line(j, batch=True) for j in chunk], len(jobs), i + 1, len(chunks)), chunk, True)
                        for i, chunk in enumerate(chunks)]
        else:
            messages = [(self._line(j), [j], False) for j in jobs]

        for idx, (text, chunk, batch) in enumerate(messages):
            if remaining <= 0:
                left = [j for _, c, _ in messages[idx:] for j in c]
                s.capped += len(left)
                log.warning("DAILY CAP HIT: not texting remaining matches", cap=cap, jobs_skipped=len(left),
                            urls=" ".join(j.url for j in left))
                if not self.dry_run:
                    self.db.mark_seen(left, "capped", f"daily cap {cap}")
                return
            try:
                self.notifier.send(text, title=None if batch else f"{chunk[0].company}: {chunk[0].title}",
                                   url=None if batch else chunk[0].url, batch=batch)
            except NotifyError as e:
                # Leave these unseen so the next run retries them.
                log.error("notification failed; will retry next run", error=str(e))
                return
            remaining -= 1
            s.messages_sent += 1
            s.notified_jobs += len(chunk)
            if not self.dry_run:
                self.db.mark_seen(chunk, "notified")
                self.db.log_notification(self.notifier.channel, len(chunk), text)
                self.db.set_meta("last_notified_run_at", self.now.isoformat())
