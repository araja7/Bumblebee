"""Strategy: guess ATS slugs from company names and probe the public APIs.

A slug is accepted only if the API returns 200 with >= 1 job AND the board
plausibly belongs to that company (Greenhouse reports the board's company
name; for Lever/Ashby we look for the name in job descriptions). Misses are
cached for 30 days so we never hammer the same dead guess.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from bumblebee.db import DB
from bumblebee.http import HttpClient, NotFound, RobotsDisallowed
from bumblebee.log import get_logger
from bumblebee.sources import ATSAdapter, BoardResult

log = get_logger(__name__)

_LEGAL = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc",
          "gmbh", "sa", "ag", "lp", "llp", "the", "pbc"}
_GENERIC = {"technologies", "technology", "tech", "labs", "lab", "ai", "hq", "software", "systems", "group",
            "holdings", "global", "usa", "us", "io", "app", "com", "inc", "health", "financial", "bank",
            "solutions", "services", "platforms", "platform", "digital", "network", "networks"}
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9\-]{1,60}$")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\([^)]*\)", " ", s)          # drop parentheticals: "Block (Square)"
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"\.(com|io|ai|co|net|org)\b", r" \1", s)
    return s


def generate_slugs(name: str, max_variants: int = 4) -> list[str]:
    """Likely ATS slugs for a company name, most likely first.

    "Scale AI, Inc."  -> ["scaleai", "scale-ai", "scale"]
    "Two Sigma"       -> ["twosigma", "two-sigma"]
    "Macy's"          -> ["macys"]
    """
    words = re.findall(r"[a-z0-9]+", _fold(name))
    full = [w for w in words if w not in _LEGAL]
    core = [w for w in full if w not in _GENERIC] or full
    variants = ["".join(full), "-".join(full), "".join(core), "-".join(core)]
    # "and" often gets dropped: "Johnson & Johnson" -> "johnsonjohnson"
    if "and" in full:
        no_and = [w for w in full if w != "and"]
        variants += ["".join(no_and), "-".join(no_and)]
    out: list[str] = []
    for v in variants:
        if v and _SLUG_OK.match(v) and v not in out:
            out.append(v)
    return out[:max_variants]


def normalize_name(s: str | None) -> str:
    words = re.findall(r"[a-z0-9]+", _fold(s or ""))
    return "".join(w for w in words if w not in _LEGAL)


def name_plausible(expected: str, board: BoardResult) -> bool:
    """Does this board plausibly belong to `expected`?"""
    want = normalize_name(expected)
    if not want:
        return False
    if board.company_name:
        got = normalize_name(board.company_name)
        if not got:
            return False
        if want == got or (min(len(want), len(got)) >= 4 and (want in got or got in want)):
            return True
        return difflib.SequenceMatcher(None, want, got).ratio() >= 0.8
    # No reported name (Lever/Ashby): look for the name in the job text.
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(expected.lower().strip())}(?![a-z0-9])")
    for text in board.name_evidence:
        low = (text or "").lower()
        if pattern.search(low) or (len(want) >= 5 and want in re.sub(r"[^a-z0-9]", "", low)):
            return True
    return False


@dataclass
class ProbeHit:
    name: str
    ats: str
    slug: str
    board: BoardResult


@dataclass
class ProbeReport:
    probes: int = 0
    cached_skips: int = 0
    names_tried: int = 0
    hits: list[ProbeHit] = field(default_factory=list)
    errors: int = 0


def probe_names(names: list[tuple[str, str | None]], http: HttpClient, db: DB,
                adapters: dict[str, ATSAdapter], *, ats_order: list[str], max_probes: int,
                negative_cache_days: int = 30, known: set[str] | None = None) -> ProbeReport:
    """names: [(company name, ats hint or None)], highest priority first.
    known: normalized names/slugs already monitored (skipped)."""
    rep = ProbeReport()
    known = known or set()
    for name, hint in names:
        if rep.probes >= max_probes:
            break
        if normalize_name(name) in known:
            continue
        slugs = generate_slugs(name)
        if not slugs:
            continue
        rep.names_tried += 1
        order = [hint] if hint in adapters else [a for a in ats_order if a in adapters]
        found = False
        for slug in slugs:
            if found:
                break
            for ats in order:
                if f"{ats}:{slug}" in known:
                    found = True
                    break
                cached = db.probe_cached(ats, slug, negative_cache_days)
                if cached is not None:
                    rep.cached_skips += 1
                    continue
                if rep.probes >= max_probes:
                    return rep
                rep.probes += 1
                try:
                    board = adapters[ats].fetch_board(http, slug, name)
                except NotFound:
                    db.cache_probe(ats, slug, "miss", "404")
                    continue
                except RobotsDisallowed:
                    continue
                except Exception as e:  # noqa: BLE001 - transient; don't cache
                    rep.errors += 1
                    log.debug("probe error", ats=ats, slug=slug, error=str(e))
                    continue
                if not board.jobs:
                    db.cache_probe(ats, slug, "miss", "no jobs")
                    continue
                if not name_plausible(name, board):
                    db.cache_probe(ats, slug, "miss", f"name mismatch ({board.company_name or 'no name'})")
                    log.debug("probe name mismatch", name=name, ats=ats, slug=slug, board=board.company_name)
                    continue
                db.cache_probe(ats, slug, "hit", name)
                rep.hits.append(ProbeHit(name, ats, slug, board))
                log.info("probe hit", name=name, ats=ats, slug=slug, jobs=len(board.jobs))
                found = True
                break
    return rep


def read_candidate_names(path) -> list[str]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
