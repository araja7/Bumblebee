"""Command-line interface.

  python -m bumblebee run [--dry-run] [--seed]
  python -m bumblebee discover [--strategies github,seen,probe] [--max-probes N]
  python -m bumblebee companies list [--status active]
  python -m bumblebee companies add greenhouse:stripe | <job-board URL> [--name Stripe]
  python -m bumblebee companies remove greenhouse:stripe
  python -m bumblebee test-notify
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from bumblebee.agent import Agent
from bumblebee.companies import parse_company_spec, seed_companies_from_yaml
from bumblebee.config import Config, load_config
from bumblebee.db import DB
from bumblebee.discovery.lifecycle import LifecycleReport, apply_validation
from bumblebee.discovery.runner import ALL_STRATEGIES, run_discovery
from bumblebee.http import HttpClient
from bumblebee.log import get_logger, setup_logging
from bumblebee.notify import ConfigError, NotifyError, build_notifier
from bumblebee.sources import ADAPTERS

log = get_logger("cli")


def _open(cfg: Config) -> tuple[DB, HttpClient]:
    db = DB(cfg.path("db"))
    seed_companies_from_yaml(db, cfg.path("companies_seed"))
    return db, HttpClient(cfg["http"], cache_dir=cfg.path("http_cache"))


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    db, http = _open(cfg)
    try:
        notifier = build_notifier(cfg, dry_run=args.dry_run)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    if args.max_age_hours is not None:
        cfg.criteria["max_age_hours"] = args.max_age_hours
    s = Agent(cfg, db, http, notifier, dry_run=args.dry_run).run(seed=args.seed)
    print(f"\nChecked {s.companies_checked} companies ({s.companies_failed} failed, "
          f"{s.companies_skipped} skipped as low-relevance), {s.jobs_fetched} jobs fetched, "
          f"{s.candidates} passed title+location filters.")
    if s.seed_mode:
        verb = "Would seed" if args.dry_run else "Seeded"
        print(f"{verb} {s.seeded} jobs silently (no texts).")
    else:
        if s.seeded:
            print(f"Silently seeded {s.seeded} jobs from newly added companies/sources.")
        print(f"New matches: {s.new_matches} | rejected on experience: {s.rejected} | "
              f"messages {'printed' if args.dry_run else 'sent'}: {s.messages_sent} | capped: {s.capped}")
    print(f"Took {s.duration_s}s, {http.request_count} HTTP requests.")
    return 0


def cmd_discover(cfg: Config, args: argparse.Namespace) -> int:
    db, http = _open(cfg)
    strategies = tuple(x.strip() for x in args.strategies.split(",")) if args.strategies else ALL_STRATEGIES
    bad = set(strategies) - set(ALL_STRATEGIES)
    if bad:
        print(f"Unknown strategies: {', '.join(bad)} (choose from {', '.join(ALL_STRATEGIES)})", file=sys.stderr)
        return 2
    s = run_discovery(cfg, db, http, strategies=strategies, max_probes=args.max_probes)
    before, after = s.counts_before, s.counts_after
    print("\n=== Discovery summary ===")
    for strat in strategies:
        found = s.found.get(strat)
        added = s.added.get(strat, [])
        extra = f", {found} boards referenced" if found is not None else ""
        print(f"  {strat:<7} added {len(added)} new candidates{extra}")
    if s.probe:
        p = s.probe
        print(f"  probing: {p.names_tried} names, {p.probes} API probes, {p.cached_skips} cached misses skipped, "
              f"{len(p.hits)} hits" + (f" ({', '.join(f'{h.name}->{h.ats}:{h.slug}' for h in p.hits)})" if p.hits else ""))
    if s.revived:
        print(f"  revived {len(s.revived)} dead companies seen with live links")
    lc = s.lifecycle or LifecycleReport()
    print(f"  validation: {lc.checked} candidates checked -> {len(lc.promoted)} promoted to active, "
          f"{lc.still_candidate} still candidate, {len(lc.died)} dead, {lc.errors} errors")
    print(f"  companies before: {_fmt_counts(before)}")
    print(f"  companies after:  {_fmt_counts(after)}")
    print(f"  took {s.duration_s}s, {http.request_count} HTTP requests")
    return 0


def _fmt_counts(c: dict[str, int]) -> str:
    return ", ".join(f"{k}={c.get(k, 0)}" for k in ("active", "candidate", "dead", "removed"))


def cmd_companies(cfg: Config, args: argparse.Namespace) -> int:
    db, http = _open(cfg)
    if args.action == "list":
        rows = db.companies(args.status)
        print(f"{'STATUS':<10} {'ATS':<11} {'SLUG':<28} {'NAME':<26} {'JOBS':>5} {'IN-METRO':>8}  VIA")
        for c in rows:
            print(f"{c.status:<10} {c.ats:<11} {c.slug[:28]:<28} {(c.name or '')[:26]:<26} "
                  f"{'' if c.job_count is None else c.job_count:>5} "
                  f"{'' if c.relevant_count is None else c.relevant_count:>8}  {c.discovered_via or ''}")
        print(f"\n{len(rows)} companies. Totals: {_fmt_counts(db.company_counts())}")
        return 0

    try:
        ats, slug = parse_company_spec(args.spec)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if args.action == "remove":
        if db.set_company_status(ats, slug, "removed"):
            print(f"Removed {ats}:{slug} (discovery won't re-add it; `companies add` will).")
            return 0
        print(f"{ats}:{slug} isn't in the database.", file=sys.stderr)
        return 1

    # add
    res = db.add_company(ats, slug, name=args.name, discovered_via="manual")
    if res == "exists":
        existing = db.get_company(ats, slug)
        if existing and existing.status in ("removed", "dead"):
            db.set_company_status(ats, slug, "candidate")
        elif existing and existing.status == "active":
            print(f"{ats}:{slug} is already active.")
            return 0
    try:
        result = ADAPTERS[ats].fetch_board(http, slug, args.name)
    except Exception as e:  # noqa: BLE001
        result = e
    rep = LifecycleReport()
    apply_validation(db, ats, slug, result, dead_after=cfg["discovery"].get("dead_after_consecutive_404s", 3), rep=rep)
    if rep.promoted:
        print(f"Added {ats}:{slug} as active ({len(result.jobs)} open jobs). Its current jobs will be "
              f"recorded silently on the next run; only later postings trigger texts.")
        return 0
    why = f"{type(result).__name__}: {result}" if isinstance(result, Exception) else "board has 0 open jobs"
    print(f"Added {ats}:{slug} as a candidate but couldn't validate it ({why}). "
          f"Discovery will retry; 3 consecutive 404s marks it dead.")
    return 1


def cmd_test_notify(cfg: Config, args: argparse.Namespace) -> int:
    try:
        notifier = build_notifier(cfg)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    db = DB(cfg.path("db"))
    stamp = datetime.now().strftime("%H:%M")
    msg = f"Bumblebee test {stamp}: if you can read this, alerts work."
    print(f"Sending test via {notifier.describe()} ...")
    try:
        notifier.send(msg, title="Bumblebee test")
    except NotifyError as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1
    db.log_notification("test", 0, msg)
    if notifier.channel != "email_sms":
        print(f'\nSent: "{msg}"\nIt should show up within seconds. If it doesn\'t, re-check the secret/URL.')
        return 0
    print(f"""
