"""Strategy: YC companies that are currently hiring (via the community-run
yc-oss API), turned into company names for ATS slug probing. Early-stage
startups are where "Member of Technical Staff" new-grad roles live."""
from __future__ import annotations

import json
from typing import Any

from bumblebee.http import HttpClient
from bumblebee.log import get_logger

log = get_logger(__name__)

YC_HIRING_URL = "https://yc-oss.github.io/api/companies/hiring.json"
_US = ("united states", "usa", ", ca", "new york", "seattle", "boston", "chicago")


def _score(c: dict[str, Any], interest_tags: set[str]) -> float:
    tags = {t.lower() for t in c.get("tags") or []} | {str(c.get("industry", "")).lower()}
    score = 10.0 * len(tags & interest_tags)
    size = c.get("team_size") or 0
    if 5 <= size <= 300:
        score += 5            # big enough to have a real careers page, small enough to hire new grads
    score += min((c.get("launched_at") or 0) / 1e9, 3)   # mild recency bump
    return score


def mine_yc(http: HttpClient, cfg: dict[str, Any]) -> list[str]:
    """Names of hiring YC companies, best-fit first."""
    url = cfg.get("url", YC_HIRING_URL)
    try:
        items = json.loads(http.get_cached_text(url))
    except Exception as e:  # noqa: BLE001
        log.warning("yc list fetch failed", error=str(e))
        return []
    interest = {t.lower() for t in cfg.get("interest_tags", [])}
    max_team = cfg.get("max_team_size", 1000)
    rows = []
    for c in items:
        name = (c.get("name") or "").strip()
        if not name or c.get("status", "Active") != "Active":
            continue
        if (c.get("team_size") or 0) > max_team:
            continue
        loc = (c.get("all_locations") or "").lower()
        if cfg.get("us_only", True) and loc and not any(k in loc for k in _US):
            continue
        rows.append((_score(c, interest), name))
    rows.sort(key=lambda r: -r[0])
    names = [n for _, n in rows[: cfg.get("max_names", 600)]]
    log.info("mined yc hiring list", companies=len(names))
    return names
