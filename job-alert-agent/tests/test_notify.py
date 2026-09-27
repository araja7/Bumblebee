"""Message formatting, batching, daily cap, and the notifier implementations."""
import smtplib
from datetime import timedelta
from unittest import mock

import pytest

from job_alert.agent import Agent, RunSummary
from job_alert.config import Config
from job_alert.notify import ConfigError, ConsoleNotifier, NotifyError, build_notifier
from job_alert.notify.base import format_job_line, to_ascii
from job_alert.notify.email_sms import EmailSMSNotifier
from job_alert.notify.ntfy import NtfyNotifier
from tests.conftest import NOW, FakeHttp, make_job


def test_format_short_line():
    line = format_job_line("Stripe", "Software Engineer, New Grad", "NYC",
                           "https://stripe.com/jobs/search?gh_jid=123")
    assert line == "Stripe - Software Engineer, New Grad (NYC) https://stripe.com/jobs/search?gh_jid=123"
    assert len(line) <= 140


def test_format_truncates_text_not_url():
    url = "https://job-boards.greenhouse.io/verylongcompanyname/jobs/1234567890"
    line = format_job_line("A Very Long Company Name Incorporated Worldwide",
                           "Software Engineer, Distributed Infrastructure and Developer Productivity, New Grad 2027",
                           "NYC/SF", url)
    assert len(line) <= 140
    assert line.endswith(url)
    assert "..." in line


def test_to_ascii():
    assert to_ascii("Nestlé – Café “SWE”") == 'Nestle - Cafe "SWE"'


class FailingNotifier(ConsoleNotifier):
    def send(self, *a, **kw):
        raise NotifyError("gateway down")


def _agent(cfg, db, notifier, dry_run=False):
    return Agent(cfg, db, FakeHttp(), notifier, adapters={}, feeds=[], dry_run=dry_run, now=NOW)


def _jobs(n):
    return [make_job(id=str(i), url=f"https://job-boards.greenhouse.io/acme/jobs/{i}", matched_metros=["new_york"])
            for i in range(n)]


def test_individual_messages_up_to_threshold(cfg, db):
    n = ConsoleNotifier()
    s = RunSummary(matches=_jobs(5))
    _agent(cfg, db, n)._notify(s)
    assert len(n.sent) == 5 and s.notified_jobs == 5
    assert db.notifications_today() == 5


def test_batch_when_over_threshold(cfg, db):
    cfg.raw["notifier"]["batch_max_items"] = 8
    n = ConsoleNotifier()
    s = RunSummary(matches=_jobs(6))
    _agent(cfg, db, n)._notify(s)
    assert len(n.sent) == 1
    assert n.sent[0].startswith("6 new SWE jobs:")
    assert n.sent[0].count("https://") == 6


def test_batches_split_by_max_items(cfg, db):
    cfg.raw["notifier"]["batch_max_items"] = 4
    n = ConsoleNotifier()
    _agent(cfg, db, n)._notify(RunSummary(matches=_jobs(10)))
    assert len(n.sent) == 3
    assert n.sent[0].startswith("10 new SWE jobs (1/3):")


def test_daily_cap(cfg, db):
    cfg.raw["notifier"]["daily_cap"] = 3
    n = ConsoleNotifier()
    s = RunSummary(matches=_jobs(5))
    _agent(cfg, db, n)._notify(s)
    assert len(n.sent) == 3 and s.capped == 2
    # capped jobs are recorded so they aren't retried forever
    assert db.conn.execute("SELECT COUNT(*) FROM seen_jobs WHERE status='capped'").fetchone()[0] == 2
    # the cap persists across runs the same day
    n2 = ConsoleNotifier()
    s2 = RunSummary(matches=[make_job(id="99", url="https://job-boards.greenhouse.io/acme/jobs/99")])
    _agent(cfg, db, n2)._notify(s2)
    assert n2.sent == [] and s2.capped == 1


def test_failed_send_leaves_jobs_unseen_for_retry(cfg, db):
    s = RunSummary(matches=_jobs(2))
    _agent(cfg, db, FailingNotifier())._notify(s)
    assert db.seen_count() == 0 and s.messages_sent == 0


