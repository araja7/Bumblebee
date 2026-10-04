"""`discover` command: run every strategy, then validate candidates."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bumblebee.ats import BOARD_API
from bumblebee.config import Config
from bumblebee.db import DB
from bumblebee.discovery.github_lists import mine_github_lists
from bumblebee.discovery.lifecycle import LifecycleReport, validate_candidates
from bumblebee.discovery.seen_urls import mine_seen_urls
from bumblebee.discovery.yc import mine_yc
from bumblebee.discovery.slug_probe import ProbeReport, normalize_name, probe_names, read_candidate_names
from bumblebee.filters import JobFilter
from bumblebee.http import HttpClient
from bumblebee.log import get_logger
from bumblebee.sources import ADAPTERS, ATSAdapter

log = get_logger(__name__)

ALL_STRATEGIES = ("github", "yc", "seen", "probe")


@dataclass
class DiscoverySummary:
    added: dict[str, list[str]] = field(default_factory=dict)    # strategy -> ["ats:slug", ...]
    revived: list[str] = field(default_factory=list)
    found: dict[str, int] = field(default_factory=dict)          # strategy -> boards referenced
    probe: ProbeReport | None = None
    lifecycle: LifecycleReport | None = None
    counts_before: dict[str, int] = field(default_factory=dict)
    counts_after: dict[str, int] = field(default_factory=dict)
    duration_s: float = 0.0

    @property
    def total_added(self) -> int:
        return sum(len(v) for v in self.added.values())


def run_discovery(cfg: Config, db: DB, http: HttpClient, *, strategies: tuple[str, ...] = ALL_STRATEGIES,
                  max_probes: int | None = None, adapters: dict[str, ATSAdapter] | None = None
                  ) -> DiscoverySummary:
    t0 = time.monotonic()
    adapters = adapters or ADAPTERS
    dcfg = cfg["discovery"]
    s = DiscoverySummary(counts_before=db.company_counts())
    job_filter = JobFilter(cfg.criteria, cfg["locations"])
    name_hints: list[tuple[str, str | None]] = []

    def register(ats: str, slug: str, name: str | None, via: str, strategy: str) -> None:
        if ats not in adapters:
            return
        res = db.add_company(ats, slug, name=name, discovered_via=via, revive_dead=True)
        if res == "added":
            s.added.setdefault(strategy, []).append(f"{ats}:{slug}")
        elif res == "revived":
            s.revived.append(f"{ats}:{slug}")

    if "github" in strategies:
        mined = mine_github_lists(http, dcfg.get("github_lists", []), job_filter,
                                  dcfg.get("max_listing_age_days", 180))
        s.found["github"] = len(mined.refs)
        for ref, (name, via) in mined.refs.items():
            register(ref.ats, ref.slug, name, via, "github")
        hints = sorted(mined.names.values(), key=lambda h: -h.score)
        name_hints.extend((h.name, h.ats_hint) for h in hints)

    if "yc" in strategies and dcfg.get("yc", {}).get("enabled", True):
        yc_names = mine_yc(http, dcfg.get("yc", {}))
        s.found["yc"] = len(yc_names)
        name_hints = [(n, None) for n in yc_names] + name_hints

    if "seen" in strategies:
        refs = mine_seen_urls(db)
        s.found["seen"] = len(refs)
        for ref in refs:
            register(ref.ats, ref.slug, None, "seen_urls", "seen")

    prefetched = {}
    if "probe" in strategies:
        pcfg = dcfg.get("probe", {})
        interval = pcfg.get("min_interval", 0.75)
        for ats in adapters:
            http.limiter.set_min_interval(urlparse(BOARD_API[ats]).netloc, interval)
        user_names = [(n, None) for n in read_candidate_names(cfg.path("candidate_names"))]
        known: set[str] = set()
        for c in db.companies():
            known.add(c.key)
            known.add(normalize_name(c.slug))
            if c.name:
                known.add(normalize_name(c.name))
        seen_names: set[str] = set()
        ordered = []
        for name, hint in user_names + name_hints:   # yc names rank ahead of github ones
            if name.lower() not in seen_names:
                seen_names.add(name.lower())
                ordered.append((name, hint))
        s.probe = probe_names(ordered, http, db, adapters,
                              ats_order=pcfg.get("ats_order", ["greenhouse", "ashby", "lever"]),
                              max_probes=max_probes if max_probes is not None else pcfg.get("max_probes_per_run", 60),
                              negative_cache_days=pcfg.get("negative_cache_days", 30),
                              known=known)
        for hit in s.probe.hits:
            register(hit.ats, hit.slug, hit.name, f"probe:{hit.name}", "probe")
            prefetched[f"{hit.ats}:{hit.slug}"] = hit.board

    s.lifecycle = validate_candidates(db, http, adapters,
                                      max_validations=dcfg.get("max_validations_per_run", 300),
                                      dead_after=dcfg.get("dead_after_consecutive_404s", 3),
                                      prefetched=prefetched)
    s.counts_after = db.company_counts()
    s.duration_s = round(time.monotonic() - t0, 1)
    log.info("discovery complete",
             added=s.total_added, **{f"added_{k}": len(v) for k, v in s.added.items()},
             revived=len(s.revived), promoted=len(s.lifecycle.promoted), died=len(s.lifecycle.died),
             probes=s.probe.probes if s.probe else 0, probe_hits=len(s.probe.hits) if s.probe else 0,
             active=s.counts_after.get("active", 0), candidate=s.counts_after.get("candidate", 0),
             dead=s.counts_after.get("dead", 0), duration_s=s.duration_s)
    for strategy, keys in s.added.items():
        log.info("added companies", strategy=strategy, companies=" ".join(sorted(keys)))
    return s
