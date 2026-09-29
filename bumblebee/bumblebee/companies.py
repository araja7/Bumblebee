"""Company registry helpers: seeding from companies.yaml and parsing
one-line manual adds like `greenhouse:stripe` or a job URL."""
from __future__ import annotations

from pathlib import Path

import yaml

from bumblebee.ats import SUPPORTED_ATS, extract_job_ref
from bumblebee.db import DB
from bumblebee.log import get_logger

log = get_logger(__name__)


def parse_company_spec(spec: str) -> tuple[str, str]:
    """'greenhouse:stripe', 'lever/palantir', or any Greenhouse/Lever/Ashby URL."""
    spec = spec.strip()
    if spec.startswith("http"):
        ref = extract_job_ref(spec)
        if not ref or not ref.slug:
            raise ValueError(f"Couldn't find a Greenhouse/Lever/Ashby board slug in {spec!r}")
        return ref.ats, ref.slug
    for sep in (":", "/"):
        if sep in spec:
            ats, slug = (x.strip().lower() for x in spec.split(sep, 1))
            if ats not in SUPPORTED_ATS:
                raise ValueError(f"Unknown ATS {ats!r}; expected one of {', '.join(SUPPORTED_ATS)}")
            if not slug:
                raise ValueError("Empty slug")
            return ats, slug
    raise ValueError("Expected ats:slug (e.g. greenhouse:stripe) or a job-board URL")


def seed_companies_from_yaml(db: DB, path: Path) -> int:
    """Insert companies from companies.yaml that aren't in the DB yet (as
    active: they're hand-verified). Never resurrects removed/dead ones."""
    if not path.exists():
        return 0
    data = yaml.safe_load(path.read_text()) or {}
    added = 0
    for ats, entries in data.items():
        if ats not in SUPPORTED_ATS:
            log.warning("unknown ATS in companies.yaml", ats=ats)
            continue
        for entry in entries or []:
            if isinstance(entry, str):
                slug, name = entry, None
            else:
                slug, name = entry["slug"], entry.get("name")
            if db.add_company(ats, slug.lower(), name=name, status="active", discovered_via="seed") == "added":
                added += 1
    if added:
        log.info("seeded companies from yaml", added=added)
    return added
