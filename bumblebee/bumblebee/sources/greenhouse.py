"""Greenhouse public job board API (no auth).

List:   GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
Detail: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}

The list omits descriptions (content=true would be ~5 MB for big boards), so
we fetch details only for the few jobs that pass the cheap filters.
"""
from __future__ import annotations

from bumblebee.ats import BOARD_API
from bumblebee.filters import html_to_text
from bumblebee.http import HttpClient
from bumblebee.models import Job
from bumblebee.sources.base import ATSAdapter, BoardResult, clean_url, parse_dt

DETAIL_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}"


class GreenhouseAdapter(ATSAdapter):
    ats = "greenhouse"

    def fetch_board(self, http: HttpClient, slug: str, company_name: str | None = None) -> BoardResult:
        data = http.get_json(BOARD_API["greenhouse"].format(slug=slug))
        raw = data.get("jobs", []) if isinstance(data, dict) else []
        board_name = next((j.get("company_name") for j in raw if j.get("company_name")), None)
        name = company_name or board_name or slug
        jobs = [self._to_job(j, slug, name) for j in raw]
        return BoardResult(jobs=jobs, company_name=board_name, name_evidence=[board_name] if board_name else [])

    def _to_job(self, j: dict, slug: str, company: str) -> Job:
        loc = ((j.get("location") or {}).get("name") or "").strip()
        return Job(
            id=str(j["id"]),
            company=company,
            title=(j.get("title") or "").strip(),
            location=loc,
            locations=[loc] if loc else [],
            url=clean_url(j.get("absolute_url") or f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}"),
            posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
            source="greenhouse",
            ats="greenhouse",
            company_slug=slug,
        )

    def fetch_description(self, http: HttpClient, job: Job) -> str | None:
        if job.description is not None:
            return job.description
        data = http.get_json(DETAIL_API.format(slug=job.company_slug, id=job.id))
        offices = [o.get("name") for o in data.get("offices") or [] if o.get("name")]
        if offices:
            # Offices often list every city for a multi-location post.
            job.locations = list(dict.fromkeys(job.locations + offices))
        return html_to_text(data.get("content"))
