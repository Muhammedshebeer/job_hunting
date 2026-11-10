# Phase 2C: broader authorized UAE discovery

Current policy and supervised manual workflow: [PHASE4A.md](PHASE4A.md). Minimum score is 50%; publication window is 10 days, inclusive.

For the audit of all 13 publication-date failures and the opt-in
`--diagnose-dates` command, see [PUBLICATION_DATE_AUDIT.md](PUBLICATION_DATE_AUDIT.md).

This phase adds an insert-only coordinator around the existing project. It
reuses `Job`, its review fields and uniqueness constraints, `CandidateProfile.resume_text`,
the skill scorer, role/location/experience checks, public API fetcher, and
authenticated email parser. No model/migration changes, CV uploads, scraping,
CAPTCHA handling, or application submissions are introduced. NaukriGulf URL
recognition and the existing email command are unchanged.

## Verified company coverage

`discovery_sources.phase2c.json` now contains eleven enabled public company boards
and the existing authorized email integration. See [COMPANY_FEED_AUDIT.md](COMPANY_FEED_AUDIT.md)
for the board audit, three additional verified feeds, and the latest combined
results. The original eight-board verification snapshot is below.
All returned HTTP 200 with JSON job lists on 10 November 2026:

| Employer | API adapter | Board | Verified jobs |
| --- | --- | --- | ---: |
| Ruya AI | Greenhouse | ruyaai | 8 |
| Sphere IT Consultants | Greenhouse | sphereit | 14 |
| Ziina | Ashby | ziina | 13 |
| AlgoDriven | Ashby | algodriven | 1 |
| Sparkland | Greenhouse | sparkland | 2 |
| qlub | Ashby | qlub | 30 |
| Asula Labs | Ashby | asula | 3 |
| MultiBank Group | Ashby | mexdigital | 14 |

