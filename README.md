# UAE Job Agent — safe local Django starter

This is a **runnable starter**, not a fully connected auto-apply service. It supports authorized live JSON discovery, CSV imports from authorized sources, CV parsing, deterministic skill scoring, 3-day filtering with a minimum 25% CV match, a 20-attempt daily reservation cap, duplicate protection, Django admin review, and confirmation records. It **does not** scrape job boards or submit to external websites.

## Windows PowerShell setup

Discovery accepts all locations from the configured feeds, with UAE jobs first.
The dashboard and daily report separate UAE jobs from other locations. Matching
uses the same 3-day/25% criteria worldwide; automatic local draft preparation
covers both groups. Actual automatic submission still needs an integration.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env with your local PostgreSQL credentials
# Create PostgreSQL database: CREATE DATABASE uae_job_agent;
python manage.py makemigrations jobs profiles applications
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/admin/ . Add one CandidateProfile with your latest PDF resume. The PDF is stored locally under media/private/resumes; do not expose MEDIA_ROOT publicly.

## Import jobs

For the split **07:30 discovery / 08:00 report** Dubai schedules, dry runs and
the Task Scheduler installer, see [WINDOWS_DAILY_SCHEDULE.md](WINDOWS_DAILY_SCHEDULE.md).

For scheduled discovery, automatic local draft preparation, private SMTP setup,
daily reports and the application audit/budget, see [DAILY_AUTOMATION.md](DAILY_AUTOMATION.md).

The current qualification policy is at least 25% CV match within the last 3 days;
see [DAILY_AUTOMATION.md](DAILY_AUTOMATION.md) for daily submission counts. For
supervised application handoff and manual confirmation recording, see
[PHASE4A.md](PHASE4A.md) (its original policy values are historical).

For dashboard discovery history, the managed background worker, and rejected-
vacancy audit, see [DISCOVERY_HISTORY.md](DISCOVERY_HISTORY.md).

For the authenticated Phase 3 dashboard, grounded application drafts, explicit
human approval, and its additive migration, see [PHASE3.md](PHASE3.md).

For Phase 2C combined discovery, eleven verified employer boards, Gmail alerts, mapped public
APIs, and safe manual imports, see [PHASE2C.md](PHASE2C.md).

For Phase 2 live discovery, configuration, and isolated tests, see [DISCOVERY.md](DISCOVERY.md).

For Phase 2B authorized job-alert email imports, Windows credential setup, review
handling, migrations, and daily scheduling, see [EMAIL_ALERTS.md](EMAIL_ALERTS.md).

Import a CSV with headers: `source,source_id,company,title,location,url,posted_at,min_experience,max_experience,description`. `posted_at` must be a verified ISO-8601 timestamp with offset (e.g. `2026-11-09T08:00:00+04:00`). Do not fabricate posting dates.

```powershell
python manage.py import_jobs jobs.csv --profile-id 1 --authorization-reference "Authorized export" --dry-run
python manage.py run_daily_jobs --profile-id 1 --limit 20
```

The command only **prepares** applications, never sends them. It reserves up to 20 attempts/day, and duplicates are blocked by a database constraint. The scorer is explainable weighted keyword overlap, **not** a validated AI match probability. Experience requirements must be verified. Add cross-platform canonical URL / employer-job-ID deduplication before production submission.

## Production roadmap

1. Authorized discovery adapters (official feeds/APIs, employer integrations, or manual exports).
2. Source-aware URL normalization and deduplication across sources.
3. Cover letter generator with grounding checks and human review for unknown answers.
4. Site-specific submission adapters behind explicit allowlists, written authorization checks, and manual login/verification gates.
5. Confirmation evidence capture, idempotency keys, per-site limits, and crash recovery.
6. Daily scheduling through Windows Task Scheduler after testing. Windows PC must be awake.
7. Harden local admin (secure secrets, private CV/evidence storage, audit logging, backup).

## Employment policy

UAE employment visa, 60-day notice, not immediately available, employer sponsorship/permit transfer generally required. Unknown unrestricted authorization questions require human clarification.
