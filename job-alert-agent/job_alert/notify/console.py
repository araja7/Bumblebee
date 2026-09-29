"""--dry-run notifier: print instead of sending."""
from __future__ import annotations

import sys

from job_alert.notify.base import Notifier


class ConsoleNotifier(Notifier):
    channel = "dry_run"

    def __init__(self, stream=None, sms_like: bool = True):
        self.stream = stream or sys.stdout
        self.sms_like = sms_like
        self.sent: list[str] = []

    def send(self, message: str, *, title: str | None = None, url: str | None = None,
             batch: bool = False) -> None:
        self.sent.append(message)
        kind = "BATCH" if batch else "TEXT"
        print(f"--- [DRY RUN {kind}, {len(message)} chars] ---\n{message}", file=self.stream)
