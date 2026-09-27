"""ATS knowledge shared by sources and discovery: API endpoints, and
extracting (ats, slug, job_id) from arbitrary job/apply URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

SUPPORTED_ATS = ("greenhouse", "lever", "ashby")

BOARD_API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,79}$")
_GH_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards-api.greenhouse.io",
             "api.greenhouse.io"}
_GH_RESERVED = {"embed", "v1", "jobs", "api", "boards", ""}
_LEVER_HOSTS = {"jobs.lever.co", "api.lever.co"}
_ASHBY_HOSTS = {"jobs.ashbyhq.com", "api.ashbyhq.com"}
_ASHBY_RESERVED = {"posting-api", "api", ""}
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>()\[\]]+")


@dataclass(frozen=True)
class JobRef:
    ats: str
    slug: str | None
    job_id: str | None = None

    @property
    def company_key(self) -> str | None:
        return f"{self.ats}:{self.slug}" if self.slug else None


def _clean_slug(s: str | None) -> str | None:
    if not s:
        return None
    s = unquote(s).strip().lower()
    return s if _SLUG_RE.match(s) else None


def extract_job_ref(url: str | None) -> JobRef | None:
    """Parse a job or board URL. Returns None if it isn't a supported ATS.

    Handles:
      boards.greenhouse.io/{slug}/jobs/{id}, job-boards.greenhouse.io/{slug}/jobs/{id}
      boards.greenhouse.io/embed/job_app?for={slug}&token={id}
      boards-api.greenhouse.io/v1/boards/{slug}/jobs[/{id}]
      any-company-site.com/careers?gh_jid={id}           (job id only, no slug)
      jobs.lever.co/{slug}/{uuid}[/apply], api.lever.co/v0/postings/{slug}[/{uuid}]
      jobs.ashbyhq.com/{slug}[/{uuid}[/application]]
      api.ashbyhq.com/posting-api/job-board/{slug}
      any-company-site.com/careers?ashby_jid={uuid}       (job id only, no slug)
    """
    if not url:
        return None
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    host = (p.netloc or "").lower().split(":")[0]
    host = host.removeprefix("www.")
    parts = [seg for seg in p.path.split("/") if seg]
    qs = parse_qs(p.query)

    if host in _GH_HOSTS:
        if parts[:2] == ["v1", "boards"] and len(parts) >= 3:
            slug = _clean_slug(parts[2])
            job_id = parts[4] if len(parts) >= 5 and parts[3] == "jobs" else None
            return JobRef("greenhouse", slug, _digits(job_id)) if slug else None
        if parts and parts[0] == "embed":
            slug = _clean_slug((qs.get("for") or [None])[0])
            job_id = _digits((qs.get("token") or [None])[0])
            return JobRef("greenhouse", slug, job_id) if slug else None
        if parts and parts[0].lower() not in _GH_RESERVED:
            slug = _clean_slug(parts[0])
            job_id = _digits(parts[2]) if len(parts) >= 3 and parts[1] == "jobs" else None
            job_id = job_id or _digits((qs.get("gh_jid") or [None])[0])
            return JobRef("greenhouse", slug, job_id) if slug else None
        return None

    if host in _LEVER_HOSTS:
        if host == "api.lever.co":
            if parts[:2] == ["v0", "postings"] and len(parts) >= 3:
                slug = _clean_slug(parts[2])
                return JobRef("lever", slug, parts[3] if len(parts) >= 4 else None) if slug else None
            return None
        if parts:
            slug = _clean_slug(parts[0])
            job_id = parts[1] if len(parts) >= 2 and _is_uuid(parts[1]) else None
            return JobRef("lever", slug, job_id) if slug else None
        return None

    if host in _ASHBY_HOSTS:
        if host == "api.ashbyhq.com":
            if parts[:2] == ["posting-api", "job-board"] and len(parts) >= 3:
                slug = _clean_slug(parts[2])
                return JobRef("ashby", slug) if slug else None
            return None
        if parts and parts[0].lower() not in _ASHBY_RESERVED:
            slug = _clean_slug(parts[0])
            job_id = parts[1] if len(parts) >= 2 and _is_uuid(parts[1]) else None
            return JobRef("ashby", slug, job_id) if slug else None
        return None

    # Company-hosted career pages that embed an ATS board.
    if gh := _digits((qs.get("gh_jid") or [None])[0]):
        return JobRef("greenhouse", None, gh)
    if (aj := (qs.get("ashby_jid") or [None])[0]) and _is_uuid(aj):
        return JobRef("ashby", None, aj)
    return None


def extract_company_refs(text: str) -> set[JobRef]:
    """Find every ATS board (ats, slug) referenced by URLs in a blob of text."""
    found: set[JobRef] = set()
    for m in _URL_IN_TEXT.finditer(text):
        ref = extract_job_ref(m.group(0).rstrip(".,;:!?*`"))
        if ref and ref.slug:
            found.add(JobRef(ref.ats, ref.slug))
    return found


def _digits(s: str | None) -> str | None:
    return s if s and s.isdigit() else None


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _is_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s))
