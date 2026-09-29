"""Discord webhook notifier.

Create one in Discord: Server Settings -> Integrations -> Webhooks -> New
Webhook -> pick a channel -> Copy Webhook URL. Store the URL as the
DISCORD_WEBHOOK_URL secret; anyone who has it can post to that channel.
"""
from __future__ import annotations

import time

import requests

from bumblebee.log import get_logger
from bumblebee.notify.base import Notifier, NotifyError

log = get_logger(__name__)

MAX_CONTENT = 2000         # Discord's hard limit per message
SUPPRESS_EMBEDS = 1 << 2   # message flag: no link previews


class DiscordNotifier(Notifier):
    channel = "discord"
    sms_like = False

    def __init__(self, webhook_url: str, username: str = "Job Alerts", timeout: float = 15,
                 max_attempts: int = 3, session: requests.Session | None = None):
        self.url = webhook_url
        self.username = username
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = session or requests.Session()

    def describe(self) -> str:
        return "discord webhook"

    def send(self, message: str, *, title: str | None = None, url: str | None = None,
             batch: bool = False) -> None:
        if len(message) > MAX_CONTENT:
            message = message[: MAX_CONTENT - 3] + "..."
        payload = {
            "content": message,
            "username": self.username,
            "allowed_mentions": {"parse": []},   # a job title can't @everyone
        }
        if batch:
            payload["flags"] = SUPPRESS_EMBEDS  # one preview card per link is too noisy
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self.session.post(self.url, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                last = e
            else:
                if r.status_code < 300:
                    log.info("discord sent", chars=len(message))
                    return
                if r.status_code == 429:
                    wait = _retry_after(r)
                    log.info("discord rate limited", seconds=wait)
                    time.sleep(min(wait, 30))
                    continue
                last = NotifyError(f"Discord HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code in (401, 403, 404):
                    raise NotifyError(f"Discord rejected the webhook (HTTP {r.status_code}). "
                                      "Check DISCORD_WEBHOOK_URL; the webhook may have been deleted.")
                if r.status_code < 500:
                    raise last
            if attempt < self.max_attempts:
                time.sleep(2 ** attempt)
        raise NotifyError(f"Discord send failed: {last}")


def _retry_after(r: requests.Response) -> float:
    try:
        return float(r.json().get("retry_after", 1))
    except (ValueError, AttributeError):
        return float(r.headers.get("Retry-After", 1))
