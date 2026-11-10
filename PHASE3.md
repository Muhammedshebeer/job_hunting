# Phase 3: application preparation and human review

Current policy and supervised manual workflow: [PHASE4A.md](PHASE4A.md). Minimum score is 50%; publication window is 10 days, inclusive.

For queued background discovery, recent runs, source counts, and the separate
read-only rejected-vacancy audit, see [DISCOVERY_HISTORY.md](DISCOVERY_HISTORY.md).

The Django dashboard now lives at `/`; the existing admin remains at `/admin/`.
This is a local preparation/review system. It does **not** submit applications,
send email, browse application forms, or upload the CV to employers.

## Start the dashboard in Windows PowerShell

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate
$env:DJANGO_DEBUG = '1'
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open **http://127.0.0.1:8000/** and sign in with your existing Django staff or
superuser account. The temporary `DJANGO_DEBUG` environment value enables local
development static assets without editing `.env`. The server is bound to
localhost. If you need another staff account, create/manage it using your
existing admin; this phase does not create a real user or change credentials.

One additive migration, `applications.0002_application_review_drafts`, is required.
It extends the existing Application record with draft content, snapshot fields,
preparation/approval timestamps, reviewer identity, and an approval digest. It
does not delete or rewrite existing application/job/profile records or uploaded
files. Legacy statuses (`qualified`, `prepared`, `blocked`, `needs_review`) stay
valid. The existing candidate/job uniqueness constraint is retained. No existing
migrations were edited. The migration was generated and tested in isolated
SQLite; it has **not** been applied to your existing PostgreSQL during development.

## Dashboard behavior

The responsive job workspace displays company, role, location, discovery source,
publication date in the configured UAE timezone, current CV relevance, and
qualification. Search by company/title; filter by source, selected candidate,
or **eligible / needs review / rejected / already applied**.

Qualification and CV relevance are calculated from the selected candidate's
existing extracted `resume_text`, not blindly from the globally cached
`Job.match_score`. GET requests do not update that score or create drafts.
The existing weighted skill scorer is unchanged; it is not a calibrated AI
probability. All approval decisions recheck current eligibility.

The dashboard displays **persisted Job records**. Live-discovery counters alone
are not stored vacancies: the discovery pipeline discards known rejected rows.
Consequently your recent 13 old live candidates may not appear unless they were
already saved previously. Rejected filters show stored jobs that fail current
checks (for example, jobs that have aged past 10 days). This phase does not
recreate filtered-out records or insert demonstration data into PostgreSQL.

Details show every applicable rejection/missing-information reason, the actual
stored description, verified experience bounds, and the original safe HTTPS
destination. Discovery review flags remain approval blockers; the dashboard
does not clear unknown source evidence to obtain more eligible jobs.

## Test the preparation and review workflow

1. Select your existing active candidate (normally profile 1) and open a vacancy.
2. Review source facts and all qualification checks. You may prepare a local
   draft for inspection even when the vacancy is blocked; approval remains blocked.
3. Optionally paste actual employer questions, one per line (maximum 20 extra),
   then choose **Prepare draft**. The same candidate/vacancy record is reused.
4. Read the cover letter and proposed answers. They are local and have not been sent.
5. For ambiguous questions, record a verified human clarification. Check its
   separate verification box only when the answer is independently established.
   If the answer is still unknown, leave it unchecked; approval remains blocked.
6. Choose **Save review**. Once all evidence and answers are complete, the draft
   becomes `ready_for_review`.
7. To approve, check the explicit approval confirmation and choose **Approve
   draft only**. This records the staff reviewer and time, not a submission.
8. **Skip this application** preserves the record and revokes approval. Submitted,
   failed, and skipped records are terminal in this phase and cannot be regenerated.

State vocabulary:

| Status | Meaning |
| --- | --- |
| discovered | Initial/legacy application record, not yet prepared |
| review_required | A source/profile/question requirement is unresolved, or eligibility fails |
| ready_for_review | Current draft has no unresolved blockers; human approval is still required |
| approved | Explicit human approval of this exact draft and evidence |
| submitted | Existing or separately verified submission record; no submit action is implemented |
| failed | Preserved failed record; no automatic retry or sending |
| skipped | Deliberately set aside and preserved |