def _cfg_with_env(cfg, monkeypatch, **env):
    for k in ("SMTP_USER", "SMTP_PASSWORD", "MY_PHONE_NUMBER", "MY_CARRIER", "SMTP_HOST", "SMTP_PORT", "NTFY_TOPIC"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return cfg


def test_build_notifier_xfinity_uses_verizon_gateway(cfg, monkeypatch):
    _cfg_with_env(cfg, monkeypatch, SMTP_USER="me@gmail.com", SMTP_PASSWORD="abcd efgh ijkl mnop",
                  MY_PHONE_NUMBER="(206) 555-1234", MY_CARRIER="Xfinity")
    n = build_notifier(cfg)
    assert isinstance(n, EmailSMSNotifier)
    assert n.sms_addr == "2065551234@vtext.com"
    assert n.mms_addr == "2065551234@vzwpix.com"
    assert n.password == "abcdefghijklmnop"


def test_build_notifier_errors(cfg, monkeypatch):
    _cfg_with_env(cfg, monkeypatch)
    with pytest.raises(ConfigError, match="Missing"):
        build_notifier(cfg)
    _cfg_with_env(cfg, monkeypatch, SMTP_USER="a", SMTP_PASSWORD="b", MY_PHONE_NUMBER="123", MY_CARRIER="xfinity")
    with pytest.raises(ConfigError, match="10-digit"):
        build_notifier(cfg)
    _cfg_with_env(cfg, monkeypatch, SMTP_USER="a", SMTP_PASSWORD="b", MY_PHONE_NUMBER="2065551234",
                  MY_CARRIER="nope")
    with pytest.raises(ConfigError, match="Unknown MY_CARRIER"):
        build_notifier(cfg)


def test_build_ntfy(cfg, monkeypatch):
    _cfg_with_env(cfg, monkeypatch, NTFY_TOPIC="secret-topic-123")
    cfg.raw["notifier"]["type"] = "ntfy"
    assert isinstance(build_notifier(cfg), NtfyNotifier)


def test_dry_run_notifier(cfg):
    assert isinstance(build_notifier(cfg, dry_run=True), ConsoleNotifier)


def test_email_sms_sends_plain_text_without_subject():
    n = EmailSMSNotifier("smtp.gmail.com", 587, "me@gmail.com", "pw", "2065551234", "vtext.com", "vzwpix.com")
    with mock.patch("smtplib.SMTP") as smtp:
        n.send("hello")
        n.send("batch", batch=True)
    server = smtp.return_value.__enter__.return_value
    server.starttls.assert_called()
    server.login.assert_called_with("me@gmail.com", "pw")
    sent = [c.args[0] for c in server.send_message.call_args_list]
    assert sent[0]["To"] == "2065551234@vtext.com" and sent[0]["Subject"] is None
    assert sent[0].get_content().strip() == "hello"
    assert sent[1]["To"] == "2065551234@vzwpix.com"


def test_email_sms_auth_error_is_clear():
    n = EmailSMSNotifier("smtp.gmail.com", 587, "me@gmail.com", "bad", "2065551234", "vtext.com")
    with mock.patch("smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")
        with pytest.raises(NotifyError, match="App Password"):
            n.send("x")


def test_email_sms_retries_transient(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    n = EmailSMSNotifier("h", 587, "u", "p", "2065551234", "vtext.com", max_attempts=3)
    with mock.patch("smtplib.SMTP") as smtp:
        server = smtp.return_value.__enter__.return_value
        server.send_message.side_effect = [smtplib.SMTPServerDisconnected("x"), None]
        n.send("hi")
    assert server.send_message.call_count == 2


def test_ntfy_posts():
    session = mock.Mock()
    session.post.return_value = mock.Mock(status_code=200)
    NtfyNotifier("https://ntfy.sh", "topic", session=session).send("msg", title="T", url="https://x")
    args, kwargs = session.post.call_args
    assert args[0] == "https://ntfy.sh/topic"
    assert kwargs["headers"]["Click"] == "https://x" and kwargs["timeout"]


def test_format_long_url_keeps_readable_head():
    url = "https://remitly.wd5.myworkdayjobs.com/en-US/Remitly_Careers/job/Seattle-Washington-United-States/AI-Native-Software-Engineer_R_106966"
    line = format_job_line("Remitly", "AI Native Software Engineer", "Seattle", url)
    assert line.startswith("Remitly - AI Native Software Engineer (Seattle) ")
    assert line.endswith(url)


def test_batch_lines_not_squeezed(cfg, db):
    n = ConsoleNotifier()
    jobs = [make_job(id=str(i), title="Software Development Engineer, Distributed Storage Systems, New Grad",
                     url=f"https://jobs.ashbyhq.com/qumulo/e1cebc33-3bfc-4c86-9581-4d558cd5f8c{i}",
                     matched_metros=["seattle"]) for i in range(6)]
    _agent(cfg, db, n)._notify(RunSummary(matches=jobs))
    assert "Software Development Engineer, Distributed Storage Systems, New Grad (Seattle)" in n.sent[0]
