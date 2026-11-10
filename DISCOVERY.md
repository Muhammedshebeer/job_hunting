# Phase 2: authorized live discovery

Current policy and supervised manual workflow: [PHASE4A.md](PHASE4A.md). Minimum score is 50%; publication window is 10 days, inclusive.

Run commands from the `uae_job_agent` directory, using its existing virtual
environment. This feature creates qualifying `Job` records for admin review.
It does not create applications, reserve daily attempts, answer employment
questions, upload a CV, or submit anything. No migrations are required.

## Configure a source

Copy `discovery_sources.example.json` to `discovery_sources.local.json`.
Every example is disabled deliberately. Enable only sources whose discovery
method you have checked and are permitted to use.

For company careers using Greenhouse or Ashby, replace `board` with the employer's
board identifier and `company` with the employer name, then set `enabled` and
`authorized` to `true`. Retain an accurate `authorization_reference` documenting
the permitted public API or employer authorization. Use a stable unique source
name per employer board; source names form part of the job identity.

Documented GET endpoints:

- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board): list jobs,
  then fetch detail for relevant roles/locations when the list omits
  `first_published`. Never uses `updated_at` as a posting date. At most 100
  detail requests per source per run; exceeding that fails the source visibly.
- [Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api):
  uses `publishedAt`, documented as the **last publication** date; a repost can
  therefore qualify. Only explicitly listed postings are considered. Only
  `workplaceType: Remote` adds a remote marker; `isRemote` can also mean hybrid.

LinkedIn, Indeed, NaukriGulf, and Bayt do **not** have built-in scraping or native
partner-API integrations in this implementation. Each has a disabled
`authorized_json` configuration entry. Enable one only when you have a permitted
public HTTPS feed or an authorized integration that exposes the contract below.
This adapter is a normalized integration boundary, not a claim that these boards
provide this exact API. Private authenticated partner APIs require a future
source-specific adapter. CSV import remains available for authorized exports.

An authorized feed source configuration looks like:

```json
{
  "name": "indeed",
  "adapter": "authorized_json",
  "enabled": true,
  "authorized": true,
  "authorization_reference": "Reference to permission covering this feed and use",
  "url": "https://your-authorized-provider.example/jobs"
}
```

The configuration file is a JSON **list** of source objects. Do not put secrets,
access tokens, signed URLs, or passwords in it. HTTPS endpoints only; redirects,
environment proxies, and credential-bearing URLs are refused. Each request has
a 20-second timeout and a 5 MB response limit. HTTP errors, including 401, 403,
and 429, stop that source with no retries or access-control bypass. Other sources
continue. A partial failure returns a nonzero command exit code; rerunning is
safe because existing identities are skipped.

## Authorized JSON feed contract

Return `Content-Type: application/json` with an object containing a `jobs` list:

```json
{
  "jobs": [{
    "source_id": "stable-employer-job-id",
    "company": "Employer name",
    "title": "Python Django Developer",
    "location": "Dubai, UAE",
    "url": "https://careers.example.com/jobs/stable-employer-job-id",
    "posted_at": "2026-11-09T08:00:00+04:00",
    "min_experience": 1,
    "max_experience": 3,
    "description": "Python Django PostgreSQL. Requires 1-3 years of experience.",
    "accepting_applications": true
  }]
}
```

The sample timestamp is illustrative; use the source's actual verified posting
time. All eligible jobs must fall in the inclusive rolling interval
`now - 10 days <= posted_at <= now`. Naive timestamps, date-only values, relative
dates, missing dates, future dates, and update timestamps are not substitutes.

Only target titles and Dubai, Abu Dhabi, Sharjah, or explicitly UAE remote
locations qualify. Worldwide/EMEA remote alone is insufficient to establish UAE
eligibility. Ashby secondary locations are not inferred; the primary location
must qualify in this phase.

Provide integer experience bounds when available. Otherwise, conservative
description extraction recognizes explicit phrases such as `1-3 years of
experience` or `2+ years experience`. Missing, malformed, or conflicting bounds
are skipped for review; no requirements are invented. A verified minimum must
be at most three years, and any maximum must be at least one year. This means
requirements overlap the candidate's 1-3 year target, rather than demanding that
every employer's upper bound is three. The existing weighted CV skill scorer
must return at least 50; this is an explainable skill-overlap percentage, not a
validated AI probability.

## Run locally

Ensure your existing active CandidateProfile has its existing extracted
`resume_text` populated. Discovery reads that text locally; it does not reopen
or alter the uploaded CV. Replace profile ID `1` as needed.

```powershell
Copy-Item discovery_sources.example.json discovery_sources.local.json
# Edit discovery_sources.local.json as described above.
.\.venv\Scripts\python.exe manage.py discover_jobs --sources discovery_sources.local.json --profile-id 1 --dry-run
.\.venv\Scripts\python.exe manage.py discover_jobs --sources discovery_sources.local.json --profile-id 1
```

The first discovery command fetches and filters live data but performs no database
writes. The second creates qualifying jobs. Both report counts for rejected dates,
locations, roles, unknown experience, low scores, duplicates, and source failures.
Aggregate diagnostics include `fetched`, `recent` (verified last 10 days),
`uae_location` (all posting ages), `recent_uae`, and
`recent_uae_target_cv_pass` (recent UAE target roles with a CV score of at least
50, before experience/open-status/identity checks). `uae_target_cv_pass` and
`uae_target_experience_unverified` diagnose UAE target jobs of **all ages**;
they never override the 10-day eligibility rule. These groups overlap and
must not be added together. `source.<name>.<metric>` provides per-source counts.
Missing, invalid, future, and older publication dates are counted separately.
`would_create` reports qualifying, nonduplicate jobs in a dry run; if absent,
it is zero. Metrics reflect normalized rows yielded by the adapters; if a
Greenhouse list lacks publication dates, dates are fetched only for target
roles/locations, so unrelated jobs can remain undated in these diagnostics.
View results in `/admin/jobs/job/`. Repeating the command should report duplicates.
Your usual Django runtime still uses your existing local configuration and
PostgreSQL connection; no configuration changes are needed in `.env`.

Existing `(source, source_id)` or canonical URL identities are skipped without
overwriting existing job/application records. Known tracking parameters are
removed for comparison, including on legacy CSV records, while identity query
parameters such as `jk` and `jobId` are retained. Existing uniqueness constraints
protect concurrent inserts. Different unrelated board URLs without a shared
employer identity cannot yet be deduplicated reliably; employer requisition
mapping remains required before enabling external submission.

The existing `run_daily_jobs` command remains separate and prepares applications
only. This phase does not harden its concurrency behavior or authorize submission.
Cover letters, employment-question gates, Playwright submission adapters,
confirmation capture, and scheduling belong to later phases. API discovery
needs neither Playwright nor Ollama; existing PyMuPDF CV parsing stays intact.

## Isolated checks and tests

```powershell
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py test jobs.tests --settings=config.test_settings
```

The test settings never import normal settings, load `.env`, connect to the
existing PostgreSQL database, or read uploaded files. Tests use a disposable
in-memory SQLite database and mocked network responses. They validate boundary
dates, adapters, authorization configuration, role/location/experience/score
filters, duplicate handling, preserved records, dry runs, and failure handling.
Real PostgreSQL concurrency and live employer endpoints are not covered by
these isolated tests. Run the configured live dry run locally to verify your
chosen employer boards.
