# Bumblebee

A job-scouting agent, named after the Autobot scout. Every hour it checks
job boards for **newly posted entry-level / new-grad software engineering
roles** in Seattle, SF, NYC, Boston, and Chicago (on-site or hybrid), and
texts you each new match via email-to-SMS. A daily discovery job keeps
growing the list of companies it watches.

```
sources (Greenhouse, Lever, Ashby per company + SimplifyJobs list)
   -> filters (title, location/remote, posted <24h, years of experience)
   -> dedupe (SQLite)
   -> notify (email-to-SMS | ntfy.sh | stdout for --dry-run)
```

## Setup

```bash
cd bumblebee
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill it in (see below)
.venv/bin/python -m pytest  # all network calls are mocked
```

### Gmail app password

Gmail won't accept your normal password over SMTP. Create an App Password:

1. Turn on 2-Step Verification: <https://myaccount.google.com/security>.
2. Go to <https://myaccount.google.com/apppasswords>, name it `bumblebee`, and click Create.
3. Copy the 16-character password into `.env` as `SMTP_PASSWORD` (spaces are fine).
4. Set `SMTP_USER` to your full Gmail address.

For another provider, change `SMTP_HOST`/`SMTP_PORT` (587 = STARTTLS, 465 = SSL).

### Carrier gateway

Set `MY_PHONE_NUMBER` (10 digits) and `MY_CARRIER` in `.env`. `MY_CARRIER` is
a key from the `carriers:` map in `config.yaml`:

| carrier | SMS gateway | MMS gateway |
|---|---|---|
| `xfinity` (Xfinity Mobile runs on Verizon) | `vtext.com` | `vzwpix.com` |
| `verizon` | `vtext.com` | `vzwpix.com` |
| `tmobile`, `mint` | `tmomail.net` | `tmomail.net` |
| `att` | `txt.att.net` | `mms.att.net` (**discontinued 2025**) |
| others | see `config.yaml` | |

Single matches go to the SMS gateway and are kept under ~140 chars:
`Company - Title (Loc) URL`. When a run finds more than 5, they're batched,
and batches go to the MMS gateway (`batch_via_mms: true`) because SMS
gateways truncate or split long messages.

> **Deliverability warning:** carriers have been shutting down or throttling
> email-to-SMS gateways. AT&T discontinued theirs in 2025, and others silently
> drop or delay mail, especially bulk-looking mail from Gmail. It may work
> fine for you, stop working later, or never arrive. That's why the notifier
> is pluggable and **ntfy.sh is built in as a fallback.**

### Discord (no email or password needed)

1. In a Discord server you own (create one if needed: "+" -> Create My Own),
   pick or create a channel such as `#job-alerts`.
2. Open the channel's settings (gear icon) -> Integrations -> Webhooks ->
   New Webhook -> **Copy Webhook URL**.
3. Save it as the `DISCORD_WEBHOOK_URL` secret, or put it in `.env` for local
   runs. Treat it like a password: anyone with the URL can post to that
   channel. If it leaks, delete the webhook and make a new one.
4. Enable mobile push for that channel in the Discord app (long-press the
   channel -> Notification Settings -> All Messages).

Discord messages aren't squeezed to 140 characters, so you get full titles,
and each single-job message shows a link preview. Batches of more than 5 turn
previews off to stay readable. `@everyone`-style mentions in job titles are
disabled.

### Verify delivery first

```bash
.venv/bin/python -m bumblebee test-notify
```

This sends one test text. "Accepted for delivery" only means Gmail took the
message. If nothing reaches your phone within a couple of minutes, check
Gmail for a bounce and switch to ntfy:

1. Install the ntfy app (iOS/Android) and subscribe to a long random topic,
   e.g. `jobs-7f3k9q2m8x`. Anyone who knows the topic can read it.
2. Put `NTFY_TOPIC=jobs-7f3k9q2m8x` in `.env`.
3. Set `notifier.type: ntfy` in `config.yaml` and rerun `test-notify`.

## Running locally

```bash
# 1. Grow the company list (daily job; first run takes ~5 min)
.venv/bin/python -m bumblebee discover

# 2. Preview what would be texted. Writes nothing to the DB.
.venv/bin/python -m bumblebee run --dry-run
.venv/bin/python -m bumblebee run --dry-run --max-age-hours 168   # wider window for a fuller preview

# 3. Seed: record everything currently open WITHOUT texting
.venv/bin/python -m bumblebee run --seed

# 4. From now on, only new postings are texted
.venv/bin/python -m bumblebee run
```

