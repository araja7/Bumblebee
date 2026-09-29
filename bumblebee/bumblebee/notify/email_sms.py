"""Email-to-SMS via SMTP (Gmail app password by default)."""
from __future__ import annotations

import smtplib
import time
from email.message import EmailMessage

from bumblebee.log import get_logger
from bumblebee.notify.base import Notifier, NotifyError

log = get_logger(__name__)


class EmailSMSNotifier(Notifier):
    channel = "email_sms"
    sms_like = True

    def __init__(self, host: str, port: int, user: str, password: str, number: str,
                 sms_domain: str, mms_domain: str | None = None, batch_via_mms: bool = True,
                 timeout: float = 30, max_attempts: int = 3):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.number = number
        self.sms_addr = f"{number}@{sms_domain}"
        self.mms_addr = f"{number}@{mms_domain}" if mms_domain else self.sms_addr
        self.batch_via_mms = batch_via_mms
        self.timeout = timeout
        self.max_attempts = max_attempts

    def describe(self) -> str:
        return f"email_sms -> {self.sms_addr} (batches -> {self.mms_addr if self.batch_via_mms else self.sms_addr})"

    def send(self, message: str, *, title: str | None = None, url: str | None = None,
             batch: bool = False) -> None:
        to_addr = self.mms_addr if (batch and self.batch_via_mms) else self.sms_addr
        msg = EmailMessage()
        msg["From"] = self.user
        msg["To"] = to_addr
        # No Subject: gateways prepend it to the text, wasting characters.
        msg.set_content(message, charset="us-ascii")
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                self._deliver(msg)
                log.info("sms sent via email gateway", to=to_addr, chars=len(message))
                return
            except smtplib.SMTPAuthenticationError as e:
                raise NotifyError(f"SMTP login failed for {self.user}. For Gmail you need an App Password "
                                  f"(see README). Server said: {e.smtp_code} {e.smtp_error!r}") from e
            except smtplib.SMTPRecipientsRefused as e:
                raise NotifyError(f"SMTP server refused recipient {to_addr}: {e.recipients}") from e
            except (smtplib.SMTPException, OSError) as e:
                last = e
                log.warning("smtp send failed; retrying", attempt=attempt, error=f"{type(e).__name__}: {e}")
                if attempt < self.max_attempts:
                    time.sleep(2 ** attempt)
        raise NotifyError(f"SMTP send failed after {self.max_attempts} attempts: {last}")

    def _deliver(self, msg: EmailMessage) -> None:
        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout) as s:
                s.login(self.user, self.password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(self.user, self.password)
                s.send_message(msg)