The message was accepted for delivery: "{msg}"

Watch your phone for about 2 minutes.
  - If it arrives, you're set. Next: `python -m bumblebee run --seed`.
  - If it DOESN'T arrive, the SMTP send worked but the carrier gateway
    dropped or is throttling it. That's common now: carriers have been shutting down
    email-to-SMS (AT&T ended theirs in 2025), and Gmail-originated mail is often
    filtered. Check your Gmail inbox for a bounce, then switch to the ntfy.sh
    fallback: install the ntfy app, subscribe to a long random topic, put
    NTFY_TOPIC=<topic> in .env, set `notifier.type: ntfy` in config.yaml, and
    run test-notify again.""")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bumblebee", description="Entry-level SWE job alerts via text.")
    p.add_argument("--config", help="Path to config.yaml (default: ./config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Check sources and text new matches")
    r.add_argument("--dry-run", action="store_true", help="Print messages instead of sending; no DB writes")
    r.add_argument("--seed", action="store_true", help="Record all current matches as seen without texting")
    r.add_argument("--max-age-hours", type=float, help="Override criteria.max_age_hours (handy with --dry-run)")

    d = sub.add_parser("discover", help="Find new companies to monitor")
    d.add_argument("--strategies", help=f"Comma list from {','.join(ALL_STRATEGIES)} (default: all)")
    d.add_argument("--max-probes", type=int, help="Override discovery.probe.max_probes_per_run")

    c = sub.add_parser("companies", help="List/add/remove monitored companies")
    csub = c.add_subparsers(dest="action", required=True)
    cl = csub.add_parser("list")
    cl.add_argument("--status", choices=["candidate", "active", "dead", "removed"])
    ca = csub.add_parser("add", help="e.g. greenhouse:stripe or https://jobs.lever.co/acme")
    ca.add_argument("spec")
    ca.add_argument("--name")
    cr = csub.add_parser("remove")
    cr.add_argument("spec")

    sub.add_parser("test-notify", help="Send one test text")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    cfg = load_config(args.config)
    handlers = {"run": cmd_run, "discover": cmd_discover, "companies": cmd_companies,
                "test-notify": cmd_test_notify}
    return handlers[args.command](cfg, args)
