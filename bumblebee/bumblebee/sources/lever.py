"""Lever public postings API (no auth).

GET https://api.lever.co/v0/postings/{slug}?mode=json
Descriptions and requirement lists come inline.
"""
from __future__ import annotations

from bumblebee.ats import BOARD_API
from bumblebee.filters import html_to_text
from bumblebee.http import HttpClient
from bumblebee.models import Job
from bumblebee.sources.base import ATSAdapter, BoardResult, clean_url, normalize_workplace, parse_dt

DETAIL_API = "https://api.lever.co/v0/postings/{slug}/{id}"


class LeverAdapter(ATSAdapter):
    ats = "lever"

    def fetch_board(self, http: HttpClient, slug: str, company_name: str | None = None) -> BoardResult:
        data = http.get_json(BOARD_API["lever"].format(slug=slug))
        raw = data if isinstance(data, list) else []
        name = company_name or slug
        jobs = [self.to_job(p, slug, name) for p in raw]
        evidence = [(p.get("openingPlain") or "") + " " + (p.get("descriptionPlain") or "")
                    + " " + (p.get("additionalPlain") or "") for p in raw[:5]]
        return BoardResult(jobs=jobs, company_name=None, name_evidence=evidence)

    @staticmethod
    def to_job(p: dict, slug: str, company: str) -> Job:
        cats = p.get("categories") or {}
        loc = (cats.get("location") or "").strip()
        all_locs = [x for x in (cats.get("allLocations") or []) if x] or ([loc] if loc else [])
        return Job(
            id=p["id"],
            company=company,
            title=(p.get("text") or "").strip(),
            location=" / ".join(all_locs) if len(all_locs) > 1 else loc,
            locations=all_locs,
            url=clean_url(p.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{p['id']}"),
            posted_at=parse_dt(p.get("createdAt"), millis=True),
            source="lever",
            workplace_type=normalize_workplace(p.get("workplaceType")),
            description=lever_text(p),
            ats="lever",
            company_slug=slug,
        )


def lever_text(p: dict) -> str:
    parts = [p.get("openingPlain") or "", p.get("descriptionBodyPlain") or p.get("descriptionPlain") or ""]
    for lst in p.get("lists") or []:
        parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
    parts.append(p.get("additionalPlain") or "")
    return "\n".join(x for x in parts if x)


def fetch_lever_posting(http: HttpClient, slug: str, posting_id: str) -> dict:
    return http.get_json(DETAIL_API.format(slug=slug, id=posting_id))