The very first real `run` on an empty DB seeds automatically, even without
`--seed`. Other commands:

```bash
python -m bumblebee companies list [--status active|candidate|dead|removed]
python -m bumblebee companies add greenhouse:stripe
python -m bumblebee companies add https://jobs.lever.co/palantir   # any board or job URL works
python -m bumblebee companies remove ashby:somecompany             # discovery won't re-add it
python -m bumblebee -v run --dry-run                                # debug logging
LOG_FORMAT=json python -m bumblebee run                             # JSON log lines
```

## Matching rules (all in `config.yaml`)

- **Title:** must contain an include keyword ("software engineer", "new grad",
  "SWE I", ...) and an engineering word ("engineer", "developer", "software",
  ...). Any exclude keyword rejects it ("senior", "II", "intern", ...).
  Matching is whole-word, so "intern" doesn't hit "Internal Tools". I added a
  few keywords beyond your list; they're marked `added:` in the config.
- **Experience:** reads the description and rejects if the *required*
  minimum is above 2 years. Lines or sections marked preferred / nice to have
  / bonus are ignored, and so is "BS or 4+ years equivalent experience".
  Greenhouse descriptions are fetched only for jobs that already passed every
  other filter.
- **Location:** the posting matches if *any* listed location is one of the
  five metros. Variants and boroughs count ("NYC", "Brooklyn", "SF", "New York,
  NY"), and look-alikes don't ("Albany, New York", "South San Francisco",
  "Manhattan Beach"). Remote-only postings are excluded, as are "Remote - NYC"
  style entries and roles whose `workplaceType` is remote. A hybrid NYC role
  that also lists "Remote (US)" still matches. Bellevue/Redmond/Kirkland count as
  Seattle, and Cambridge MA/Somerville count as Boston.
- **Recency:** posted within 24h. Jobs with no posting date pass, because
  dedupe guarantees they're new to us.

## Dedupe and seeding

`seen_jobs` in SQLite is keyed on a canonical id. Greenhouse job ids and
Lever/Ashby UUIDs are globally unique, so the same job reached through
SimplifyJobs or through the company's own board collapses to one key, e.g.
`greenhouse:5248751007`. As a fallback, a hash of company + title + metro
catches cross-source duplicates with unrelated URLs. That hash only applies
*across* sources, because one company can have two genuinely separate
"Software Engineer" reqs.

Nothing is texted for:
- anything open on the very first run, or during `run --seed`,
- a newly added company's jobs on its first fetch,
- the SimplifyJobs feed's contents on its first fetch.

Jobs rejected on experience are remembered, so their descriptions aren't
re-fetched every run. If sending fails, the jobs stay unseen and are retried
on the next run.

**Daily cap:** 30 texts per local day by default. A batch counts as one
text. When the cap is hit, the remaining matches are logged with their URLs
and marked `capped`.

## How discovery works

Companies live in the `companies` table with status
`candidate -> active -> dead` (plus `removed` for manual removals).
`companies.yaml` seeds 46 hand-verified boards as active.

`discover` runs three strategies, each in `bumblebee/discovery/`:

1. **`github_lists.py`**: downloads the SimplifyJobs New-Grad listings JSON
   (13 MB, ETag-cached) and the other public new-grad lists configured under
   `discovery.github_lists`. It extracts Greenhouse/Lever/Ashby slugs from
   every apply link active in the last 180 days, e.g.
   `job-boards.greenhouse.io/twitch/jobs/...`, `jobs.ashbyhq.com/ramp/...`,
   `boards.greenhouse.io/embed/job_app?for=airbnb`. Listings whose links
   *don't* reveal a slug (Workday, custom sites) contribute their company
   name to the probe queue instead. Those are ranked by recency and whether
   the role/location match you, and links with `?gh_jid=` or `?ashby_jid=`
   (which prove the ATS) rank highest.
2. **`seen_urls.py`**: re-mines every job URL already in the seen DB.
3. **`slug_probe.py`**: for names from `candidate_names.txt` (yours, tried
   first) plus the queue above, it generates likely slugs (`Scale AI` ->
   `scaleai`, `scale-ai`, `scale`) and probes each ATS API. A slug is
   accepted only if it returns 200 with at least one job **and** the company
   name plausibly matches: Greenhouse reports the board's company name, and
   for Lever/Ashby the name must appear in the job text. Probing runs at
   2 req/s per host, is capped per run (`max_probes_per_run`), and caches
   misses for 30 days.

