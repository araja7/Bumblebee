"""Ashby public job board API (no auth).

GET https://api.ashbyhq.com/posting-api/job-board/{slug}
Descriptions come inline as descriptionPlain.
"""
from __future__ import annotations

from bumblebee.ats import BOARD_API
from bumblebee.http import HttpClient
from bumblebee.models import Job
from bumblebee.sources.base import ATSAdapter, BoardResult, clean_url, normalize_workplace, parse_dt


class AshbyAdapter(ATSAdapter):
    ats = "ashby"

    def fetch_board(self, http: HttpClient, slug: str, company_name: str | None = None) -> BoardResult:
        data = http.get_json(BOARD_API["ashby"].format(slug=slug))
        raw = [j for j in (data.get("jobs", []) if isinstance(data, dict) else []) if j.get("isListed", True)]
        name = company_name or slug
        jobs = [self.to_job(j, slug, name) for j in raw]
        evidence = [j.get("descriptionPlain") or "" for j in raw[:5]]
        return BoardResult(jobs=jobs, company_name=None, name_evidence=evidence)

    @staticmethod
    def to_job(j: dict, slug: str, company: str) -> Job:
        primary = (j.get("location") or "").strip()
        secondary = [s.get("location", "").strip() for s in j.get("secondaryLocations") or [] if s.get("location")]
        all_locs = [x for x in [primary, *secondary] if x]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry"))
        return Job(
            id=j["id"],
            company=company,
            title=(j.get("title") or "").strip(),
            location=" / ".join(all_locs) if len(all_locs) > 1 else primary,
            locations=all_locs,
            url=clean_url(j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j['id']}"),
            posted_at=parse_dt(j.get("publishedAt")),
            source="ashby",
            workplace_type=normalize_workplace(j.get("workplaceType")),
            country=country,
            description=j.get("descriptionPlain") or "",
            ats="ashby",
            company_slug=slug,
        )
