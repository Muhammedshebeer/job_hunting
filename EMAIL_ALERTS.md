# Phase 2B: job-alert email discovery

Current policy and supervised manual workflow: [PHASE4A.md](PHASE4A.md). Minimum score is 50%; publication window is 10 days, inclusive.

This extends the existing Django project. It reads a specifically authorized
mailbox folder through TLS IMAP and creates `Job` records only. It never creates
`Application` records, sends email, visits job links, downloads images or
attachments, or submits applications. Greenhouse/Ashby commands still work.

## Mailbox authorization and privacy

The local configuration is prefilled with your requested Gmail address and
`authorized: false`. No mailbox has been connected. Set `authorized: true` only
after reviewing the configuration and authorizing this local importer. The
local config is ignored by Git and contains no password. `.env` is unchanged.

This implementation chooses **securely configured IMAP**, rather than Gmail
OAuth. It uses implicit TLS on port 993 with certificate validation. Gmail's
documented endpoint is [imap.gmail.com:993](https://developers.google.com/workspace/gmail/imap/imap-smtp).
The importer accepts an app password from Windows Credential Manager only;
plaintext keyring backends are refused. The password is never a command-line
argument, environment variable, JSON field, or log entry.

Gmail app passwords require 2-Step Verification and may be unavailable under
certain account policies. See [Google's app-password instructions](https://support.google.com/accounts/answer/185833).
If your account does not support an app password, do not weaken account security
or use your normal Gmail password; a Gmail OAuth adapter is not included yet.
An app password grants broader account access than this importer exercises;
its restrictions are enforced by this code, not by a Gmail folder-only scope.
Revoke the app password in your Google account when access is no longer wanted.

The importer opens only the configured label/folder in read-only mode. It searches
that folder for exact configured senders, fetches headers first, and reads bodies
only when the sender, alert-like subject, and trusted provider DMARC result match.
It uses `BODY.PEEK` so unread status is preserved. It never marks, moves,
deletes, or expunges messages. Only the configured receiving provider's
`Authentication-Results` header is trusted; Gmail uses `mx.google.com`. This
relies on that provider's header handling, not local cryptographic email verification.
Forwarded alerts whose original authentication cannot be established are skipped.

## Windows PowerShell setup

From your existing project directory:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate
notepad email_alerts.local.json
```

Migration `jobs.0002_job_alert_review` adds `requires_review`, `review_reasons`,
and `discovery_metadata`. Existing rows receive false/empty defaults; no records
or uploaded files are deleted. Normal Django commands use your existing local
database settings without printing credentials. Apply the migration before
running either discovery command, since the Job model now includes these fields.

In Gmail, subscribe to job alerts on LinkedIn, Indeed, NaukriGulf, and Bayt using
your desired roles and UAE locations. Create the label **UAEJobAlerts** and Gmail
filters that put only those job-alert messages under it. Do not use your full
inbox as the import folder.

For each platform, inspect a genuine alert's sender in Gmail's **Show original**
view. Add that exact sender address to its array in `senders`. Do not copy a
display name or a sender from an untrusted message. The importer checks domain
boundaries and DMARC against the platform. Supported domains are `linkedin.com`,
`indeed.com`, `indeedemail.com`, `naukrigulf.com`, and `bayt.com`, including their
subdomains. An unrecognized legitimate sender needs a reviewed code change;
do not add a broad wildcard or turn off sender authentication. Sender addresses
are not prepopulated because they vary and were not verified against your mailbox.

Change `authorized` to `true`, retaining the configured Gmail username, then
create a dedicated app password in your Google account and store it locally:

```powershell
.\.venv\Scripts\python.exe configure_mailbox.py --config email_alerts.local.json
```

The password prompt is hidden. This setup script does not load `.env` or connect
to Gmail. Do not paste your app password into chat, a PowerShell command, or a
source file. The credential stays associated with your Windows account.

## Dry run, then import

```powershell
.\.venv\Scripts\python.exe manage.py import_job_alerts --config email_alerts.local.json --profile-id 1 --dry-run
.\.venv\Scripts\python.exe manage.py import_job_alerts --config email_alerts.local.json --profile-id 1
```

For a temporary privacy-safe URL-recognition investigation, explicitly enable:

```powershell
.\.venv\Scripts\python.exe manage.py import_job_alerts --profile-id 1 --dry-run --diagnose-links
```

`--diagnose-links` is off by default and requires `--dry-run`. It prints grouped
`URL_SHAPE` counts with the selected platform, coarse hostname, normalized path
template, category, and reason. Job IDs, board names, unrecognized path segments,
dynamic subdomains, query parameter names/values, and tokens are not printed.
Explicit embedded URL shapes may also be counted, with an `embedded:` reason;
these diagnostic group counts are additional observations, not job totals.
No URLs are fetched, no email bodies are printed or persisted by diagnostics,
and no diagnostic file is written. Omit the flag after troubleshooting.

The live 10 November 2026 investigation found Indeed job links using the
`/rc/clk/dl` route with an explicit valid job ID; this route is now normalized
to the existing Indeed job URL. Other opaque Indeed tracking links remain
unresolved. NaukriGulf root wrappers pointed to unsupported same-platform routes
without a supported job-detail identity, so they were not treated as jobs. Its
five external links were app-store/social links, not employer careers pages.
The fix recognized one Indeed candidate from two repeated link occurrences;
the existing target-role filter rejected it. It did not invent NaukriGulf jobs.

A subsequent targeted inspection separated the NaukriGulf embedded destinations:
15 have the normalized structure `/<segment>/user/<segment>` and are reported as
`user_service_route_without_verified_job_identity`; one is under
`/mnj/<segment>/settings/mailer/<segment>` and is reported as
`mailer_settings_not_job_detail`. The user-service path's ultimate page type
remains unverified. Neither its three-segment shape nor an authenticated sender
proves it is an individual job. No new positive recognition rule was justified
by these messages. Existing explicit job-detail destinations still decode and
normalize locally, without visiting any wrapper. Search, editorial, account,
and mailer route families are distinguished in diagnostics when their static
path components provide that evidence; unknown paths stay unknown.

Indeed's observed direct route contains a syntactically valid stable job key,
which supports canonicalization and deduplication. It does not prove a job's
publication date, availability, or role eligibility. The available live heading
and anchor did not match the approved role pattern, and company/location/
description were not verified in the associated context. No fields or role
aliases were invented to make the candidate qualify.

Use your existing active profile 1 and its extracted `resume_text`. The uploaded
CV is not reopened or transmitted. Dry runs read authorized email and database
identities but write nothing. The regular command creates qualified or review
jobs, never applications. Counts are aggregate only; zero-value keys may be absent.

- `alert_messages_read`, `messages_parsed`, `jobs_extracted`: read/parse counts.
- `would_create_qualified` / `created_qualified`: jobs passing every current gate.
- `would_create_review` / `created_review`: records blocked for manual review.
- `publication_date_unverified`, `experience_unverified`, `missing_*`,
  `description_completeness_unverified`, `accepting_applications_unverified`:
  review reasons. A job may have several reasons; these are not additive job totals.
- `rejected_date`, `rejected_role`, `rejected_location`, `rejected_experience`,
  `rejected_score`: jobs excluded by known requirements.
  All five totals are printed even when zero. Each rejected candidate contributes
  to its first failed gate, in role/location/date/experience/score order;
  `source.<platform>.rejected_*` also counts the failure by platform.
- `candidate_title_evidence_label`, `candidate_title_evidence_semantic`,
  `candidate_title_evidence_heading`, `candidate_title_evidence_link`, and
  `candidate_title_evidence_missing`: title evidence categories, when present.
  `candidate_title_role_match`, `candidate_title_action_text`, and `card_anchor_*`
  report aggregate extraction categories without printing actual titles.
- `duplicates`: existing URL/platform-job-ID identities or repeated jobs in the batch.
- `messages_without_recognized_jobs`: unsupported templates or tracking-only URLs.
- `html_parts`, `plain_text_parts`, `mime_decode_failures`: MIME alternatives
  inspected. Both HTML and plain text are examined, including when HTML exists
  but contains no usable jobs. Attachments and attached messages are excluded.
- `links_examined`, `recognized_job_links`, `rejected_links`: link occurrences,
  including repeated title/Apply links and MIME alternatives. These are not
  unique job totals; navigation links can contribute to rejections.
- `decoded_tracking_links`: explicit same-platform HTTPS destination URLs
  decoded locally from supported query parameters. No requests are made.
- `unresolved_tracking_links`: selected-platform tracking links whose usable
  destination is unavailable or unsafe. Opaque tokens and unknown shorteners
  are not resolved. `rejected_external_links`, `rejected_unsafe_links`, and
  `rejected_non_job_links` provide additional rejection categories when present.
- `extracted_job_candidates`: unique platform job IDs within each message,
  before database deduplication and qualification. `candidate_missing_title`,
  `candidate_missing_company`, `candidate_missing_location`, and
  `candidate_missing_description` diagnose extraction before eligibility filters.
  `source.<platform>.<counter>` provides platform counts without printing
  addresses or message contents.
- `untrusted_or_unselected_sender`, `invalid_headers`, `oversized_or_unknown_size`:
  bodies skipped before parsing.

`lookback_days` (default 14) limits which email deliveries are searched; it is
**not** a job freshness rule. A maximum of `max_messages` (default 100) newest
matching messages is inspected per run, with a 2 MB per-message limit. Increase
the bounded limit (maximum 1000) for a backlog, otherwise repeated runs keep
checking the same newest messages. The importer intentionally has no processed
mail flag; duplicate protection makes overlap safe. Connection/protocol failures
return a sanitized error and nonzero exit status. Earlier completed job inserts
may remain on a partial failure; retry safely. No raw mailbox response is printed.

## Extraction and qualification limits

Supported extraction paths are JSON-LD `JobPosting` data in the email, ordinary
job links with nearby title/company/location fields, semantic HTML classes,
labeled text cards, compact multi-job text digests, and simple company/location
bullet layouts. Repeated image/title/Apply links and MIME alternatives are
merged for the same platform job ID. HTML and MIME are parsed locally with
the Python standard library. No platform email templates have yet been validated
against your live mailbox; real layouts can vary. Unsupported content is skipped
or retained with missing-field review reasons rather than guessed.

Recognized job-detail links are normalized by platform, and platform job IDs
are extracted. LinkedIn and Indeed tracking parameters are stripped by rebuilding
their direct job URLs. Explicit destinations embedded in selected-platform
tracking links can be percent-decoded locally only when the destination is a
recognized HTTPS job URL on that same platform or an exact supported public
Greenhouse/Ashby job-detail route. Decoding is bounded; ambiguous
or external destinations are rejected. Opaque-token and unknown redirect-only
links are **not followed**, so emails can still have zero extracted jobs.
Existing imports, tracking URLs, and repeated
source IDs are checked without overwriting existing records. Existing review
records are also preserved on repeat imports; enrich or correct them manually.
Cross-platform URLs with no common employer identity remain a deduplication limit.

Exact allowed ATS hosts are `boards.greenhouse.io`, `job-boards.greenhouse.io`,
and `jobs.ashbyhq.com`. Greenhouse requires a board plus numeric job-detail ID;
Ashby requires a board plus UUID. Host suffix lookalikes, arbitrary employer
domains, board homepages, application endpoints, and unknown redirectors are not
accepted. The email platform stays in `Job.source`; an ATS-prefixed job identity
prevents collisions with native platform IDs. Greenhouse legacy/current host
aliases normalize to one URL. ATS email links alone do not establish publication
dates, experience, description completeness, or authorization to submit.

Publication time must be explicitly identified for that specific job: a
`JobPosting.datePosted` value or a labeled posting timestamp. It must include
a timezone and fall in the inclusive rolling last 10 days. Email `Date`,
delivery time, subject-relative dates, and `INTERNALDATE` are never substitutes.
Date-only or missing values require review; a date-only value is retained in
discovery metadata without inventing a midnight timestamp. Dates are asserted by
the authenticated platform email; this importer does not independently verify a
live posting page. Metadata explicitly records that distinction. Old and future
verified timestamps are rejected.

Known locations and roles must match the existing UAE/role filters. A verified
experience minimum must be at most three, and any maximum at least one. Ambiguous
experience stays under review. The existing deterministic skill-overlap scorer
uses local CV text and requires >=50 for qualification; it is not an AI probability.
Short email card text is incomplete evidence and is always marked for review.
Structured job descriptions can be scored for qualification; an explicit future
`validThrough` is also required to establish sender-asserted open status in this
phase. Ordinary alerts will therefore frequently create review records rather
than automatically qualify jobs. This is expected behavior.

Review records are visible at `/admin/jobs/job/` using the `requires_review`
filter. They have `accepting_applications=False`. The shared `eligible()` check
blocks review records, so `run_daily_jobs` cannot prepare them. Review metadata
records only job evidence, reasons, and a hash of Message-ID; raw email bodies,
recipient details, OAuth tokens, and CV contents are not persisted. Correct
missing data using legitimate source evidence before clearing review status.
No separate submission authorization is created by importing a job.

## Run daily with Windows Task Scheduler

First test the wrapper without database writes:

```powershell
powershell.exe -NoProfile -File .\scripts\import_job_alerts_daily.ps1 -DryRun
```

After reviewing the dry-run result, the following registers a job-only importer
for 09:00 daily in **your PC's timezone**. Use UAE time on the PC if you want
09:00 Dubai time. It runs as your current Windows account while logged in,
which is required for its Credential Manager entry. Your PC must be awake.

```powershell
$taskScript = (Resolve-Path .\scripts\import_job_alerts_daily.ps1).Path
$taskArguments = '-NoProfile -File "' + $taskScript + '"'
$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArguments
$taskTrigger = New-ScheduledTaskTrigger -Daily -At '09:00'
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'UAE Job Alert Import' -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings
Get-ScheduledTaskInfo -TaskName 'UAE Job Alert Import'
```

To schedule a continuing dry run, append ` + ' -DryRun'` when assigning
`$taskArguments`. Scheduling is not performed by this implementation session.
Task exit status is available in Task Scheduler; run the wrapper interactively
to see aggregate counters. Do not schedule `run_daily_jobs` or submission tools
as part of this mailbox import task.

## Isolated validation

```powershell
.\.venv\Scripts\python.exe manage.py test jobs.tests --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Tests use synthetic messages, mocked TLS IMAP, and disposable SQLite. They never
connect to your mailbox or existing PostgreSQL, read `.env`, or access uploaded
files. Live Gmail connectivity, actual platform email templates, and PostgreSQL
concurrency need separate local verification after you configure authorization.