New boards enter as `candidate`. `lifecycle.py` then validates up to 500 per
run (200 with at least one job -> `active`). The backlog drains
least-recently-checked first, interleaved across hosts. Any board, candidate
or active, that 404s **3 consecutive times** becomes `dead`. That also
happens during normal runs, so a vanished board retires without discovery
running. A dead board seen again in a fresh GitHub link is revived to
`candidate`. `discover` ends with a summary of what was added per strategy,
and the log lists every added `ats:slug`.

Every active company is checked every hourly run. To check companies with
**no** postings in your metros less often, raise
`scheduling.low_relevance_interval_minutes` (0 by default).

## Politeness and robustness

- Only public, no-login endpoints are used. robots.txt is honored, including
  Lever's `Crawl-delay: 1` (a 4xx robots.txt means no restrictions, per
  RFC 9309).
- Requests to each host are rate-limited. There's one worker per ATS host,
  so hosts run in parallel but each sees at most 1-2 req/s.
- Every request has connect/read timeouts. 429/5xx/connection errors retry
  with exponential backoff plus jitter, and `Retry-After` is honored.
- One failing company or source is logged and skipped; it never crashes the run.

## Deployment

### Option A: cron

```bash
crontab -e   # paste scripts/crontab.example with your path filled in
```

Simple, free, and the fastest reaction time. **But it only runs while the
machine is awake.** A sleeping laptop misses runs, so this is best on an
always-on box: a desktop, a Raspberry Pi, or a $5 VPS.

### Option B: GitHub Actions (what this repo uses)

The workflows live at the **repo root** in `.github/workflows/`, because
GitHub ignores workflow files in subdirectories. They `cd` into
`bumblebee/`:

- `check-jobs.yml`: hourly, at :22.
- `discover.yml`: daily at 11:10 UTC.
- `test-notify.yml`: manual only. It sends one test message using the repo
  secrets.

All three share a concurrency group, so they never write the DB at the same
time. The SQLite DB persists on a `state` branch, force-pushed as a single
commit each run so history doesn't bloat. The large GitHub listing files use
`actions/cache`.

**Credentials live only in GitHub secrets.** Go to Settings -> Secrets and
variables -> Actions -> New repository secret. Nothing sensitive goes in the
repo or in a local `.env`.

| secret | value |
|---|---|
| `DISCORD_WEBHOOK_URL` | Discord channel webhook (see below) |
| `SMTP_USER` + `SMTP_PASSWORD` | *or* Gmail address + app password, for texts |
| `MY_PHONE_NUMBER`, `MY_CARRIER` | only needed for texts |
| `NTFY_TOPIC` | *or* ntfy push, with no account at all |

`notifier.type: auto` uses the first of Discord, texts, and ntfy whose
secrets exist. **Until one of `DISCORD_WEBHOOK_URL` / `SMTP_PASSWORD` /
`NTFY_TOPIC` exists, check-jobs skips itself.** So alerts turn on the moment
you add one. Then:

1. Actions -> **test-notify** -> Run workflow, and check your phone.
2. The next check-jobs run seeds silently (empty DB). Texts start with the
   run after that.

Tradeoffs:
- Scheduled runs are often delayed 5-20 minutes when GitHub is busy.
- A run takes ~4 minutes with ~500 companies. That's free in a **public**
  repo, but at 24 runs/day (~2,900 minutes/month) it would exceed a private
  repo's 2,000 free minutes/month. In a public repo, `config.yaml` and the `state` branch (the
  company list plus seen jobs) are public. Secrets are not.
- GitHub disables schedules in public repos after 60 days without repo
  activity; re-enable from the Actions tab if that happens.

**Recommendation:** cron on an always-on machine is the most timely and
private option. Otherwise use GitHub Actions in a public repo: always on and
free, at the cost of schedule jitter and a public config.

## Layout

```
bumblebee/
  cli.py            commands: run, discover, companies, test-notify
  agent.py          one run: fetch -> filter -> dedupe -> notify (+ seeding, cap, batching)
  filters.py        title / years-of-experience / location+remote / recency
  db.py             SQLite: seen_jobs, companies, probe_cache, notifications, meta
  http.py           timeouts, retries+backoff, per-host rate limit, robots.txt, ETag cache
  ats.py            ATS endpoints + (ats, slug, job_id) extraction from any URL
  fetching.py       concurrent per-host board fetching
  sources/          greenhouse, lever, ashby (per company), simplify (feed)
  discovery/        github_lists, seen_urls, slug_probe, lifecycle, runner
  notify/           email_sms, ntfy, console (dry run)
tests/              unit tests, network fully mocked
```
