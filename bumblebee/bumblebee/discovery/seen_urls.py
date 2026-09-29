"""Strategy: every job URL we've already seen may point at an ATS board we
don't monitor yet (e.g. a Simplify listing linking to jobs.lever.co/acme)."""
from __future__ import annotations

from bumblebee.ats import JobRef, extract_job_ref
from bumblebee.db import DB


def mine_seen_urls(db: DB) -> set[JobRef]:
    refs: set[JobRef] = set()
    for url in db.seen_urls():
        ref = extract_job_ref(url)
        if ref and ref.slug:
            refs.add(JobRef(ref.ats, ref.slug))
    return refs
