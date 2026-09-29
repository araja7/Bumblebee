"""Shared data types."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    id: str                      # source-specific external id
    company: str
    title: str
    location: str                # human-readable, possibly multi-location
    url: str
    posted_at: datetime | None
    source: str                  # greenhouse | lever | ashby | simplify
    # Optional extras populated by adapters that have them.
    locations: list[str] = field(default_factory=list)  # individual location entries
    workplace_type: str | None = None   # remote | hybrid | onsite | None (unknown)
    country: str | None = None          # ISO-ish country code when known
    description: str | None = None      # plain text; may be fetched lazily
    ats: str | None = None              # ATS that hosts the posting, if known
    company_slug: str | None = None     # ATS board slug, if known
    matched_metros: list[str] = field(default_factory=list)  # set by filters

    def all_locations(self) -> list[str]:
        return self.locations or ([self.location] if self.location else [])

    @property
    def canonical_key(self) -> str:
        """Stable identity across sources.

        Greenhouse job ids and Lever/Ashby posting UUIDs are globally unique,
        so a Simplify listing that links to greenhouse job 123 and our own
        Greenhouse fetch of job 123 collapse to the same key.
        """
        from bumblebee.ats import extract_job_ref

        if self.ats and self.id and self.source == self.ats:
            return f"{self.ats}:{self.id}"
        ref = extract_job_ref(self.url)
        if ref and ref.job_id:
            return f"{ref.ats}:{ref.job_id}"
        return f"{self.source}:{self.id}"

    @property
    def fingerprint(self) -> str:
        """Fallback identity: hash of normalized company + title + location."""
        loc = ",".join(sorted(self.matched_metros)) or _norm(self.location)
        raw = "|".join([_norm_company(self.company), _norm(self.title), loc])
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class Company:
    ats: str
    slug: str
    name: str | None = None
    status: str = "candidate"     # candidate | active | dead | removed
    discovered_via: str | None = None
    last_checked: str | None = None
    job_count: int | None = None
    relevant_count: int | None = None
    consecutive_404: int = 0
    seeded_at: str | None = None
    added_at: str | None = None
    last_error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.slug}"


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|holdings)\b"
)


def _norm(s: str | None) -> str:
    return _NON_ALNUM.sub(" ", (s or "").lower()).strip()


def _norm_company(s: str | None) -> str:
    return _NON_ALNUM.sub("", _COMPANY_SUFFIXES.sub(" ", _norm(s)))
