"""Notifier interface and message formatting."""
from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod


class NotifyError(Exception):
    pass


class ConfigError(Exception):
    pass


class Notifier(ABC):
    channel: str = "base"
    sms_like: bool = False   # True => keep singles under notifier.max_chars

    @abstractmethod
    def send(self, message: str, *, title: str | None = None, url: str | None = None,
             batch: bool = False) -> None:
        """Deliver one message. Raise NotifyError on failure."""

    def describe(self) -> str:
        return self.channel


def to_ascii(s: str) -> str:
    """SMS gateways switch to UCS-2 (70 chars/segment) on any non-GSM char,
    so fold accents and drop anything else outside ASCII."""
    s = unicodedata.normalize("NFKD", s).replace("–", "-").replace("—", "-")
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s.encode("ascii", "ignore").decode()).strip()


def _truncate(s: str, n: int) -> str:
    if n <= 3:
        return s[:max(n, 0)]
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."


MIN_HEAD = 45  # never squeeze "Company - Title" below this, even for huge URLs


def format_job_line(company: str, title: str, loc: str, url: str, max_chars: int | None = 140) -> str:
    """'Company - Title (Loc) URL'. Shrinks company/title (never the URL) to fit
    max_chars where possible; max_chars=None (MMS batches) only trims extremes."""
    company, title, loc = to_ascii(company), to_ascii(title), to_ascii(loc)
    suffix = f" ({loc}) {url}" if loc else f" {url}"
    if max_chars is None:
        return f"{_truncate(company, 40)} - {_truncate(title, 150)}{suffix}"
    budget = max(max_chars - len(suffix), MIN_HEAD)
    head = f"{company} - {title}"
    if len(head) > budget:
        company = _truncate(company, max(12, budget // 3))
        head = _truncate(f"{company} - {title}", budget)
    return head + suffix


def format_batch(lines: list[str], total: int, part: int = 1, parts: int = 1) -> str:
    header = f"{total} new SWE jobs" + (f" ({part}/{parts})" if parts > 1 else "") + ":"
    return "\n".join([header, *lines])
