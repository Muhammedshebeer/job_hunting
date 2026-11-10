# Daily discovery, supervised applications and email reports

This upgrade reuses the existing discovery adapters, PostgreSQL-backed discovery
queue/session lock, insert-only job ingestion, CV scorer, draft generator and
per-application human review. It adds a daily coordinator, private SMTP delivery,
a report outbox, application audit events and a shared 20-slot UAE-day budget.
It does not create another candidate, replace your CV, alter old migrations,
rewrite confirmation evidence, scrape websites, or submit employer forms.

**Automatic submission remains disabled.** No employer submission integration
with documented permission has been supplied. `authorized_adapter_for()` returns
no adapter, and the existing submission adapter still refuses every submission.
The Auto-Apply Eligible section therefore remains empty. Permission to read a
Greenhouse/Ashby board is not permission to submit applications. A future live
adapter requires its own documented permission, a separate explicit approval for
that application and method, idempotency, budget reservation and actual
confirmation evidence. Existing approval authorizes manual opening only.

## Upgrade and configure email (Windows PowerShell)

Stop the development web server and worker, then:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate
if (-not (Test-Path -LiteralPath .\email_delivery.local.json)) {
    Copy-Item -LiteralPath .\email_delivery.example.json -Destination .\email_delivery.local.json
}
notepad .\email_delivery.local.json
```

The two additive `0004_daily_discovery_and_reporting` migrations create the
outbox/audit/budget tables and a unique scheduled-day marker on discovery runs.
Existing job/profile/application fields and uploaded evidence are preserved.
Development tests migrated only isolated SQLite, not your live PostgreSQL.

In `email_delivery.local.json`, verify the existing example values, then set
`"authorized": true` to authorize this mailbox for outgoing reports. The fixed
recipient is **shabeersh122@gmail.com**. Keep Gmail `smtp.gmail.com`, port `587`,
`use_tls: true`, and `use_ssl: false`; alternatively an explicitly approved SMTP
provider may use authenticated TLS on 587 or implicit TLS on 465. Set its actual
username/from address and a dedicated Credential Manager service name. Extra
inline password/token fields, port 25, missing authorization, and alternate
recipients are refused. The local JSON is gitignored and contains no password.

Store the SMTP App Password with the same Windows account that will run the
scheduled task:

```powershell
.\.venv\Scripts\python.exe manage.py configure_report_email
.\.venv\Scripts\python.exe manage.py send_test_email --profile-id 1
```

The first command prompts using hidden input and stores the secret only in
Windows Credential Manager. Use a Gmail **App Password**, not your normal Gmail
password. If necessary, enable Google's 2-Step Verification and create an App
Password in your Google account settings; availability depends on account
policy. See [Google App Password guidance](https://support.google.com/accounts/answer/185833?hl=en)
and [Google SMTP/TLS settings](https://developers.google.com/workspace/gmail/imap/imap-smtp).
No password belongs in PowerShell arguments, JSON, source control, or
logs. The SMTP secret is separate from your existing IMAP secret; mailbox label,
sender allowlist, DMARC checks and read-only IMAP access are unchanged. No IMAP
credential is silently copied. Django's SMTP backend is reused with private
runtime keyring lookup. Test emails are clearly marked TEST and do not consume
the one-daily-report allowance. Each explicit test command can send a real email.

## Run the daily workflow once

Find your existing active staff ID without displaying any credentials:

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth import get_user_model; print(list(get_user_model().objects.filter(is_active=True, is_staff=True).values('id', 'username')))"
```

Use that existing ID below (the example assumes `1`):

```powershell
.\.venv\Scripts\python.exe manage.py run_daily_workflow --profile-id 1 --staff-id 1
```

This command persists one daily discovery run, prepares missing eligible drafts,
and sends the daily report. It uses the existing
`discovery_sources.phase2c.json`; enabled Greenhouse, Ashby, authorized Gmail and
any configured manual-link/CSV adapters all use their existing services. See
[PHASE2C.md](PHASE2C.md) for authorized manual-import configuration. No new board
IDs or submission endpoints are invented. Restarting the command on the same
UAE date reuses its run and report and preserves existing drafts/clarifications
and approvals. A failed/partial daily discovery is reported as such; it is not
silently rerun on every restart. You can request an additional discovery through
the existing dashboard if a source needs another check.

The coordinator never grants approval or invokes a submission adapter. It can
prepare more than 20 local drafts; preparation is not submission. Unknown work
authorization and other questions remain clarification blockers, so new drafts
may appear in Needs Review rather than Manual Apply until you verify answers.
No joining date, missing publication date, or missing experience is invented.

For a strictly read-only discovery retest before persisting anything:

```powershell
.\.venv\Scripts\python.exe manage.py discover_all_jobs --profile-id 1 --sources discovery_sources.phase2c.json --dry-run --summary --diagnose-dates
```

