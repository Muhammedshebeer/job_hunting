# UAE Job Agent — safe local Django starter

This is a **runnable starter**, not a fully connected auto-apply service. It supports CSV imports from authorized sources, CV parsing, deterministic skill scoring, seven-day filtering, a 20-attempt daily reservation cap, duplicate protection, Django admin review, and confirmation records. It **does not** scrape job boards or submit to external websites. Implement source-specific adapters only where authorized.

## Windows PowerShell setup

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

Import a CSV with headers: `source,source_id,company,title,location,url,posted_at,min_experience,max_experience,description`. `posted_at` must be a verified ISO-8601 timestamp with offset (e.g. `2026-11-09T08:00:00+04:00`). Do not fabricate posting dates.

```powershell
python manage.py import_jobs jobs.csv
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
