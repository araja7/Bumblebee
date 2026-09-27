"""SimplifyJobs/New-Grad-Positions curated list.

The README is generated from .github/scripts/listings.json (~13 MB), which we
fetch with ETag caching so unchanged polls cost a 304.
"""
from __future__ import annotations

import json

from job_alert.ats import extract_job_ref
from job_alert.http import HttpClient
from job_alert.models import Job
from job_alert.sources.base import FeedSource, clean_url, parse_dt
from job_alert.sources.greenhouse import DETAIL_API as GH_DETAIL
from job_alert.filters import html_to_text
from job_alert.sources.lever import fetch_lever_posting, lever_text

DEFAULT_URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"


def load_listings(http: HttpClient, url: str = DEFAULT_URL) -> list[dict]:
    data = json.loads(http.get_cached_text(url))
    return data if isinstance(data, list) else []


def listing_to_job(item: dict) -> Job:
    url = clean_url(item.get("url") or "")
    ref = extract_job_ref(url)
    locs = [x for x in item.get("locations") or [] if x]
    return Job(
        id=str(item.get("id")),
        company=(item.get("company_name") or "").strip(),
        title=(item.get("title") or "").strip(),
        location=" / ".join(locs),
        locations=locs,
        url=url,
        posted_at=parse_dt(item.get("date_posted")),
        source="simplify",
        ats=ref.ats if ref else None,
        company_slug=ref.slug if ref else None,
    )


class SimplifySource(FeedSource):
    name = "simplify"

    def __init__(self, url: str = DEFAULT_URL):
        self.url = url

    def fetch(self, http: HttpClient) -> list[Job]:
        return [listing_to_job(i) for i in load_listings(http, self.url)
                if i.get("active") and i.get("is_visible", True)]

    def fetch_description(self, http: HttpClient, job: Job) -> str | None:
        """Simplify has no descriptions; follow the link to the ATS API when we can."""
        ref = extract_job_ref(job.url)
        if not ref or not ref.slug or not ref.job_id:
            return None
        if ref.ats == "greenhouse":
            return html_to_text(http.get_json(GH_DETAIL.format(slug=ref.slug, id=ref.job_id)).get("content"))
        if ref.ats == "lever":
            return lever_text(fetch_lever_posting(http, ref.slug, ref.job_id))
        return None
