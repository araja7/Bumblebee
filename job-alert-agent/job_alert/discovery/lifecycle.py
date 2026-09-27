"""Company lifecycle: candidate -> active (validated: API 200 with >= 1 job),
and candidate/active -> dead after N consecutive 404s.

Active companies also accrue 404s during normal runs (see Agent), so a board
that disappears is retired even if discovery doesn't run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from job_alert.db import DB
from job_alert.fetching import fetch_boards
from job_alert.http import HttpClient, NotFound
from job_alert.log import get_logger
from job_alert.models import Company
from job_alert.sources import ATSAdapter, BoardResult

log = get_logger(__name__)


@dataclass
class LifecycleReport:
    checked: int = 0
    promoted: list[str] = field(default_factory=list)
    still_candidate: int = 0
    died: list[str] = field(default_factory=list)
    errors: int = 0


def apply_validation(db: DB, ats: str, slug: str, result: BoardResult | Exception, *,
                     dead_after: int, rep: LifecycleReport) -> None:
    key = f"{ats}:{slug}"
    rep.checked += 1
    if isinstance(result, Exception):
        status = db.record_fetch_error(ats, slug, str(result)[:300], not_found=isinstance(result, NotFound),
                                       dead_after=dead_after)
        if status == "dead":
            rep.died.append(key)
        elif not isinstance(result, NotFound):
            rep.errors += 1
        else:
            rep.still_candidate += 1
        return
    db.record_fetch_ok(ats, slug, len(result.jobs), None, result.company_name)
    if result.jobs:
        db.set_company_status(ats, slug, "active")
        rep.promoted.append(key)
    else:
        rep.still_candidate += 1


def validate_candidates(db: DB, http: HttpClient, adapters: dict[str, ATSAdapter], *,
                        max_validations: int, dead_after: int,
                        prefetched: dict[str, BoardResult] | None = None) -> LifecycleReport:
    rep = LifecycleReport()
    prefetched = prefetched or {}
    # Least-recently-checked first so a big backlog drains fairly over days,
    # interleaved across ATSes so each host's worker gets an even share.
    candidates = _interleave_by_ats(sorted((c for c in db.companies("candidate") if c.ats in adapters),
                                           key=lambda c: c.last_checked or ""))
    to_fetch = []
    for c in candidates:
        if c.key in prefetched:
            apply_validation(db, c.ats, c.slug, prefetched[c.key], dead_after=dead_after, rep=rep)
        elif len(to_fetch) < max_validations:
            to_fetch.append(c)
    for company, result in fetch_boards(http, adapters, to_fetch):
        apply_validation(db, company.ats, company.slug, result, dead_after=dead_after, rep=rep)
    remaining = len(candidates) - rep.checked
    if remaining > 0:
        log.info("validation backlog carried to next run", remaining=remaining)
    return rep


def _interleave_by_ats(companies: list[Company]) -> list[Company]:
    queues: dict[str, list[Company]] = {}
    for c in companies:
        queues.setdefault(c.ats, []).append(c)
    out: list[Company] = []
    while any(queues.values()):
        for q in queues.values():
            if q:
                out.append(q.pop(0))
    return out
