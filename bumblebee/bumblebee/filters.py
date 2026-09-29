"""Matching logic: titles, years of experience, locations/remote, recency."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from bumblebee.models import Job


def norm_words(s: str | None) -> str:
    """Lowercase, punctuation -> space, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def _word_re(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(norm_words(phrase))}(?![a-z0-9])")


# --------------------------------------------------------------------------
# Titles
# --------------------------------------------------------------------------
class TitleFilter:
    def __init__(self, criteria: dict[str, Any]):
        self.include = [_word_re(k) for k in criteria.get("title_include", [])]
        self.require_any = [_word_re(k) for k in criteria.get("title_require_any", [])]
        self.exclude = [(k, _word_re(k)) for k in criteria.get("title_exclude", [])]
        self.exclude_patterns = [re.compile(p, re.I) for p in criteria.get("title_exclude_patterns", [])]

    def check(self, title: str) -> str | None:
        """Return None if the title matches, else a short rejection reason."""
        t = norm_words(title)
        for kw, rx in self.exclude:
            if rx.search(t):
                return f"title excludes '{kw}'"
        for rx in self.exclude_patterns:
            if rx.search(title) or rx.search(t):
                return f"title matches exclude pattern {rx.pattern!r}"
        if not any(rx.search(t) for rx in self.include):
            return "title has no include keyword"
        if self.require_any and not any(rx.search(t) for rx in self.require_any):
            return "title is not an engineering role"
        return None


# --------------------------------------------------------------------------
# Years of experience
# --------------------------------------------------------------------------
_NUM_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}
_NUM_WORD_RE = re.compile(r"\b(" + "|".join(_NUM_WORDS) + r")\b")
_WORD_THEN_PAREN = re.compile(r"\b(" + "|".join(_NUM_WORDS) + r")\s*\(\s*(\d{1,2})\s*\+?\s*\)")

_RANGE = r"(\d{1,2})\s*(?:\+|plus|or more)?\s*(?:(?:-|to)\s*(\d{1,2})\s*)?\+?"
_YEARS = r"(?:years?|yrs?)\b"
_YEARS_PHRASE = re.compile(
    rf"(?:\b(at least|minimum of|a minimum of|minimum|min|over|more than|greater than)\s+)?{_RANGE}\s*{_YEARS}")
_EXP_THEN_YEARS = re.compile(  # "Experience: 4+ years", "experience of at least 3 years"
    rf"\bexperience\b\s*(?::|-|of|required:?|requirement:?)?\s*(?:at least|a minimum of|minimum of|minimum)?\s*"
    rf"{_RANGE}\s*{_YEARS}")
_EXP_CONTEXT = re.compile(
    r"\b(experience|exp|professional|industry|working|work|building|developing|development|"
    r"engineering|programming|coding|writing|designing|hands on|hands-on|shipping)\b")
_NOT_REQUIREMENT_BEFORE = re.compile(
    r"\b(past|last|next|within|every|per|since|founded|for over|for more than|history|anniversary)\b[^.\n]{0,12}$")
_NOT_REQUIREMENT_AFTER = re.compile(r"^\s*(ago|old|of age|warranty|in business|of history|of growth)\b")
_PREFERRED = re.compile(r"\b(prefer|preferred|preferably|nice to have|nice-to-have|bonus|a plus|"
                        r"is a plus|ideally|desired|desirable|good to have|pluses)\b")
_REQUIRED_HEADING = re.compile(r"\b(required|requirements|minimum|basic|must|qualifications|what you|"
                               r"about you|you have|who you are|you bring|looking for|you will need)\b")
_DEGREE = re.compile(r"\b(degree|bachelor|bachelors|bs|ba|b s|ms|m s|master|masters|phd|ph d)\b")


