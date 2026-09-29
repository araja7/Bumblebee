"""Fetch many company boards concurrently: one worker per ATS host, so each
host still sees at most one request per its rate-limit interval."""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from bumblebee.http import HttpClient
from bumblebee.models import Company
from bumblebee.sources import ATSAdapter, BoardResult

FetchOutcome = tuple[Company, BoardResult | Exception]


def fetch_boards(http: HttpClient, adapters: dict[str, ATSAdapter], companies: list[Company],
                 fetch: Callable[[ATSAdapter, HttpClient, Company], BoardResult] | None = None
                 ) -> list[FetchOutcome]:
    fetch = fetch or (lambda a, h, c: a.fetch_board(h, c.slug, c.name))
    groups: dict[str, list[Company]] = defaultdict(list)
    for c in companies:
        if c.ats in adapters:
            groups[c.ats].append(c)

    def worker(ats: str) -> list[FetchOutcome]:
        out: list[FetchOutcome] = []
        for c in groups[ats]:
            try:
                out.append((c, fetch(adapters[ats], http, c)))
            except Exception as e:  # noqa: BLE001 - one bad board must not kill the run
                out.append((c, e))
        return out

    results: list[FetchOutcome] = []
    if not groups:
        return results
    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        for chunk in pool.map(worker, list(groups)):
            results.extend(chunk)
    return results