The public GET methods are documented by
[Greenhouse](https://docs.greenhouse.io/job-board) and
[Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api).
Each enabled entry records its authorization reference and verification date.
This is a verified endpoint catalog, not a guarantee of fresh suitable vacancies
or comprehensive UAE coverage. Job counts change. Ashby `publishedAt` is the
last publication time and may include reposts; Greenhouse uses `first_published`.
The existing bounded Greenhouse detail fallback is preserved.

The live combined dry run with profile 1 fetched **85** jobs: **0 eligible**,
**0 review**, **85 rejected**, and no failed sources. First-failure reasons were
64 role mismatches, 10 location mismatches, and 11 old posting dates. No jobs
reached CV qualification in that snapshot; filters were not relaxed to generate
results. No records were saved. Rejection groups are mutually exclusive first
failures, so 64 + 10 + 11 = 85.

## PowerShell: live testing

Run from your existing project directory:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe scripts\verify_discovery_sources.py
.\.venv\Scripts\python.exe manage.py discover_all_jobs --sources discovery_sources.phase2c.json --profile-id 1 --dry-run
```

No additional dependencies or migrations are needed if Phase 2B is installed.
Validation includes the original 116 tests, source integration and reporting
tests, Django checks, migration consistency checks, and live combined dry runs.
The verification script does not load Django, `.env`, CV data, or the mailbox.
The Django dry run uses your existing profile and PostgreSQL identities locally
and prints aggregate counters only. It does not reopen the uploaded CV.

To customize sources without changing the checked-in catalog:

```powershell
Copy-Item discovery_sources.phase2c.json discovery_sources.phase2c.local.json
notepad discovery_sources.phase2c.local.json
.\.venv\Scripts\python.exe manage.py discover_all_jobs --sources discovery_sources.phase2c.local.json --profile-id 1 --dry-run
```

Existing `discovery_sources.local.json` and mailbox configuration are preserved.
The combined command also understands the previous list of Greenhouse/Ashby/
authorized JSON source objects. Use stable unique source names per employer.
After reviewing a dry run, remove `--dry-run` to save qualifying **and review**
jobs. Review jobs remain blocked by the existing eligibility check. This does
not authorize applications or run `run_daily_jobs`.

## Enable email in the combined run

The main manifest's `platform_email_alerts.enabled` and `.authorized` are now
`true` at your explicit request. A previously copied local manifest may still
have the entry disabled; enable that entry in the copy if you intend to run it.
The referenced `email_alerts.local.json` must independently pass all existing
authorization, sender/DMARC, folder, and credential-store checks. No password
belongs in either configuration. The existing standalone `import_job_alerts`
command continues to work.

For a compact combined run including Gmail:

```powershell
.\.venv\Scripts\python.exe manage.py discover_all_jobs --sources discovery_sources.phase2c.json --profile-id 1 --dry-run --summary
```

Omit `--summary` to see individual employer rows as well. Output always includes
`adapter.greenhouse`, `adapter.ashby`, and `platform.linkedin`, `platform.indeed`,
`platform.naukrigulf`, `platform.bayt` groups, including zero-message platforms.
Group rows are alternative views and are not added to `total.*` a second time.
`messages_read` counts authenticated email messages; email `fetched` counts
recognized candidates, so a message with only unresolved links can have zero
fetched candidates. `configured_senders` reports the number of existing sender
allowlist entries, without printing their addresses; zero means that platform
is not configured for mailbox search, rather than that its job market is empty.
Existing mailbox authorization and sender checks remain
mandatory even though the combined source entry is enabled.

The `stage.*` counters are cumulative in this order: `role_pass`, `uae_pass`,
`date_pass`, `experience_pass`, `cv_pass`, then `eligible` after all evidence.
Unknown evidence blocks the corresponding stage without inventing values.
Stages describe received candidates before duplicate removal; final eligibility
and review counters follow the actual existing validation/deduplication path.
`stage.cv_pass` alone does not establish description completeness or open status.
`audit.role_matches`, `audit.uae_matches`, `audit.uae_role_matches`, and
`audit.recent_matches` are independent counts, used to audit source relevance
without pretending that old jobs qualify. Do not add audit counts together.

The coordinator parses authenticated messages using the unchanged email parser
and feeds their candidates into the same run-local duplicate index as APIs and
manual imports. Thus a combined **dry run** also deduplicates an API job against
a later email occurrence, even though neither is saved. Put trustworthy full
feeds before sparse manual/email sources; existing jobs are never overwritten
or automatically enriched by a later duplicate.

## Manual links: no platform page fetches

Only use links/exports you are authorized to access. Use clean job-detail URLs,
not signed or opaque tracking links. A simple manual link command is:

```powershell
.\.venv\Scripts\python.exe manage.py import_job_link --platform linkedin --url 'https://www.linkedin.com/jobs/view/REPLACE_WITH_REAL_JOB_ID/' --profile-id 1 --dry-run
```

Replace the placeholder with a real numeric ID from a vacancy you viewed.
Supported platform names are `linkedin`, `indeed`, `naukrigulf`, and `bayt`.
This command never visits the URL. A bare recognized job link becomes a review
candidate with missing fields left empty. Unknown tracking/service/search URLs
are rejected, not guessed into job identities. Exact recognized Greenhouse or
Ashby detail links supplied through a platform workflow are also supported.

Optional `--metadata job_facts.json` reads a local JSON object containing only
facts you actually verified, such as `title`, `company`, `location`, `posted_at`,
`description`, integer experience bounds, `description_complete: true`, and
`accepting_applications: true`. Unknown fields must stay absent or null. The
last two booleans are evidence assertions, not convenience switches. Do not
fabricate them to force qualification. The platform and URL command arguments
determine the source and link; metadata cannot replace them.

For multiple manually entered links:

```powershell
Copy-Item manual_job_links.example.json manual_job_links.local.json
notepad manual_job_links.local.json
```

The example links are synthetic. Replace them with real links before importing.
Each JSON-list record requires `source` and `url`; other facts are optional.
Enable the `manual_links` source in your copied manifest and run the combined
dry-run command. Missing facts produce review records, not invented defaults.

## Authorized CSV workflow

```powershell
Copy-Item manual_jobs.example.csv manual_jobs.csv
notepad manual_jobs.csv
.\.venv\Scripts\python.exe manage.py import_jobs manual_jobs.csv --profile-id 1 --authorization-reference 'Personal manual entry from legitimately accessed vacancies' --dry-run
```

Use one of the four lowercase platform names in `source`. `url` is required;
source job IDs are optional if unknown. All remaining columns may be blank.
`posted_at` must be an actual timezone-aware ISO posting timestamp to qualify.
`description_complete` and `accepting_applications` accept `true`/`false`; blanks
remain unknown. Experience bounds are integer years. Any supported job link
with incomplete evidence is retained for review. Missing/invalid job URLs
cannot form a stable record and are rejected.

**The old `import_jobs` CLI now requires `--profile-id` and
`--authorization-reference`.** It previously updated matching records without
qualification. It now uses the shared insert-only qualification/review path and
supports `--dry-run`; existing jobs are never overwritten. This intentional
CLI change closes the previous unfiltered CSV route. You can instead enable the
`csv` source in a combined manifest. Relative file paths resolve against the
manifest directory, not an arbitrary task working directory.

## Other explicitly permitted employer feeds

`authorized_json` retains the normalized `{ "jobs": [...] }` contract documented
in DISCOVERY.md. The new `mapped_json` adapter maps a documented public API's
field names to the same job facts. Its disabled example in the catalog shows
`items_path` and a `fields` dictionary. Only dot-separated object paths are
supported; configuration cannot import modules, evaluate code, or execute scripts.
Missing fields remain unknown. Complete-description evidence must be explicitly
mapped from the provider (`description_complete`) or remains under review.
Publication mappings also require `publication_date_semantics` and a real
`publication_date_reference`; undocumented meanings or modification/fetch/email
timestamps remain unverified and require review rather than being treated as
recent publications. See the date audit for the exact semantics and tests.

Before enabling an endpoint, verify its actual URL, documented response shape,
permission for this use, and publication-date semantics. Set `authorized: true`
and an accurate `authorization_reference`. Credentials, signed URLs, cookies,
and tokens do not belong in configuration. The existing HTTPS-only JSON reader
refuses redirects, implicit proxies, and credential-bearing URLs, with a 20-second
timeout and 5 MB limit. APIs needing authentication or pagination need a specific
reviewed adapter; the generic mapper reads a single response and follows no links.

Developers can extend `ADAPTERS` through `register_adapter(name, loader, validator)`
in `jobs/services/discovery_adapters.py`. Registration happens in trusted Python
code. A loader yields records and must use authorized, bounded access; a validator
checks source-specific configuration. Add authorization/failure/qualification
tests for every new adapter. No new website/API access is enabled merely by
being mentioned in a feed or an email.

## Shared identity, review, and diagnostics

The shared pipeline normalizes known platform/ATS URLs and compares canonical
URLs, source job IDs, and all existing database records. Greenhouse host aliases,
known tracking parameters, and repeat occurrences are deduplicated. If two
authorized sources explicitly provide both `employer_domain` and
`employer_job_id`, that pair supplies an additional cross-source identity; company
names alone are not fuzzy-matched. Evidence is stored in existing discovery
metadata. Without a common URL, native identity, or verified employer requisition
pair, two cross-platform adverts cannot be safely merged. Do not claim universal
deduplication for unrelated board URLs.

New normalized URL inserts are protected by existing database uniqueness
constraints. Simultaneous imports with different URLs but the same optional
employer-requisition pair are not protected by a database constraint. Run one
coordinator at a time for that case; no schema change is introduced here.

The same role list, UAE locations, overlapping 1-3 year experience policy,
inclusive rolling 10 days, and >=50 weighted skill score are preserved.
Unknown publication dates, missing company/location/title/description, ambiguous
experience, incomplete descriptions, and unknown open status require review.
Known disqualifiers are rejected at the first failed gate. API publication fields
and manually supplied evidence have distinct provenance; email receipt time is
never a substitute. No midnight, experience requirement, or company is invented.

Each enabled source prints `fetched`, `eligible`, `rejected`, `review`,
`duplicates`, `created`, `would_create`, and `failed_sources`. Email `fetched`
counts extracted job candidates, with message/link counts under `adapter.*`.
`eligible` means fully qualified for job-record ingestion, not authorization to
apply. Review reasons can overlap; rejection reasons identify the first failure.
Source failures return a nonzero exit status after remaining sources are tried.
Earlier successful inserts may remain; retrying deduplicates them. Parser/server
exception details are suppressed. No CV or mailbox data is printed.

## Isolated checks

```powershell
.\.venv\Scripts\python.exe manage.py test jobs.tests --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Tests use synthetic records, mocked network/mailbox responses, and in-memory
SQLite. They do not read `.env`, uploaded files, credentials, or existing
PostgreSQL. The live dry run tests public endpoints and the existing profile
lookup, but does not validate a qualifying PostgreSQL insert or concurrency.