The shared policy remains 50% relevance and an inclusive 10-day/240-hour window,
plus approved roles, UAE locations and verified 1–3-year experience. Source date
semantics, original job URLs, review flags, rejection snapshots and cross-source
deduplication are retained. Email receipt, fetch and modification times never
become posting dates. Rejected and expired jobs do not enter the apply queues.

## Schedule separate daily discovery and reporting tasks

Use [WINDOWS_DAILY_SCHEDULE.md](WINDOWS_DAILY_SCHEDULE.md) and its installer for
07:30 discovery/preparation and 08:00 reporting in Asia/Dubai. The newer
`run_daily_job_agent` never sends email; `send_daily_job_report` supports bounded
lock retries, `--dry-run` and `--test-email`. Disable the old
`UAEJobAgentDailyWorkflow` combined schedule when switching, so it cannot send
an earlier daily report. The legacy `run_daily_workflow` command remains available
for explicit combined CLI use, but should not be scheduled for the split setup.

## Dashboard and report queue

In one terminal:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe manage.py discovery_worker --poll-seconds 3
```

In another:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
$env:DJANGO_DEBUG = '1'
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Sign in at http://127.0.0.1:8000/ using your existing staff account. The existing
Run Discovery button queues background work; its worker now prepares missing
eligible drafts too. Send Daily Report queues the outbox without SMTP inside the
HTTP request. The worker processes discovery, preparation and queued reports.
The dashboard shows Auto-Apply Eligible, Manual Apply, Submitted, Needs Review,
the last successful discovery/report timestamps, recent delivery states, and
per-application audit events. Recorded history begins at this upgrade; older
events are not fabricated. A report can also be sent synchronously from a CLI:

```powershell
.\.venv\Scripts\python.exe manage.py send_daily_report --profile-id 1
```

There is one daily report per recipient/UAE date, across all profiles. Use profile
1 consistently for this single-recipient setup. A report sent early in the day
is not resent after later discovery/approvals; those changes appear on the
dashboard and in the next day's report.

Reports contain the six requested sections, public company/title/location,
unchanged source publication date, current score, source, safe direct vacancy
URL, and workflow status. Only submitted records with a submission timestamp
and a confirmation reference or an existing private evidence file appear as
successful. Unconfirmed/opened records never gain a success timestamp or
reference in the email. Actual CV text, cover letters, private answers and
evidence contents are omitted. Legacy source-provided confirmations are
reported as stored; the email does not independently contact an employer to
verify them. A report is sent even with zero qualifying vacancies and states
that result explicitly. SMTP acceptance is not proof of inbox delivery.

## Email failures and duplicate prevention

Outbox states are queued, sending, sent, failed and delivery_unknown. Configuration
or rendering failures **before a send attempt** are failed and require explicit
retry after fixing setup:

```powershell
.\.venv\Scripts\python.exe manage.py send_daily_report --profile-id 1 --retry-failed
```

If SMTP times out, returns an uncertain outcome, or the process dies after
handoff began, the state becomes delivery_unknown. The Message-ID is retained
and displayed for provider reconciliation. Automatic retries, the daily task,
and retry-failed never resend that report. SMTP cannot guarantee exactly-once
delivery across a network/process failure; this design favors avoiding duplicate
emails and can therefore leave a report unsent when acceptance is uncertain.
Check Gmail/provider records and use tomorrow's daily report; do not delete rows
or force-reset uncertain delivery. SMTP/provider errors are not printed verbatim.

## Application limits and evidence

Opening a currently human-approved vacancy reserves a global application slot
for the UAE date, across profiles. Reopening the same application on the same
date reuses its slot. Opening does **not** mark submitted, create evidence, or
alter existing attempted/submitted timestamps. Reservations, legacy attempted
records and recorded submissions share the maximum 20 daily budget, serialized
using a database row lock. Reserved/failed slots are not recycled during the
same date. The legacy run_daily_jobs command uses this same budget.

After completing a form yourself, explicitly record a confirmation reference or
evidence through the existing supervised flow. Current eligibility/approval and
the daily budget are checked again. Never use a fake confirmation to test live
records. The system cannot control applications you submit independently outside
this workflow. No background task interprets discovery, an opened URL, or an HTTP
response as proof that an employer received an application.

## Isolated validation

```powershell
.\.venv\Scripts\python.exe manage.py test applications.tests.test_daily_automation --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Tests use temporary files, in-memory SQLite, synthetic messages/jobs and Django's
in-memory email backend. They never send applications, real emails, or touch
`.env`, Gmail, existing uploads, Credential Manager secrets or PostgreSQL data.
Boundary tests from Phase 4A remain in the full suite. Real SMTP delivery and
cross-process PostgreSQL scheduling must be checked locally after configuration.