Regeneration revokes approval and replaces the local generated letter and
answers. Profile/job evidence changes require regeneration. Edited content,
outdated job dates, changed URLs, or unresolved questions invalidate approval.
A stale browser review form is rejected instead of silently overwriting a newer
draft. The dashboard displays stale-approval blockers without mutating records
on a GET request.

Draft preparation does not reserve application attempts or set `attempted_at`.
Existing timestamps from legacy preparation are preserved. The existing
`run_daily_jobs` command remains a separate legacy reservation/preparation flow;
it does not grant human approval. Use the dashboard for Phase 3 review.

## Grounded cover letters and questions

The generator is deterministic and local. It uses the actual job title/company
and only skills recognized in both the existing CV text and job description.
It phrases these as references in the CV, not invented proficiency, years of
experience, qualifications, projects, metrics, achievements, or employer history.
Job text cannot instruct the generator to invent facts. There is no AI network
call or raw CV upload. Missing facts produce a blocker rather than fabricated text.

The verified employment policy supplied for this project is:

- Current UAE employment visa under the current employer sponsorship.
- A 60-day notice period; not immediately available.
- New employer sponsorship/work-permit transfer generally required.
- Exact joining date is unconfirmed and never computed from today's date.
- Current residence/employment visa does not prove unrestricted authorization
  to work for a new employer.

The policy is checked against the existing profile flags and notice period.
Conflicts stop assertive answer/letter generation until clarified. The default
unrestricted-work-authorization question always requires human clarification.
Unknown employer questions, nationality/citizenship claims, exact start dates,
and existing permits for a new employer are not guessed.

Prepared automated answers remain visible alongside any separately recorded
human clarification. A nonempty note alone does not resolve ambiguity: the
reviewer must also explicitly mark it verified. Do not check that box for
"still unknown" or "ask the employer". Approval is a human review record, not
platform permission or independent legal verification.

## Duplicate prevention and submission evidence

The original `(job, candidate)` database constraint remains. Preparation also
checks canonical platform/ATS URLs and explicitly supplied employer-requisition
identity against the candidate's existing applications, handling legacy duplicate
Job records without deleting them. A profile lock serializes preparation and
approval paths on PostgreSQL. Different unrelated URLs with no shared verified
identity cannot be reliably inferred to be the same vacancy.

Existing confirmation references, submission times and evidence are preserved.
Evidence downloads are staff-authenticated, read-only file responses; MEDIA_ROOT
is not publicly exposed. The UI has no submit/upload/mark-submitted endpoint.
The existing `confirm_submission` helper now requires a current explicitly
approved draft before recording a new confirmation. Its existing idempotent
handling of submitted records is preserved. `AuthorizedAdapter.submit()` still
raises `SubmissionNotAuthorized` for every destination.

Application status, approval and confirmation fields are read-only in admin to
keep review transitions in the authenticated service. Arbitrary direct database
editing is outside this workflow and never grants valid approval automatically.

## Automated checks

```powershell
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

The full suite currently has **203 passing tests**, including the prior 170.
New coverage checks staff access, CSRF, read-only GETs, dynamic candidate scores,
filters, safe/escaped job content, question grounding, duplicate drafts, legacy
records, clarification verification, explicit approval, stale drafts, changed
evidence, expired jobs, and the disabled submission adapter. Tests never read
`.env`, uploaded files, real mailbox credentials, or existing PostgreSQL records.

## Optional synthetic visual checks

The dashboard, job detail, and draft pages were rendered with a synthetic
candidate and jobs in an in-memory database, then checked at desktop, tablet,
and 390-pixel mobile sizes with no horizontal overflow. No demo records were
inserted into PostgreSQL. To reproduce on this Windows machine:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\render_dashboard_preview.py
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory output/dashboard_preview
```

In another PowerShell window:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe scripts\check_dashboard_layout.py
```

This optional checker uses installed Chrome from its standard Windows path and
only `127.0.0.1:8765`. The preview HTML is for layout inspection, not a working
authenticated dashboard; forms do not run the live workflow. Generated preview
HTML, synthetic browser profiles and screenshots are ignored by Git. Stop the
preview server with Ctrl+C when finished.
