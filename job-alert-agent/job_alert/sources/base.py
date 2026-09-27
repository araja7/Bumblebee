"""Source adapter interfaces."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from job_alert.http import HttpClient
from job_alert.models import Job


@dataclass
class BoardResult:
    jobs: list[Job]
    company_name: str | None = None
    # Free text we can search for the company's name when judging whether a
    # probed slug really belongs to the company we were looking for.
    name_evidence: list[str] = field(default_factory=list)


class ATSAdapter(ABC):
    """One company board on an applicant-tracking system."""

    ats: str

    @abstractmethod
    def fetch_board(self, http: HttpClient, slug: str, company_name: str | None = None) -> BoardResult:
        """Fetch all open jobs for one company. Raises NotFound on 404."""

    def fetch_description(self, http: HttpClient, job: Job) -> str | None:
        """Plain-text description; adapters that get it inline just return it."""
        return job.description


class FeedSource(ABC):
    """A source that isn't per-company (e.g. a curated GitHub list)."""

    name: str

    @abstractmethod
    def fetch(self, http: HttpClient) -> list[Job]:
        ...

    def fetch_description(self, http: HttpClient, job: Job) -> str | None:
        return job.description


def parse_dt(value: str | int | float | None, *, millis: bool = False) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if millis else value, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


_TRACKING_KEYS = {"ref", "source", "src", "gh_src", "lever-source", "lever-origin", "embed"}


def clean_url(url: str) -> str:
    """Shortest direct link: strip tracking params (utm_*, ref=Simplify, ...),
    apply-form suffixes, and gh_jid when the path already has the job id.
    Keeps gh_jid on company-hosted pages where it's the only job id."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    host, path = p.netloc.lower(), p.path
    if host.endswith("ashbyhq.com"):
        path = re.sub(r"/application/?$", "", path)
    elif host.endswith("lever.co"):
        path = re.sub(r"/apply/?$", "", path)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_KEYS
         and not (k == "gh_jid" and re.search(rf"/jobs/{re.escape(v)}/?$", path))]
    return urlunparse(p._replace(path=path, query=urlencode(q)))


def normalize_workplace(value: str | None) -> str | None:
    v = (value or "").lower().replace("-", "").replace("_", "").replace(" ", "")
    if v in ("remote",):
        return "remote"
    if v in ("hybrid",):
        return "hybrid"
    if v in ("onsite", "inoffice", "office"):
        return "onsite"
    return None
