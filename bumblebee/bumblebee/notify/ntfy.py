"""ntfy.sh push notifications — the fallback when carrier gateways drop mail.

Install the ntfy app, subscribe to your (secret, unguessable) topic, set
notifier.type: ntfy in config.yaml and NTFY_TOPIC in .env.
"""
from __future__ import annotations

import time

import requests

from bumblebee.log import get_logger
from bumblebee.notify.base import Notifier, NotifyError

log = get_logger(__name__)


class NtfyNotifier(Notifier):
    channel = "ntfy"

    def __init__(self, server: str, topic: str, timeout: float = 15, max_attempts: int = 3,
                 session: requests.Session | None = None):
        self.url = f"{server.rstrip('/')}/{topic}"
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = session or requests.Session()

    def describe(self) -> str:
        return f"ntfy -> {self.url.rsplit('/', 1)[0]}/<topic>"

    def send(self, message: str, *, title: str | None = None, url: str | None = None,
             batch: bool = False) -> None:
        headers = {"Tags": "briefcase"}
        if title:
            headers["Title"] = title.encode("ascii", "ignore").decode()
        if url and not batch:
            headers["Click"] = url
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self.session.post(self.url, data=message.encode("utf-8"), headers=headers, timeout=self.timeout)
                if r.status_code < 300:
                    log.info("ntfy sent", chars=len(message))
                    return
                last = NotifyError(f"ntfy HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code < 500 and r.status_code != 429:
                    raise last
            except requests.RequestException as e:
                last = e
            if attempt < self.max_attempts:
                time.sleep(2 ** attempt)
        raise NotifyError(f"ntfy send failed: {last}")
