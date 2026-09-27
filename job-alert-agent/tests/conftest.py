"""Shared fixtures. All network access is faked — tests never hit the internet."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from job_alert.config import Config, load_config
from job_alert.db import DB
from job_alert.http import NotFound
from job_alert.models import Job

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path) -> Config:
    real = load_config(ROOT / "config.yaml")
    raw = copy.deepcopy(real.raw)
    raw["paths"]["db"] = str(tmp_path / "test.db")
    raw["paths"]["http_cache"] = str(tmp_path / "cache")
    raw["paths"]["companies_seed"] = str(tmp_path / "companies.yaml")
    raw["paths"]["candidate_names"] = str(tmp_path / "candidate_names.txt")
    return Config(raw=raw, root=ROOT)


@pytest.fixture
def db() -> DB:
    d = DB(":memory:")
    yield d
    d.close()


class _Limiter:
    def set_min_interval(self, host, seconds):
        pass

    def wait(self, host):
        pass


class FakeHttp:
    """Maps exact URLs to JSON payloads, text, or exceptions."""

    def __init__(self, routes: dict | None = None):
        self.routes = dict(routes or {})
        self.calls: list[str] = []
        self.request_count = 0
        self.limiter = _Limiter()

    def _resolve(self, url):
        self.calls.append(url)
        self.request_count += 1
        if url not in self.routes:
            raise NotFound(url, 404)
        val = self.routes[url]
        if isinstance(val, Exception):
            raise val
        if callable(val):
            return val()
        return val

    def get_json(self, url, **kw):
        val = self._resolve(url)
        return json.loads(val) if isinstance(val, str) else copy.deepcopy(val)

    def get_cached_text(self, url):
        val = self._resolve(url)
        return val if isinstance(val, str) else json.dumps(val)


@pytest.fixture
def fake_http():
    return FakeHttp()


def make_job(**kw) -> Job:
    base = dict(id="1", company="Acme", title="Software Engineer", location="New York, NY",
                url="https://job-boards.greenhouse.io/acme/jobs/1", posted_at=NOW - timedelta(hours=2),
                source="greenhouse", ats="greenhouse", company_slug="acme")
    base.update(kw)
    return Job(**base)
