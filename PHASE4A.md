# Phase 4A: supervised manual applications

The daily coordinator, email reports, application audit and shared 20-slot budget
are documented in [DAILY_AUTOMATION.md](DAILY_AUTOMATION.md). Opening now reserves
a daily slot; it still never records a submission.

The existing Django dashboard and discovery pipeline now share
`jobs/qualification.py`: **minimum relevance 50%**, **publication window 10 days**.
Both endpoints are inclusive: exactly 50% and exactly 240 hours old qualify when
all other evidence passes. One microsecond older, future timestamps, date-only
values, naive timestamps, and missing publication dates never qualify as recent.
Comparisons use timezone-aware instants; UI dates use `Asia/Dubai`.

Approved developer roles, Dubai/Abu Dhabi/Sharjah/verified UAE remote locations,
1–3 years' experience, complete descriptions, verified open status, and existing
truthful employment facts remain required. Missing information requires review.
Scores still come from the existing weighted skill overlap against the actual
`CandidateProfile.resume_text`, not an AI probability or a fabricated score.
Neither model `match_score` values nor historical rejection snapshots are
automatically rewritten as eligible jobs.

Greenhouse `first_published`, Ashby `publishedAt`, mapped-feed publication
semantics, and job-specific email date evidence retain their existing meaning.
Update/fetch/email-received timestamps are never substituted for publication.
Diagnostic result keys are now `within_publication_window`,
`older_than_publication_window`, `unverified_or_outside_publication_window`,
`match_below_threshold`, and `uae_target_cv_pass` (including its `recent_` form).
The date-window diagnostic reports 240 inclusive hours. Source counts from new
managed runs record their policy in the existing run totals JSON. Old history
continues to display its original 75% / seven-day policy and is not relabeled.

## Upgrade locally in PowerShell

Stop the development web server and discovery worker, then run:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py revalidate_approvals --dry-run
.\.venv\Scripts\python.exe manage.py revalidate_approvals
```

The new `applications.0003_supervised_application_workflow` migration only adds
opening/recording timestamps and staff references, and adds `externally_opened`
to existing status choices. Existing submitted timestamps, confirmation files,
references, and terminal records are preserved. No old migration is modified.
No migration has been applied to your live PostgreSQL during implementation.

Existing approvals cannot silently survive the upgrade. Approval evidence now
includes the qualification policy, source, scoring weights, current CV text,
uploaded CV file bytes (hashed privately), candidate facts, job evidence, and
the reviewed draft. The revalidation command revokes stale approvals to
`review_required`; it never promotes jobs or creates applications. Its dry run
does not write records. Stale approvals are also excluded from Ready to Apply
on every dashboard GET, and revoked transactionally on a refused handoff.
GET requests themselves do not mutate records.

## Retest discovery with current rules

```powershell
.\.venv\Scripts\python.exe manage.py discover_all_jobs --profile-id 1 --sources discovery_sources.phase2c.json --dry-run --summary --diagnose-dates
```

This remains read-only: no jobs, history, applications, or approvals are written.
To capture a persisting discovery run and rejection audit, restart the worker:

```powershell
.\.venv\Scripts\python.exe manage.py discovery_worker --poll-seconds 3
```

In a second PowerShell window:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
$env:DJANGO_DEBUG = '1'
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Sign in at http://127.0.0.1:8000/ with your existing staff account. Select your
existing candidate and click **Run Discovery**. Only a full new qualification
through the existing services can admit newly qualifying vacancies. The
read-only rejection audit has no promotion, draft, or application action.
Existing review flags and missing source evidence remain blockers even when a
stored score is above the new threshold. Existing discovery duplicates remain
insert-only; changing policy does not manufacture new records for old rejections.

## Supervised application workflow

1. Open an eligible verified vacancy. Prepare or regenerate its local draft.
   Existing drafts and canonical/employer-ID duplicate checks are reused.
2. On the dedicated application details page (`/drafts/<id>/`), inspect company,
   role, location, publication, current calculated score, required experience,
   source, prepared cover letter, and employment answers.
3. Clarify unknown questions with independently verified facts. The default
   unrestricted-work-authorization question intentionally blocks approval until
   clarified. Current employer sponsorship, 60-day notice, non-immediate
   availability, and generally required sponsorship/permit transfer remain
   explicit. No joining date is invented.
4. Tick the explicit review/approval checkbox and approve the exact draft.
   Eligible current approvals appear in **Ready to Apply**.
5. Click **Open Application**. A CSRF-protected staff POST re-locks and reloads
   the candidate, job, and application; checks current eligibility, uploaded CV,
   provenance, draft approval, URL identity, and cross-source duplicates; then
   redirects a new browser tab to the safe stored job/application destination.
   There is no caller-supplied redirect destination. No external page is fetched
   by Django, and no forms, cookies, or credentials are handled by this agent.
6. Complete the employer application yourself. The handoff is recorded as
   `externally_opened`, with the first request time and staff identity. A redirect
   cannot prove that the browser loaded a page, and never proves submission.
   Opening/reopening creates no application attempt or submitted record.
7. Only after receiving actual employer confirmation, use **Record completed
   submission** on the same details page. Explicitly attest that you completed
   it and supply a confirmation reference **or** a PDF/PNG/JPEG evidence file
   (maximum 5 MB; extension and signature validated). Current approval checks
   must still pass. The system records a human-reported submission, the staff
   reviewer and recording time. It does not infer an employer submission time.
   Never use a fake reference to test against production records.

The first recorded confirmation is preserved on duplicate POSTs. Existing
confirmation references/files cannot be overwritten through this workflow.
Evidence downloads require staff authentication and use attachment responses;
do not expose `MEDIA_ROOT` publicly. Evidence file validation is a type/size
check, not an antivirus service. No CV text, evidence bodies, mailbox tokens,
passwords, or private exception details are printed by these actions.

External links on the draft and vacancy pages no longer bypass approval.
Unverified provenance, example/reserved/local hostnames or literal IP destinations, flagged synthetic
records, missing descriptions/dates/experience, changed evidence, expired
vacancies, and duplicate submitted/opened applications cannot enter the ready
queue. Legacy records lacking provenance stay in review; reimport/verify them
through the existing authorized discovery path rather than asserting missing
evidence. No scraping, automated submission, CAPTCHA bypass, or new credential
integration is included.

## Isolated verification commands

```powershell
.\.venv\Scripts\python.exe manage.py test applications.tests.test_supervised --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

These tests use synthetic fixtures, temporary CV/evidence files and isolated
SQLite, without `.env`, Gmail, existing uploads, or PostgreSQL records. They
exercise exact 50%/10-day boundaries, older/future/missing/naive dates, stale
policy/profile/CV-file/draft/URL evidence, approval/CSRF/staff gates, cross-source
duplicates, safe handoff, evidence validation, idempotent confirmations, and
preservation of terminal records. External URLs in fixtures are never visited.
Real employer submissions and live multi-process PostgreSQL contention have not
been exercised by the automated tests.
