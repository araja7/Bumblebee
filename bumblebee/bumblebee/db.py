"""SQLite state: seen jobs, companies, probe cache, notification log."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

from bumblebee.models import Company, Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    key          TEXT PRIMARY KEY,     -- canonical key, e.g. greenhouse:12345
    source       TEXT NOT NULL,
    external_id  TEXT,
    fingerprint  TEXT NOT NULL,        -- hash(company+title+location) fallback
    company      TEXT,
    title        TEXT,
    location     TEXT,
    url          TEXT,
    posted_at    TEXT,
    status       TEXT NOT NULL,        -- seeded | notified | rejected | capped
    reason       TEXT,
    first_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_fp ON seen_jobs(fingerprint);

CREATE TABLE IF NOT EXISTS companies (
    ats             TEXT NOT NULL,
    slug            TEXT NOT NULL,
    name            TEXT,
    status          TEXT NOT NULL DEFAULT 'candidate',  -- candidate|active|dead|removed
    discovered_via  TEXT,
    added_at        TEXT,
    last_checked    TEXT,
    job_count       INTEGER,
    relevant_count  INTEGER,
    consecutive_404 INTEGER NOT NULL DEFAULT 0,
    seeded_at       TEXT,       -- first successful fetch; jobs then were seeded silently
    last_error      TEXT,
    PRIMARY KEY (ats, slug)
);

CREATE TABLE IF NOT EXISTS probe_cache (
    ats        TEXT NOT NULL,
    slug       TEXT NOT NULL,
    result     TEXT NOT NULL,     -- hit | miss
    reason     TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (ats, slug)
);

CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at   TEXT NOT NULL,
    channel   TEXT NOT NULL,
    n_jobs    INTEGER NOT NULL,
    message   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- meta -------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))
        self.conn.commit()

    # ---- seen jobs --------------------------------------------------------
    def is_seen(self, job: Job) -> bool:
        """Seen if the canonical key exists, or if the same company+title+location
        was already recorded from a *different* source (cross-source duplicate).
        Same-source fingerprint collisions are distinct postings (e.g. two
        separate "Software Engineer" reqs) and are not treated as duplicates."""
        if self.conn.execute("SELECT 1 FROM seen_jobs WHERE key=?", (job.canonical_key,)).fetchone():
            return True
        row = self.conn.execute("SELECT 1 FROM seen_jobs WHERE fingerprint=? AND source<>? LIMIT 1",
                                (job.fingerprint, job.source)).fetchone()
        return row is not None

    def mark_seen(self, jobs: Iterable[Job], status: str, reason: str | None = None) -> int:
        n = 0
        now = iso()
        for j in jobs:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO seen_jobs
                   (key, source, external_id, fingerprint, company, title, location, url, posted_at, status, reason, first_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (j.canonical_key, j.source, j.id, j.fingerprint, j.company, j.title, j.location, j.url,
                 iso(j.posted_at) if j.posted_at else None, status, reason, now))
            n += cur.rowcount
        self.conn.commit()
        return n

    def seen_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]

    def seen_urls(self) -> Iterator[str]:
        for row in self.conn.execute("SELECT url FROM seen_jobs WHERE url IS NOT NULL"):
            yield row["url"]

    # ---- companies --------------------------------------------------------
    def get_company(self, ats: str, slug: str) -> Company | None:
        row = self.conn.execute("SELECT * FROM companies WHERE ats=? AND slug=?", (ats, slug)).fetchone()
        return Company(**dict(row)) if row else None

    def companies(self, status: str | None = None) -> list[Company]:
        if status:
            rows = self.conn.execute("SELECT * FROM companies WHERE status=? ORDER BY ats, slug", (status,))
        else:
            rows = self.conn.execute("SELECT * FROM companies ORDER BY status, ats, slug")
        return [Company(**dict(r)) for r in rows]

    def company_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM companies GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def add_company(self, ats: str, slug: str, *, name: str | None = None, status: str = "candidate",
                    discovered_via: str | None = None, revive_dead: bool = False) -> str:
        """Insert a company. Returns 'added', 'revived', or 'exists'.

        `removed` companies are never re-added by discovery. `dead` ones come
        back as candidates only when revive_dead=True (i.e. we just saw a live
        job link pointing at them)."""
        existing = self.get_company(ats, slug)
        if existing:
            if existing.status == "dead" and revive_dead:
                self.conn.execute("UPDATE companies SET status='candidate', consecutive_404=0, discovered_via=? "
                                  "WHERE ats=? AND slug=?", (discovered_via, ats, slug))
                self.conn.commit()
                return "revived"
            if name and not existing.name:
                self.conn.execute("UPDATE companies SET name=? WHERE ats=? AND slug=?", (name, ats, slug))
                self.conn.commit()
            return "exists"
        self.conn.execute("INSERT INTO companies(ats, slug, name, status, discovered_via, added_at) VALUES (?,?,?,?,?,?)",
                          (ats, slug, name, status, discovered_via, iso()))
        self.conn.commit()
        return "added"

    def set_company_status(self, ats: str, slug: str, status: str) -> bool:
        cur = self.conn.execute("UPDATE companies SET status=? WHERE ats=? AND slug=?", (status, ats, slug))
        self.conn.commit()
        return cur.rowcount > 0

    def record_fetch_ok(self, ats: str, slug: str, job_count: int, relevant_count: int | None,
                        name: str | None = None) -> None:
        self.conn.execute(
            """UPDATE companies SET last_checked=?, job_count=?, relevant_count=?, consecutive_404=0,
                   last_error=NULL, name=COALESCE(name, ?) WHERE ats=? AND slug=?""",
            (iso(), job_count, relevant_count, name, ats, slug))
        self.conn.commit()

    def record_fetch_error(self, ats: str, slug: str, error: str, *, not_found: bool,
                           dead_after: int) -> str:
        """Record a failed fetch. Returns the company's resulting status."""
        if not_found:
            self.conn.execute("UPDATE companies SET consecutive_404=consecutive_404+1, last_checked=?, last_error=? "
                              "WHERE ats=? AND slug=?", (iso(), error, ats, slug))
            self.conn.execute("UPDATE companies SET status='dead' WHERE ats=? AND slug=? AND consecutive_404>=? "
                              "AND status IN ('candidate','active')", (ats, slug, dead_after))
        else:
            self.conn.execute("UPDATE companies SET last_checked=?, last_error=? WHERE ats=? AND slug=?",
                              (iso(), error, ats, slug))
        self.conn.commit()
        c = self.get_company(ats, slug)
        return c.status if c else "missing"

    def mark_company_seeded(self, ats: str, slug: str) -> None:
        self.conn.execute("UPDATE companies SET seeded_at=? WHERE ats=? AND slug=?", (iso(), ats, slug))
        self.conn.commit()

    # ---- probe cache ------------------------------------------------------
    def probe_cached(self, ats: str, slug: str, max_age_days: int) -> str | None:
        row = self.conn.execute("SELECT result, checked_at FROM probe_cache WHERE ats=? AND slug=?",
                                (ats, slug)).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["checked_at"]) < utcnow() - timedelta(days=max_age_days):
            return None
        return row["result"]

    def cache_probe(self, ats: str, slug: str, result: str, reason: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO probe_cache(ats,slug,result,reason,checked_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(ats,slug) DO UPDATE SET result=excluded.result, reason=excluded.reason, "
            "checked_at=excluded.checked_at", (ats, slug, result, reason, iso()))
        self.conn.commit()

    # ---- notifications ----------------------------------------------------
    def log_notification(self, channel: str, n_jobs: int, message: str) -> None:
        self.conn.execute("INSERT INTO notifications(sent_at, channel, n_jobs, message) VALUES (?,?,?,?)",
                          (iso(), channel, n_jobs, message))
        self.conn.commit()

    def notifications_today(self, now: datetime | None = None) -> int:
        """Messages sent since local midnight."""
        local_now = (now or utcnow()).astimezone()
        midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.conn.execute("SELECT COUNT(*) FROM notifications WHERE sent_at >= ? AND channel <> 'test'",
                                (iso(midnight.astimezone(timezone.utc)),)).fetchone()
        return row[0]