def html_to_text(s: str | None) -> str:
    """Greenhouse double-escapes HTML; unescape twice, then strip tags."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div|/ul|/tr)\s*/?>", "\n", s)
    s = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s)


def _normalize_for_years(text: str) -> str:
    t = text.lower().replace("–", "-").replace("—", "-").replace("’", "'")
    t = _WORD_THEN_PAREN.sub(lambda m: m.group(2), t)      # "three (3) years" -> "3 years"
    t = re.sub(r"\((\d{1,2})\s*\+?\)", r"\1", t)             # "(3+) years" -> "3 years"
    return _NUM_WORD_RE.sub(lambda m: _NUM_WORDS[m.group(1)], t)


def _min_years(qualifier: str | None, lo: str) -> int:
    n = int(lo)
    if qualifier in ("over", "more than", "greater than"):
        n += 1
    return n


def _years_in_line(line: str) -> list[int]:
    found: list[int] = []
    for m in _YEARS_PHRASE.finditer(line):
        before, after = line[: m.start()], line[m.end():]
        if _NOT_REQUIREMENT_BEFORE.search(before[-40:]) or _NOT_REQUIREMENT_AFTER.search(after):
            continue
        # "BS in CS or 4+ years of experience": the years are an alternative to
        # a degree a new grad has, so they don't disqualify.
        alt = re.search(r"\bor\b([^.\n]{0,20})$", before)
        if alt and _DEGREE.search(before[: alt.start()]) and not _DEGREE.search(alt.group(1)):
            continue
        window = re.split(r"[.;\n]", after, maxsplit=1)[0][:100]
        if not _EXP_CONTEXT.search(window):
            continue
        n = _min_years(m.group(1), m.group(2))
        if n <= 30:
            found.append(n)
    for m in _EXP_THEN_YEARS.finditer(line):
        n = int(m.group(1))
        if n <= 30:
            found.append(n)
    return found


def parse_required_years(description: str | None) -> int | None:
    """Best-effort minimum years of *required* experience in a description.

    Lines (or sections) marked preferred / nice-to-have / bonus are ignored.
    Within one line, alternatives ("BS + 4 yrs or MS + 2 yrs") take the
    minimum; across required lines we take the maximum. None = no mention.
    """
    if not description:
        return None
    text = _normalize_for_years(description)
    in_preferred = False
    required: list[int] = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" \t-*•:")
        if not line:
            continue
        is_heading = len(line) <= 80 and len(line.split()) <= 10
        line_pref = bool(_PREFERRED.search(line))
        if is_heading:
            if line_pref and not re.search(r"\d", line):
                in_preferred = True
                continue
            if _REQUIRED_HEADING.search(line) and not line_pref:
                in_preferred = False
        if in_preferred or line_pref:
            continue
        years = _years_in_line(line)
        if years:
            required.append(min(years))
    return max(required) if required else None


def experience_reason(description: str | None, max_years: int) -> str | None:
    yrs = parse_required_years(description)
    if yrs is not None and yrs > max_years:
        return f"requires {yrs}+ years"
    return None


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------
_LOC_SPLIT = re.compile(r"\s*(?:;|\||/|•|\n|\bor\b|&|\band\b)\s*", re.I)
_REMOTE = re.compile(r"\b(remote|work from home|wfh|anywhere|distributed)\b")
_HYBRID = re.compile(r"\bhybrid\b")


def norm_location(s: str) -> str:
    """Lowercase, keep commas (they carry city/state position), strip other punctuation."""
    s = re.sub(r"[^a-z0-9,]+", " ", s.lower())
    s = re.sub(r"\s*,\s*", ", ", s)
    return re.sub(r"\s+", " ", s).strip(" ,")


def split_locations(entries: list[str]) -> list[str]:
    out: list[str] = []
    for e in entries:
        out.extend(p for p in _LOC_SPLIT.split(e or "") if p and p.strip())
    return out


class Metro:
    def __init__(self, key: str, cfg: dict[str, Any]):
        self.key = key
        self.short = cfg.get("short", key)
        self.patterns = [re.compile(rf"(?<![a-z0-9]){re.escape(norm_location(a))}(?![a-z0-9])")
                         for a in cfg.get("aliases", [])]
        self.patterns += [re.compile(p, re.I) for p in cfg.get("patterns", [])]
        self.excludes = [re.compile(p, re.I) for p in cfg.get("exclude_patterns", [])]

    def matches(self, loc_norm: str) -> bool:
        if any(x.search(loc_norm) for x in self.excludes):
            # Only veto if the excluded phrase is the sole reason we'd match.
            stripped = loc_norm
            for x in self.excludes:
                stripped = x.sub(" ", stripped)
            return any(p.search(stripped) for p in self.patterns)
        return any(p.search(loc_norm) for p in self.patterns)


class LocationMatcher:
    def __init__(self, locations_cfg: dict[str, Any], allow_remote: bool = False):
        self.metros = [Metro(k, v or {}) for k, v in locations_cfg.items()]
        self.allow_remote = allow_remote

    def metros_for(self, text: str) -> list[str]:
        n = norm_location(text)
        return [m.key for m in self.metros if m.matches(n)]

    def check(self, job: Job) -> str | None:
        """Return None if the job has a qualifying on-site/hybrid location in a
        target metro (setting job.matched_metros), else a rejection reason."""
        if not self.allow_remote and (job.workplace_type or "").lower() == "remote":
            job.matched_metros = []
            return "fully remote role"
        matched: list[str] = []
        saw_remote_only = True
        for entry in split_locations(job.all_locations()):
            n = norm_location(entry)
            if not n:
                continue
            is_remote_entry = bool(_REMOTE.search(n)) and not _HYBRID.search(n)
            if is_remote_entry and not self.allow_remote:
                continue
            saw_remote_only = False
            for key in self.metros_for(entry):
                if key not in matched:
                    matched.append(key)
        job.matched_metros = matched
        if matched:
            return None
        if saw_remote_only and job.all_locations():
            return "remote-only location"
        return "no target location"

    def short_label(self, keys: list[str]) -> str:
        by_key = {m.key: m.short for m in self.metros}
        return "/".join(by_key.get(k, k) for k in keys)


# --------------------------------------------------------------------------
# Recency
# --------------------------------------------------------------------------
def is_recent(job: Job, max_age_hours: float, now: datetime | None = None) -> bool:
    if job.posted_at is None:
        return True  # unknown date: rely on dedupe ("first seen this run")
    now = now or datetime.now(timezone.utc)
    posted = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=timezone.utc)
    return now - posted <= timedelta(hours=max_age_hours)


# --------------------------------------------------------------------------
# Combined
# --------------------------------------------------------------------------
class JobFilter:
    def __init__(self, criteria: dict[str, Any], locations_cfg: dict[str, Any]):
        self.criteria = criteria
        self.titles = TitleFilter(criteria)
        self.locations = LocationMatcher(locations_cfg, criteria.get("allow_remote", False))
        self.max_years = criteria.get("max_years_experience", 2)
        self.max_age_hours = criteria.get("max_age_hours", 24)

    def prefilter(self, job: Job) -> str | None:
        """Cheap checks needing no extra requests. None = still a candidate."""
        if reason := self.titles.check(job.title):
            return reason
        # US-only falls out of location matching: every target metro is a US
        # city, and state-position/lookalike names are handled by patterns.
        return self.locations.check(job)

    def recent(self, job: Job, now: datetime | None = None) -> bool:
        return is_recent(job, self.max_age_hours, now)

    def experience(self, job: Job) -> str | None:
        return experience_reason(job.description, self.max_years)
