"""Notifier factory."""
from __future__ import annotations

import re

from job_alert.config import Config, env
from job_alert.notify.base import ConfigError, Notifier, NotifyError
from job_alert.notify.console import ConsoleNotifier
from job_alert.notify.email_sms import EmailSMSNotifier
from job_alert.notify.ntfy import NtfyNotifier

__all__ = ["ConfigError", "ConsoleNotifier", "Notifier", "NotifyError", "build_notifier"]


def build_notifier(cfg: Config, dry_run: bool = False) -> Notifier:
    if dry_run:
        return ConsoleNotifier()
    ncfg = cfg["notifier"]
    kind = ncfg.get("type", "auto")
    if kind == "auto":
        if env("SMTP_USER") and env("SMTP_PASSWORD"):
            kind = "email_sms"
        elif env("NTFY_TOPIC"):
            kind = "ntfy"
        else:
            raise ConfigError("No notifier configured: set SMTP_USER + SMTP_PASSWORD (texts) "
                              "or NTFY_TOPIC (ntfy push) in .env or GitHub secrets")
    if kind == "ntfy":
        topic = env("NTFY_TOPIC") or ncfg.get("ntfy", {}).get("topic")
        if not topic:
            raise ConfigError("notifier.type is 'ntfy' but NTFY_TOPIC is not set in .env")
        return NtfyNotifier(ncfg.get("ntfy", {}).get("server", "https://ntfy.sh"), topic)
    if kind != "email_sms":
        raise ConfigError(f"Unknown notifier.type {kind!r} (expected auto, email_sms, or ntfy)")

    missing = [k for k in ("SMTP_USER", "SMTP_PASSWORD", "MY_PHONE_NUMBER", "MY_CARRIER") if not env(k)]
    if missing:
        raise ConfigError(f"Missing in .env: {', '.join(missing)} (copy .env.example to .env)")
    number = re.sub(r"\D", "", env("MY_PHONE_NUMBER") or "")
    if len(number) == 11 and number.startswith("1"):
        number = number[1:]
    if len(number) != 10:
        raise ConfigError("MY_PHONE_NUMBER must be a 10-digit US number")
    carrier = (env("MY_CARRIER") or "").lower().replace("-", "_").replace(" ", "_")
    gateways = cfg["carriers"].get(carrier)
    if not gateways:
        raise ConfigError(f"Unknown MY_CARRIER {carrier!r}. Options: {', '.join(sorted(cfg['carriers']))}")
    return EmailSMSNotifier(
        host=env("SMTP_HOST", "smtp.gmail.com") or "smtp.gmail.com",
        port=int(env("SMTP_PORT", "587") or 587),
        user=env("SMTP_USER") or "",
        password=(env("SMTP_PASSWORD") or "").replace(" ", ""),
        number=number,
        sms_domain=gateways["sms"],
        mms_domain=gateways.get("mms"),
        batch_via_mms=ncfg.get("batch_via_mms", True),
    )
