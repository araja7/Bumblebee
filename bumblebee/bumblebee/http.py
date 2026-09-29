"""Polite HTTP client: timeouts, retries with exponential backoff, per-host
rate limiting, robots.txt, and ETag caching for large static files."""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from bumblebee.log import get_logger

log = get_logger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, url: str, status: int | None, message: str = ""):
        self.url, self.status = url, status
        super().__init__(f"HTTP {status} for {url} {message}".strip())


class NotFound(HttpError):
    pass


class RobotsDisallowed(HttpError):
    def __init__(self, url: str):
        super().__init__(url, None, "(disallowed by robots.txt)")


class RateLimiter:
    """Enforces a minimum interval between requests to the same host."""

    def __init__(self, default_interval: float, per_host: dict[str, float] | None = None):
        self.default = default_interval
        self.per_host = dict(per_host or {})
        self._next_ok: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def set_min_interval(self, host: str, seconds: float) -> None:
        self.per_host[host] = max(self.per_host.get(host, 0.0), seconds)

    def wait(self, host: str) -> None:
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            now = time.monotonic()
            delay = self._next_ok.get(host, 0.0) - now
            if delay > 0:
                time.sleep(delay)
            self._next_ok[host] = time.monotonic() + self.per_host.get(host, self.default)


class HttpClient:
    def __init__(self, cfg: dict[str, Any], cache_dir: Path | None = None,
                 session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = cfg.get("user_agent", "bumblebee/0.1")
        self.timeout = (cfg.get("connect_timeout", 5), cfg.get("read_timeout", 30))
        self.max_retries = cfg.get("max_retries", 3)
        self.backoff_base = cfg.get("backoff_base", 1.5)
        self.limiter = RateLimiter(cfg.get("default_min_interval", 1.0), cfg.get("host_min_interval"))
        self.respect_robots = cfg.get("respect_robots", True)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()
        self.cache_dir = cache_dir
        self.request_count = 0

    # ---- robots.txt -------------------------------------------------------
    def _robots_for(self, scheme: str, host: str) -> RobotFileParser | None:
        with self._robots_lock:
            if host in self._robots:
                return self._robots[host]
        url = f"{scheme}://{host}/robots.txt"
        parser: RobotFileParser | None
        try:
            self.limiter.wait(host)
            r = self.session.get(url, timeout=self.timeout)
            self.request_count += 1
            if r.status_code == 200:
                parser = RobotFileParser()
                parser.parse(r.text.splitlines())
                delay = parser.crawl_delay(self.session.headers["User-Agent"])
                if delay:
                    self.limiter.set_min_interval(host, float(delay))
            elif 400 <= r.status_code < 500:
                parser = None  # RFC 9309: 4xx => no restrictions
            else:
                raise HttpError(url, r.status_code)
        except (requests.RequestException, HttpError) as e:
            # Unreachable robots.txt: RFC 9309 says assume full disallow. Don't
            # cache it, so the next run tries again.
            log.warning("robots.txt unavailable; skipping host this call", host=host, error=str(e))
            disallow = RobotFileParser()
            disallow.parse(["User-agent: *", "Disallow: /"])
            return disallow
        with self._robots_lock:
            self._robots[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        parser = self._robots_for(p.scheme, p.netloc)
        return parser is None or parser.can_fetch(self.session.headers["User-Agent"], url)

    # ---- core request -----------------------------------------------------
    def get(self, url: str, *, headers: dict[str, str] | None = None,
            min_interval: float | None = None) -> requests.Response:
        """GET with robots check, rate limit, retries. Raises NotFound on 404,
        HttpError on other non-2xx/304 after retries."""
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        host = urlparse(url).netloc
        if min_interval is not None:
            self.limiter.set_min_interval(host, min_interval)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait(host)
            try:
                self.request_count += 1
                r = self.session.get(url, headers=headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                log.debug("request error", url=url, attempt=attempt, error=type(e).__name__)
            else:
                if r.status_code == 404:
                    raise NotFound(url, 404)
                if r.status_code < 400:
                    return r
                last_exc = HttpError(url, r.status_code)
                if r.status_code not in RETRY_STATUSES:
                    raise last_exc
                retry_after = _retry_after(r)
                if retry_after is not None and attempt < self.max_retries:
                    log.info("server asked us to back off", url=url, seconds=retry_after)
                    time.sleep(min(retry_after, 120))
                    continue
            if attempt < self.max_retries:
                delay = self.backoff_base * (2 ** attempt) + random.uniform(0, 0.5)
                time.sleep(delay)
        if isinstance(last_exc, HttpError):
            raise last_exc
        raise HttpError(url, None, f"({type(last_exc).__name__}: {last_exc})")

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.get(url, **kw).json()

    def get_cached_text(self, url: str) -> str:
        """Conditional GET using ETag/Last-Modified; body cached on disk.
        Used for multi-MB files (GitHub listings) that rarely change."""
        if not self.cache_dir:
            return self.get(url).text
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(url.encode()).hexdigest()[:16]
        body_path, meta_path = self.cache_dir / f"{key}.body", self.cache_dir / f"{key}.meta.json"
        headers: dict[str, str] = {}
        if body_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]
        r = self.get(url, headers=headers)
        if r.status_code == 304:
            log.debug("cache hit (304)", url=url)
            return body_path.read_text()
        body_path.write_text(r.text)
        meta_path.write_text(json.dumps({"etag": r.headers.get("ETag"),
                                         "last_modified": r.headers.get("Last-Modified")}))
        return r.text


def _retry_after(r: requests.Response) -> float | None:
    val = r.headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None
