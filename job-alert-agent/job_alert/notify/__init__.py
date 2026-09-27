"""Notifier factory."""
from __future__ import annotations

import re

from job_alert.config import Config, env
from job_alert.notify.base import ConfigError, Notifier, NotifyError
from job_alert.notify.console import ConsoleNotifier
from job_alert.notify.discord import DiscordNotifier
from job_alert.notify.email_sms import EmailSMSNotifier
from job_alert.notify.ntfy import NtfyNotifier

__all__ = ["ConfigError", "ConsoleNotifier", "DiscordNotifier", "Notifier", "NotifyError", "build_notifier",
           "resolve_kind"]


def resolve_kind(cfg: Config) -> str | None:
    """Which notifier to use. `auto` picks the first one whose secrets exist."""
    kind = cfg["notifier"].get("type", "auto")
    if kind != "auto":
        return kind
    if env("DISCORD_WEBHOOK_URL"):
        return "discord"
    if env("SMTP_USER") and env("SMTP_PASSWORD"):
        return "email_sms"
    if env("NTFY_TOPIC"):
        return "ntfy"
    return None


def build_notifier(cfg: Config, dry_run: bool = False) -> Notifier:
    kind = resolve_kind(cfg)
    if dry_run:
        # Preview in the format the real channel would get.
        return ConsoleNotifier(sms_like=kind in (None, "email_sms"))
    ncfg = cfg["notifier"]
    if kind is None:
        raise ConfigError("No notifier configured: set DISCORD_WEBHOOK_URL, SMTP_USER + SMTP_PASSWORD "
                          "(texts), or NTFY_TOPIC in .env or GitHub secrets")
    if kind == "discord":
        url = env("DISCORD_WEBHOOK_URL")
        if not url:
            raise ConfigError("notifier.type is 'discord' but DISCORD_WEBHOOK_URL is not set")
        if not url.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
            raise ConfigError("DISCORD_WEBHOOK_URL should start with https://discord.com/api/webhooks/")
        return DiscordNotifier(url)
    if kind == "ntfy":
        topic = env("NTFY_TOPIC") or ncfg.get("ntfy", {}).get("topic")
        if not topic:
            raise ConfigError("notifier.type is 'ntfy' but NTFY_TOPIC is not set in .env")
        return NtfyNotifier(ncfg.get("ntfy", {}).get("server", "https://ntfy.sh"), topic)
    if kind != "email_sms":
        raise ConfigError(f"Unknown notifier.type {kind!r} (expected auto, discord, email_sms, or ntfy)")

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
