"""Strategy: mine public GitHub new-grad/intern lists for ATS board links,
and collect company names (for slug probing) from listings whose links
don't reveal a Greenhouse/Lever/Ashby slug."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from bumblebee.ats import JobRef, extract_company_refs, extract_job_ref
from bumblebee.filters import JobFilter
from bumblebee.http import HttpClient
from bumblebee.log import get_logger
from bumblebee.sources.simplify import listing_to_job

log = get_logger(__name__)


@dataclass
class NameHint:
    name: str
    score: float                 # higher = probe sooner
    ats_hint: str | None = None  # e.g. link had ?gh_jid=... => company is on Greenhouse


@dataclass
class MinedLists:
    refs: dict[JobRef, tuple[str | None, str]] = field(default_factory=dict)  # ref -> (company name, via)
    names: dict[str, NameHint] = field(default_factory=dict)                   # lowercased name -> hint
    errors: list[str] = field(default_factory=list)


def mine_github_lists(http: HttpClient, lists: list[dict[str, Any]], job_filter: JobFilter,
                      max_age_days: int = 180, now: float | None = None) -> MinedLists:
    out = MinedLists()
    now = now or time.time()
    cutoff = now - max_age_days * 86400
    for spec in lists:
        name, url, fmt = spec["name"], spec["url"], spec.get("format", "markdown")
        via = f"github:{name}"
        try:
            text = http.get_cached_text(url)
        except Exception as e:  # noqa: BLE001
            log.warning("github list fetch failed", list=name, error=str(e))
            out.errors.append(f"{name}: {e}")
            continue
        before = len(out.refs)
        if fmt == "json":
            _mine_simplify_json(text, via, cutoff, job_filter, out)
        else:
            for ref in extract_company_refs(text):
                out.refs.setdefault(ref, (None, via))
        log.info("mined github list", list=name, new_boards=len(out.refs) - before)
    return out


def _mine_simplify_json(text: str, via: str, cutoff: float, job_filter: JobFilter, out: MinedLists) -> None:
    try:
        items = json.loads(text)
    except json.JSONDecodeError as e:
        out.errors.append(f"{via}: bad JSON {e}")
        return
    for item in items if isinstance(items, list) else []:
        updated = max(item.get("date_updated") or 0, item.get("date_posted") or 0)
        if updated < cutoff:
            continue
        company = (item.get("company_name") or "").strip()
        ref = extract_job_ref(item.get("url"))
        if ref and ref.slug:
            out.refs.setdefault(JobRef(ref.ats, ref.slug), (company or None, via))
            continue
        if not company:
            continue
        # No slug in the link: remember the name for probing. Prefer companies
        # posting roles we'd actually want, recently, in our metros; and those
        # whose link proves which ATS they use (?gh_jid= / ?ashby_jid=).
        job = listing_to_job(item)
        score = 1.0 + (updated - cutoff) / 86400 / 365
        if item.get("active"):
            score += 1
        if job_filter.titles.check(job.title) is None:
            score += 2
        if job_filter.locations.check(job) is None:
            score += 2
        if ref and not ref.slug:
            score += 5
        key = company.lower()
        prev = out.names.get(key)
        if prev is None or score > prev.score:
            out.names[key] = NameHint(company, score, ref.ats if ref else (prev.ats_hint if prev else None))
